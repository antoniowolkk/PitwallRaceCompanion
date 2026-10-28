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
from .track import TrackMap
from .voice import VOICES, Voice, model_files
from . import personal, style, wording
from .events import spoken_turn
from .track import Turn

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
        "tyre_temp_c_wet": t["tyres_wet"]["temp_c"],
        "tyre_pressure_psi_wet": t["tyres_wet"]["pressure_psi"],
        "fuel_warn_laps": t["fuel"]["warn_laps"],
        "fuel_critical_laps": t["fuel"]["critical_laps"],
    }


def warm_lines(settings: Settings, variants: dict[str, list[str]] | None = None) -> list[str]:
    """Lines we can predict, rendered up front so they play at once: every fixed line and
    its AI variants, urgent ones first, the fuel and pit-closing lines for the values they
    can take, and the radio check."""
    calls = load_calls()
    variants = variants or {}
    warn_min = max(1, round(settings.thresholds["pit"]["closing_warn_s"] / 60))
    fill = {
        "fuel_warn": [{"laps": words(n)} for n in range(1, int(settings.thresholds["fuel"]["warn_laps"]) + 1)],
        "pit_window_closing": [{"minutes": "a minute"}, *({"minutes": f"{words(m)} minutes"} for m in range(2, warn_min + 1))],
    }
    warm: list[str] = []
    for kind, line in sorted(calls.items(), key=lambda item: item[1]["priority"]):
        for text in [line["text"], *variants.get(kind, [])]:
            if "{" not in text:
                warm.append(text)
            else:
                warm += [text.format(**values) for values in fill.get(kind, [])]
    return [*warm, RADIO_CHECK]


def settings_message(prefs: Prefs, variants: dict[str, list[str]] | None = None) -> dict[str, Any]:
    available = {
        engine: {choice: all(p.exists() for p in model_files(engine, choice)) for choice in VOICES[engine]}
        for engine in VOICES
    }
    count = sum(len(v) for v in (variants or {}).values())
    return {"type": "settings", **asdict(prefs), "choices": CHOICES, "downloaded": available, "ai_lines": count}


