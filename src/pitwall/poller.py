"""Polls the telemetry source, tracks the ACC connection, pushes only changes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from .events import EventEngine, Radio
from .models import diff
from .telemetry import TelemetrySource
from .track import TrackMap
from .voice import Voice

log = logging.getLogger(__name__)

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]


class Poller:
    def __init__(
        self,
        source: TelemetrySource | None,
        broadcast: Broadcast,
        hz: float = 10.0,
        unavailable_reason: str = "",
        engine: EventEngine | None = None,
        radio: Radio | None = None,
        voice: Voice | None = None,
        track_map: TrackMap | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.source = source
        self._broadcast = broadcast
        self._period = 1.0 / hz
        self._unavailable_reason = unavailable_reason
        self._engine = engine
        self._radio = radio or Radio()
        self._voice = voice
        self._voice_status = self._voice.status if self._voice else "off"
        self._clock = clock
        self.track_map = track_map
        self.connected = False
        self.last: dict[str, Any] | None = None
        self.radio_log: deque[dict[str, Any]] = deque(maxlen=20)  # newest last
        self.max_priority = 3  # Settings "radio calls": 1 urgent only, 2 important, 3 all
        self.ai_status = "off"  # ready (AI-written lines loaded) | missing (not generated) | lite | off
        self._read_error_logged = False

    def status(self) -> dict[str, Any]:
        if self.source is None:
            acc = "unavailable"
        else:
            acc = "connected" if self.connected else "waiting"
        return {
            "type": "status",
            "acc": acc,
            "source": self.source.name if self.source else None,
            "reason": self._unavailable_reason,
            "ai": self.ai_status,
            "voice": self._voice_status,
        }

    async def tick(self) -> None:
        if self._voice and self._voice.status != self._voice_status:
            self._voice_status = self._voice.status  # loading -> ready happens in the voice thread
            await self._broadcast(self.status())
        if self.source is None:
            return
        try:
            snap = self.source.read()
            self._read_error_logged = False
        except Exception:
            if not self._read_error_logged:
                log.exception("telemetry read failed")
                self._read_error_logged = True
            snap = None

        if (snap is not None) != self.connected:
            self.connected = snap is not None
            self.last = None
            if self._engine:
                self._engine.reset()
            self._radio.clear()
            log.info("ACC %s", "connected" if self.connected else "waiting")
            await self._broadcast(self.status())

        if snap is None:
            return
        wire = snap.to_wire()
        changes = diff(self.last, wire)
        self.last = wire
        if changes:
            await self._broadcast({"type": "telemetry", "data": changes})
        if self.track_map:
            track = self.track_map.update(snap)
            if track:
                await self._broadcast(track)

        if self._engine:
            now = self._clock()
            calls = self._engine.update(snap, now)  # always run, so cooldowns stay right
            if self._engine.prepare:
                if self._voice and self._voice.active:
                    for text in self._engine.prepare:
                        self._voice.prepare(text)
                self._engine.prepare.clear()
            self._radio.push([c for c in calls if c.priority <= self.max_priority])
            call = self._radio.next(now)
            if call:
                log.info("radio: %s", call.text)
                msg = {"type": "radio", **call.to_wire()}
                if self._voice and self._voice.active:
                    # Subtitle appears when the voice starts, which can be seconds later
                    # if the line still has to be rendered.
                    loop = asyncio.get_running_loop()
                    self._voice.say(
                        call.text,
                        on_start=lambda: asyncio.run_coroutine_threadsafe(self._send_radio(msg), loop),
                        priority=call.priority,
                        max_wait=call.expire_s,
                    )
                else:
                    await self._send_radio(msg)

    @property
    def engine(self) -> EventEngine | None:
        return self._engine

    @property
    def voice(self) -> Voice | None:
        return self._voice

    def set_voice(self, voice: Voice | None) -> None:
        """Swap the voice live; the new status goes out on the next tick."""
        self._voice = voice

    async def _send_radio(self, msg: dict[str, Any]) -> None:
        msg["time"] = time.strftime("%H:%M:%S")
        self.radio_log.append(msg)
        await self._broadcast(msg)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while True:
            await self.tick()
            next_at += self._period
            delay = next_at - loop.time()
            if delay < 0:  # fell behind; do not burst to catch up
                next_at = loop.time()
                delay = 0
            await asyncio.sleep(delay)
