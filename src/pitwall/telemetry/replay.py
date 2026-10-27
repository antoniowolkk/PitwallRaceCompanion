"""Record real ACC sessions to JSON Lines and replay them in mock mode."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from ..models import Snapshot


class Recorder:
    """Writes one line per snapshot: {"t": seconds since start, ...wire fields}."""

    def __init__(self, out: IO[str], clock: Callable[[], float] = time.monotonic):
        self._out = out
        self._clock = clock
        self._t0: float | None = None

    def write(self, snap: Snapshot) -> None:
        now = self._clock()
        if self._t0 is None:
            self._t0 = now
        line = {"t": round(now - self._t0, 3), **snap.to_wire()}
        self._out.write(json.dumps(line, separators=(",", ":")) + "\n")


class ReplaySource:
    """Plays a recording in real time and loops at the end."""

    name = "replay"

    def __init__(self, path: Path, speed: float = 1.0, clock: Callable[[], float] = time.monotonic):
        with open(path, encoding="utf-8") as f:
            self._frames = [json.loads(line) for line in f if line.strip()]
        if not self._frames:
            raise ValueError(f"empty recording: {path}")
        self._duration = self._frames[-1]["t"] or 1.0
        self._speed = speed
        self._clock = clock
        self._t0 = clock()
        self._i = 0

    def read(self) -> Snapshot | None:
        t = ((self._clock() - self._t0) * self._speed) % self._duration
        if t < self._frames[self._i]["t"]:
            self._i = 0  # looped
        while self._i + 1 < len(self._frames) and self._frames[self._i + 1]["t"] <= t:
            self._i += 1
        return Snapshot.from_wire(self._frames[self._i])

    def close(self) -> None:
        pass
