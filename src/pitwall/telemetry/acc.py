"""ACC shared memory reader, player car only. Windows only.

Field names follow pyaccsharedmemory 1.0.0. Still to verify in a real session:
whether graphics.packed_id keeps ticking while paused, and that the map
really goes stale (not back to ACC_OFF) when the game closes.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

from ..models import Snapshot, gear_label, lap_ms
from .base import SourceUnavailable

_STATUS = {"ACC_LIVE": "live", "ACC_PAUSE": "pause", "ACC_REPLAY": "replay"}

_SESSION = {
    "ACC_PRACTICE": "practice",
    "ACC_QUALIFY": "qualify",
    "ACC_RACE": "race",
    "ACC_HOTLAP": "hotlap",
    "ACC_TIME_ATTACK": "time_attack",
    "ACC_HOTSTINT": "hotstint",
    "ACC_HOTLAPSUPERPOLE": "superpole",
}

_FLAG = {
    "ACC_NO_FLAG": "none",
    "ACC_BLUE_FLAG": "blue",
    "ACC_YELLOW_FLAG": "yellow",
    "ACC_BLACK_FLAG": "black",
    "ACC_WHITE_FLAG": "white",
    "ACC_CHECKERED_FLAG": "chequered",
    "ACC_PENALTY_FLAG": "penalty",
    "ACC_GREEN_FLAG": "green",
    "ACC_ORANGE_FLAG": "orange",
}


def _wheels(w: Any) -> tuple[float, float, float, float]:
    return (w.front_left, w.front_right, w.rear_left, w.rear_right)


def _text(value: str) -> str:
    """Static strings come NUL-padded to their fixed buffer size."""
    return value.split("\x00", 1)[0].strip()


def from_acc(physics: Any, graphics: Any, static: Any) -> Snapshot:
    """Map pyaccsharedmemory Physics/Graphics/Static maps to a Snapshot."""
    fuel_per_lap = float(graphics.fuel_per_lap)
    return Snapshot(
        status=_STATUS.get(graphics.status.name, "live"),
        session=_SESSION.get(graphics.session_type.name, "unknown"),
        car=_text(static.car_model),
        track=_text(static.track),
        speed_kmh=float(physics.speed_kmh),
        gear=gear_label(int(physics.gear)),
        rpm=int(physics.rpm),
        max_rpm=int(static.max_rpm),
        fuel_l=float(physics.fuel),
        max_fuel_l=float(static.max_fuel),
        fuel_per_lap_l=fuel_per_lap,
        fuel_laps_left=float(graphics.fuel_estimated_laps),
        tyre_temp_c=_wheels(physics.tyre_core_temp),
        tyre_pressure_psi=_wheels(physics.wheel_pressure),
        laps=int(graphics.completed_lap),
        current_ms=lap_ms(graphics.current_time),
        last_ms=lap_ms(graphics.last_time),
        best_ms=lap_ms(graphics.best_time),
        lap_valid=bool(graphics.is_valid_lap),
        flag=_FLAG.get(graphics.flag.name, "none"),
        in_pit_lane=bool(graphics.is_in_pit_lane),
    )


class AccSource:
    name = "acc"

    def __init__(self, stale_after_s: float = 5.0, clock: Callable[[], float] = time.monotonic):
        if sys.platform != "win32":
            raise SourceUnavailable("ACC shared memory is Windows only")
        try:
            import pyaccsharedmemory as acc
        except ImportError as e:  # pragma: no cover - Windows only
            raise SourceUnavailable("pyaccsharedmemory is not installed") from e
        self._acc = acc
        # Creates the named maps even before ACC runs; they read as ACC_OFF then.
        self._asm = acc.accSharedMemory()
        self._static: Any = None
        self._ids: tuple[int, int] | None = None
        self._last_change = clock()
        self._stale_after_s = stale_after_s
        self._clock = clock

    def read(self) -> Snapshot | None:  # pragma: no cover - Windows only
        acc = self._acc
        physics = acc.read_physic_map(self._asm.physicSM)
        graphics = acc.read_graphics_map(self._asm.graphicSM)
        if graphics.status == acc.ACC_STATUS.ACC_OFF:
            self._static = None
            return None

        # Our open handle keeps the last values alive after ACC exits,
        # so frozen packet ids mean the game is gone.
        ids = (physics.packed_id, graphics.packed_id)
        now = self._clock()
        if ids != self._ids:
            self._ids = ids
            self._last_change = now
        elif now - self._last_change > self._stale_after_s:
            self._static = None
            return None

        if self._static is None:
            self._static = acc.read_static_map(self._asm.staticSM)
        return from_acc(physics, graphics, self._static)

    def close(self) -> None:  # pragma: no cover - Windows only
        self._asm.close()
