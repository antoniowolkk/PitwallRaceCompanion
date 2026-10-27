"""Event engine: plain code rules turn player-car snapshots into radio calls.

Code decides when to speak; the line comes from calls.toml (phase 3 lets a local
model reword non-urgent ones). The Radio queue decides what goes out next.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Snapshot

DEFAULT_CALLS = Path(__file__).with_name("calls.toml")
TYRES = ("Front left", "Front right", "Rear left", "Rear right")


def load_calls(path: Path | None = None) -> dict[str, dict[str, Any]]:
    with open(path or DEFAULT_CALLS, "rb") as f:
        return tomllib.load(f)


def fmt_lap(ms: int) -> str:
    return f"{ms // 60000}:{ms % 60000 / 1000:06.3f}"


@dataclass(slots=True)
class Call:
    kind: str  # key in calls.toml
    priority: int  # 1 urgent, 2 normal, 3 info
    text: str
    at: float  # engine clock, seconds
    lap: int = 0  # lap number shown to the driver (completed + 1)

    def to_wire(self) -> dict[str, Any]:
        return {"kind": self.kind, "priority": self.priority, "text": self.text, "lap": self.lap}


class EventEngine:
    def __init__(self, thresholds: dict[str, Any], calls: dict[str, dict[str, Any]] | None = None):
        self._lines = calls or load_calls()
        tyres, fuel, c = thresholds["tyres"], thresholds["fuel"], thresholds["calls"]
        self._temp_hot = tyres["temp_c"][1] + 5.0  # same line the dashboard calls "Hot"
        self._psi_high = tyres["pressure_psi"][1] + 0.5
        self._fuel_warn = fuel["warn_laps"]
        self._fuel_crit = fuel["critical_laps"]
        self._sustain = c["tyre_sustain_s"]
        self._cooldown = {
            "tyre_hot": c["tyre_cooldown_s"],
            "tyre_pressure_high": c["tyre_cooldown_s"],
            "fuel_critical": c["fuel_cooldown_s"],
        }
        self._flag_cooldown = c["flag_cooldown_s"]
        self.reset()

    def reset(self) -> None:
        self._prev: Snapshot | None = None
        self._last_call: dict[str, float] = {}
        self._fuel_level = 0  # 0 ok, 1 warn, 2 critical
        self._hot_since: dict[str, float] = {}

    def _call(self, kind: str, now: float, snap: Snapshot, **fields: Any) -> Call | None:
        line = self._lines[kind]
        cooldown = self._flag_cooldown if kind.startswith("flag_") else self._cooldown.get(kind, 0.0)
        last = self._last_call.get(kind)
        if last is not None and now - last < cooldown:
            return None
        self._last_call[kind] = now
        return Call(kind, int(line["priority"]), line["text"].format(**fields), now, snap.laps + 1)

    def update(self, snap: Snapshot, now: float) -> list[Call]:
        """Feed one snapshot; returns the calls it triggers (often none)."""
        prev, self._prev = self._prev, snap
        if snap.status != "live":
            return []  # paused or replay: nobody is driving
        calls = [
            *self._flags(prev, snap, now),
            *self._fuel(prev, snap, now),
            *self._tyres(snap, now),
            *self._best_lap(prev, snap, now),
        ]
        return [c for c in calls if c is not None]

    def _flags(self, prev: Snapshot | None, snap: Snapshot, now: float) -> list[Call | None]:
        if snap.flag == "none" or (prev is not None and prev.flag == snap.flag):
            return []
        kind = f"flag_{snap.flag}"
        return [self._call(kind, now, snap)] if kind in self._lines else []

    def _fuel(self, prev: Snapshot | None, snap: Snapshot, now: float) -> list[Call | None]:
        if snap.fuel_per_lap_l <= 0:
            return []  # ACC has no estimate yet
        laps = snap.fuel_laps_left
        level = 2 if laps < self._fuel_crit else 1 if laps < self._fuel_warn else 0
        worse, self._fuel_level = level > self._fuel_level, level
        if level == 2 and (worse or (prev is not None and snap.laps > prev.laps)):
            return [self._call("fuel_critical", now, snap)]  # again every lap until the stop
        if level == 1 and worse:
            return [self._call("fuel_warn", now, snap, laps=f"{laps:.0f}")]
        return []

    def _tyres(self, snap: Snapshot, now: float) -> list[Call | None]:
        hottest = max(range(4), key=lambda i: snap.tyre_temp_c[i])
        highest = max(range(4), key=lambda i: snap.tyre_pressure_psi[i])
        checks = (
            ("tyre_hot", snap.tyre_temp_c[hottest] > self._temp_hot, hottest),
            ("tyre_pressure_high", snap.tyre_pressure_psi[highest] > self._psi_high, highest),
        )
        calls = []
        for kind, over, i in checks:
            if not over:
                self._hot_since.pop(kind, None)
                continue
            since = self._hot_since.setdefault(kind, now)
            if now - since >= self._sustain:
                calls.append(self._call(
                    kind, now, snap, tyre=TYRES[i],
                    temp=f"{snap.tyre_temp_c[i]:.0f}", psi=f"{snap.tyre_pressure_psi[i]:.1f}",
                ))
        return calls

    def _best_lap(self, prev: Snapshot | None, snap: Snapshot, now: float) -> list[Call | None]:
        if prev is None or snap.laps < 2 or snap.best_ms is None or snap.laps == prev.laps:
            return []
        if prev.best_ms is not None and snap.best_ms < prev.best_ms:
            return [self._call("best_lap", now, snap, time=fmt_lap(snap.best_ms))]
        return []


@dataclass
class Radio:
    """One call at a time: urgent first, a gap between calls, stale ones dropped."""

    spacing_s: float = 4.0
    expire_s: float = 15.0
    _queue: list[Call] = field(default_factory=list)
    _last_out: float | None = None

    def push(self, calls: list[Call]) -> None:
        for call in calls:
            # A newer call of the same kind replaces the queued one.
            self._queue = [c for c in self._queue if c.kind != call.kind]
            self._queue.append(call)

    def next(self, now: float) -> Call | None:
        self._queue = [c for c in self._queue if now - c.at <= self.expire_s]
        if not self._queue:
            return None
        call = min(self._queue, key=lambda c: (c.priority, c.at))
        busy = self._last_out is not None and now - self._last_out < self.spacing_s
        if busy and call.priority > 1:
            return None
        self._queue.remove(call)
        self._last_out = now
        return call

    def clear(self) -> None:
        self._queue.clear()
