from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .events import EventEngine, Radio, load_calls, words
from .poller import Poller
from .telemetry import SourceUnavailable, TelemetrySource, make_source
from .voice import Voice

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


def create_app(
    settings: Settings,
    source: TelemetrySource | None = None,
    run_poller: bool = True,
    voice: Voice | None = None,
) -> FastAPI:
    hub = Hub()
    if voice is None:
        voice = Voice(settings.voice, settings.volume, engine=settings.engine) if run_poller else Voice("off")
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

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Lines we can predict are rendered up front so they play at once: every fixed
        # line, urgent ones first, plus the low-fuel warning for the last few laps.
        lines = sorted(load_calls().values(), key=lambda line: line["priority"])
        warm = [line["text"] for line in lines if "{" not in line["text"]]
        fuel = load_calls()["fuel_warn"]["text"]
        warm += [fuel.format(laps=words(n)) for n in range(1, int(settings.thresholds["fuel"]["warn_laps"]) + 1)]
        voice.start(warm)
        task = asyncio.create_task(poller.run()) if run_poller else None
        try:
            yield
        finally:
            voice.close()
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

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json(client_config(settings))
        await websocket.send_json(poller.status())
        if poller.last is not None:
            await websocket.send_json({"type": "telemetry", "data": poller.last})
        await websocket.send_json({"type": "radio_log", "calls": list(poller.radio_log)})
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
