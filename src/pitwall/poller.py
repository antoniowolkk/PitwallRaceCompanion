"""Polls the telemetry source, tracks the ACC connection, pushes only changes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .models import diff
from .telemetry import TelemetrySource

log = logging.getLogger(__name__)

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]


class Poller:
    def __init__(
        self,
        source: TelemetrySource | None,
        broadcast: Broadcast,
        hz: float = 10.0,
        unavailable_reason: str = "",
    ):
        self.source = source
        self._broadcast = broadcast
        self._period = 1.0 / hz
        self._unavailable_reason = unavailable_reason
        self.connected = False
        self.last: dict[str, Any] | None = None
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
            # Filled in by later phases.
            "ai": "off",
            "voice": "off",
        }

    async def tick(self) -> None:
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
            log.info("ACC %s", "connected" if self.connected else "waiting")
            await self._broadcast(self.status())

        if snap is None:
            return
        wire = snap.to_wire()
        changes = diff(self.last, wire)
        self.last = wire
        if changes:
            await self._broadcast({"type": "telemetry", "data": changes})

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