class TipsDesk:
    """Personal turn tips: offer after a session, write them with the local AI only on a yes."""

    def __init__(self, settings: Settings, hub: Hub, poller: Poller, post: wording.Post | None = None):
        self.root = settings.coach_dir
        self._style = settings.thresholds["style"]
        self._hub = hub
        self._poller = poller
        self._post = post or wording.http_post
        self.offer = style.offer(style.latest_offer(self.root)) if self.root else None
        self.running = False
        self.progress = ""
        self.error = ""
        self.done = ""

    def message(self) -> dict[str, Any]:
        return {"type": "coach", "offer": self.offer, "running": self.running, "progress": self.progress,
                "error": self.error, "done": self.done}

    async def _send(self) -> None:
        await self._hub.broadcast(self.message())

    async def session_end(self, ended: dict[str, Any]) -> None:
        if not self.root:
            return
        data = await asyncio.to_thread(style.save_session, self.root, ended["passes"], ended["track"], ended["car"], self._style)
        if data and data["patterns"]:
            self.offer, self.error, self.done = style.offer(data), "", ""
            await self._send()

    async def dismiss(self, sid: str) -> None:
        if self.root:
            style.update_session(self.root, sid, dismissed=True)
        if self.offer and self.offer["session"] == sid:
            self.offer = None
        await self._send()

    async def generate(self, sid: str) -> None:
        """Runs in the background; progress goes out as "coach" messages."""
        if self.running:
            raise HTTPException(status_code=409, detail="already writing tips")
        session = style.load_session(self.root, sid) if self.root else None
        if not session or not session.get("patterns"):
            raise HTTPException(status_code=404, detail="no such session with habits")
        self.running, self.error, self.done, self.progress = True, "", "", "Starting the local AI"
        await self._send()
        loop = asyncio.get_running_loop()

        def report(text: str) -> None:
            self.progress = text
            asyncio.run_coroutine_threadsafe(self._send(), loop)

        def work() -> int:
            models = wording.ollama_models()
            if models is None:
                raise RuntimeError("Ollama is not running. Start it from the Start menu, then try again.")
            model = wording.DEFAULT_MODEL
            if not any(m in (model, f"{model}:latest") for m in models):
                raise RuntimeError(f"Model {model} is not installed. Run: ollama pull {model}")
            lines = personal.generate(session, model=model, post=self._post, report=report)
            wording.unload(model, self._post)
            personal.save(self.root, session, lines, model)
            style.update_session(self.root, sid, tips=lines)
            return sum(len(h) for h in lines.values())

        try:
            count = await asyncio.to_thread(work)
            names = {t["turn"]: t["name"] for t in session["patterns"]}
            engine, voice = self._poller.engine, self._poller.voice
            if engine:
                engine.reload_personal(engine._personal_track)
            for turn, habits in (style.load_session(self.root, sid) or {}).get("tips", {}).items():
                for line in habits.values():
                    if voice and voice.active:
                        voice.prepare(f"Coming up, {spoken_turn(Turn(turn, names.get(turn, ''), 0, 0))}. {line}")
            self.offer = None
            self.done = f"{count} personal tips ready for {session['track']} ({session['condition']})."
        except Exception as e:  # shown on the dashboard; the session stays offered
            log.exception("writing personal tips failed")
            self.error = str(e) if isinstance(e, RuntimeError) else f"Writing tips failed: {e}"
        finally:
            self.running, self.progress = False, ""
            await self._send()


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
    variants = settings.variants
    reason = ""
    if source is None:
        try:
            source = make_source(settings)
        except SourceUnavailable as e:
            reason = str(e)
            log.warning("telemetry source unavailable: %s", e)
    calls = settings.thresholds["calls"]
    track_map = TrackMap(settings.thresholds, root=settings.tracks_dir)
    engine = EventEngine(
        settings.thresholds, variants=None if settings.prefs.lite else variants, track_map=track_map,
        personal_root=settings.coach_dir,
    )
    engine.tips = settings.prefs.tips
    poller = Poller(
        source, hub.broadcast, hz=settings.poll_hz, unavailable_reason=reason,
        engine=engine,
        radio=Radio(spacing_s=calls["spacing_s"], expire_s=calls["expire_s"]),
        voice=voice,
        track_map=track_map,
    )
    poller.max_priority = CALL_PRIORITY[settings.prefs.calls]
    desk = TipsDesk(settings, hub, poller)
    poller.set_session_end(desk.session_end)

    def ai_status(prefs: Prefs) -> str:
        return "lite" if prefs.lite else "ready" if variants else "missing"

    poller.ai_status = ai_status(settings.prefs)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        voice.start(warm_lines(settings, None if settings.prefs.lite else variants))
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
    app.state.desk = desk

    @app.get("/api/coach")
    async def coach() -> dict[str, Any]:
        return desk.message()

    @app.post("/api/coach/generate")
    async def coach_generate(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        sid = str(body.get("session", ""))
        if desk.running:
            raise HTTPException(status_code=409, detail="already writing tips")
        if not desk.root or not style.load_session(desk.root, sid):
            raise HTTPException(status_code=404, detail="no such session")
        asyncio.create_task(desk.generate(sid))
        return {"started": sid}

    @app.post("/api/coach/dismiss")
    async def coach_dismiss(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        await desk.dismiss(str(body.get("session", "")))
        return desk.message()

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return poller.status()

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return settings_message(settings.prefs, variants)

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
            fresh.start(warm_lines(settings, None if new.lite else variants))
            poller.set_voice(fresh)
            if previous:
                await asyncio.to_thread(previous.close)  # may wait for a line to finish
        elif poller.voice:
            poller.voice.volume = new.volume
        poller.max_priority = CALL_PRIORITY[new.calls]
        if poller.engine:
            poller.engine.tips = new.tips
        if new.lite != old.lite:
            if poller.engine:
                poller.engine.variants = {} if new.lite else variants
            poller.ai_status = ai_status(new)
            await hub.broadcast(poller.status())
        if settings.prefs_path:
            new.save(settings.prefs_path)
        msg = settings_message(new, variants)
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
        if poller.track_map and poller.track_map.track:
            await websocket.send_json(poller.track_map.message())
        await websocket.send_json({"type": "radio_log", "calls": list(poller.radio_log)})
        await websocket.send_json(settings_message(settings.prefs, variants))
        await websocket.send_json(desk.message())
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
