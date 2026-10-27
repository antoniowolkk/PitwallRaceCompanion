from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import CALL_PRIORITY, CHOICES, Prefs, Settings
from .events import EventEngine, Radio, load_calls, words
from .poller import Poller
from .telemetry import SourceUnavailable, TelemetrySource, make_source
from .voice import VOICES, Voice, model_files

RADIO_CHECK = "Radio check. Reading you loud and clear."

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).with_name("web")


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict[str, Any]) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


def client_config(settings: Settings) -> dict[str, Any]:
    t = settings.thresholds
    return {
        "type": "config",
        "tyre_temp_c": t["tyres"]["temp_c"],
        "tyre_pressure_psi": t["tyres"]["pressure_psi"],
        "fuel_warn_laps": t["fuel"]["warn_laps"],
        "fuel_critical_laps": t["fuel"]["critical_laps"],
    }


def warm_lines(settings: Settings) -> list[str]:
    """Lines we can predict, rendered up front so they play at once: every fixed line,
    urgent ones first, the low-fuel warning for the last few laps, and the radio check."""
    calls = load_calls()
    lines = sorted(calls.values(), key=lambda line: line["priority"])
    warm = [line["text"] for line in lines if "{" not in line["text"]]
    laps = range(1, int(settings.thresholds["fuel"]["warn_laps"]) + 1)
    return [*warm, *(calls["fuel_warn"]["text"].format(laps=words(n)) for n in laps), RADIO_CHECK]


def settings_message(prefs: Prefs) -> dict[str, Any]:
    available = {
        engine: {choice: all(p.exists() for p in model_files(engine, choice)) for choice in VOICES[engine]}
        for engine in VOICES
    }
    return {"type": "settings", **asdict(prefs), "choices": CHOICES, "downloaded": available}


def default_voice(prefs: Prefs) -> Voice:
    return Voice(prefs.voice, prefs.volume, engine=prefs.engine)


def create_app(
    settings: Settings,
    source: TelemetrySource | None = None,
    run_poller: bool = True,
    voice_factory: Callable[[Prefs], Voice] | None = None,
) -> FastAPI:
    hub = Hub()
    if voice_factory is None:
        voice_factory = default_voice if run_poller else (lambda prefs: Voice("off"))
    voice = voice_factory(settings.prefs)
    reason = ""
    if source is None:
        try:
            source = make_source(settings)
        except SourceUnavailable as e:
            reason = str(e)
            log.warning("telemetry source unavailable: %s", e)
    calls = settings.thresholds["calls"]
    poller = Poller(
        source, hub.broadcast, hz=settings.poll_hz, unavailable_reason=reason,
        engine=EventEngine(settings.thresholds),
        radio=Radio(spacing_s=calls["spacing_s"], expire_s=calls["expire_s"]),
        voice=voice,
    )
    poller.max_priority = CALL_PRIORITY[settings.prefs.calls]

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        voice.start(warm_lines(settings))
        task = asyncio.create_task(poller.run()) if run_poller else None
        try:
            yield
        finally:
            if poller.voice:
                poller.voice.close()
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if source:
                source.close()

    app = FastAPI(title="Pit Wall", lifespan=lifespan)
    app.state.poller = poller
    app.state.hub = hub

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return poller.status()

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return settings_message(settings.prefs)

    @app.put("/api/settings")
    async def put_settings(changes: dict[str, Any] = Body(...)) -> dict[str, Any]:
        old = settings.prefs
        try:
            new = old.updated(changes)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        settings.prefs = new
        if (new.voice, new.engine) != (old.voice, old.engine):
            previous = poller.voice
            fresh = voice_factory(new)
            fresh.start(warm_lines(settings))
            poller.set_voice(fresh)
            if previous:
                await asyncio.to_thread(previous.close)  # may wait for a line to finish
        elif poller.voice:
            poller.voice.volume = new.volume
        poller.max_priority = CALL_PRIORITY[new.calls]
        if settings.prefs_path:
            new.save(settings.prefs_path)
        msg = settings_message(new)
        await hub.broadcast(msg)
        log.info("settings changed: %s", changes)
        return msg

    @app.post("/api/voice/test")
    async def voice_test() -> dict[str, Any]:
        v = poller.voice
        if not v or not v.active:
            raise HTTPException(status_code=409, detail=f"voice is {v.status if v else 'off'}")
        v.say(RADIO_CHECK, priority=1)
        return {"voice": v.status}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json(client_config(settings))
        await websocket.send_json(poller.status())
        if poller.last is not None:
            await websocket.send_json({"type": "telemetry", "data": poller.last})
        await websocket.send_json({"type": "radio_log", "calls": list(poller.radio_log)})
        await websocket.send_json(settings_message(settings.prefs))
        hub.clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()  # client messages unused in phase 1
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
