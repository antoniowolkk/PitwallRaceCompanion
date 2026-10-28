"""Player-car telemetry snapshot shared by every telemetry source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ACC reports "no time yet" as INT32_MAX.
NO_TIME = 2147483647

Wheels = tuple[float, float, float, float]  # FL, FR, RL, RR


def gear_label(acc_gear: int) -> str:
    """ACC physics gear: 0 = R, 1 = N, 2 = 1st, ..."""
    if acc_gear <= 0:
        return "R"
    if acc_gear == 1:
        return "N"
    return str(acc_gear - 1)


def lap_ms(value: int | None) -> int | None:
    if value is None or value <= 0 or value >= NO_TIME:
        return None
    return int(value)


@dataclass(slots=True)
class Snapshot:
    status: str = "live"  # live | pause | replay
    session: str = "unknown"
    car: str = ""
    track: str = ""
    speed_kmh: float = 0.0
    gear: str = "N"
    rpm: int = 0
    max_rpm: int = 0
    fuel_l: float = 0.0
    max_fuel_l: float = 0.0
    fuel_per_lap_l: float = 0.0
    fuel_laps_left: float = 0.0
    tyre_temp_c: Wheels = (0.0, 0.0, 0.0, 0.0)
    tyre_pressure_psi: Wheels = (0.0, 0.0, 0.0, 0.0)
    laps: int = 0
    current_ms: int | None = None
    last_ms: int | None = None
    best_ms: int | None = None
    lap_valid: bool = True
    flag: str = "none"
    in_pit_lane: bool = False
    # Pit window, raw as ACC sends them: units still to confirm from a real race with a
    # mandatory stop (acc_shared_memory_rs docs say seconds; other tools use milliseconds).
    in_pit: bool = False
    session_time_left: float = 0.0
    pit_window_start: int = 0
    pit_window_end: int = 0
    mandatory_pit_done: bool = False
    missing_mandatory_pits: int = 0
    # Where the car is and what the driver does, for the track map and per-turn coaching.
    # Player car only: ACC sends 60 cars' coordinates, we keep our own entry.
    lap_pos: float = 0.0  # 0..1 around the lap (normalized_car_position)
    x: float = 0.0  # world metres, ground plane (ACC x and z)
    y: float = 0.0
    throttle: float = 0.0
    brake: float = 0.0
    steer: float = 0.0
    wheel_slip: Wheels = (0.0, 0.0, 0.0, 0.0)
    abs: float = 0.0  # ABS working right now, 0..1
    tc: float = 0.0  # traction control working right now, 0..1
    # Car settings and conditions the per-turn tips are written for.
    brake_bias: float = 0.0  # front share as ACC reports it (raw; the in-game number can differ per car)
    abs_level: int = 0
    tc_level: int = 0
    engine_map: int = 0
    wet_tyres: bool = False
    grip: str = ""  # green | fast | optimum | greasy | damp | wet | flooded
    rain: str = ""  # none | drizzle | light | medium | heavy | storm
    air_c: float = 0.0
    road_c: float = 0.0

    def to_wire(self) -> dict[str, Any]:
        """Rounded values, so the diff only sends changes a driver can see."""
        return {
            "status": self.status,
            "session": self.session,
            "car": self.car,
            "track": self.track,
            "speed_kmh": round(self.speed_kmh),
            "gear": self.gear,
            "rpm": int(round(self.rpm, -1)),
            "max_rpm": self.max_rpm,
            "fuel_l": round(self.fuel_l, 1),
            "max_fuel_l": round(self.max_fuel_l, 1),
            "fuel_per_lap_l": round(self.fuel_per_lap_l, 2),
            "fuel_laps_left": round(self.fuel_laps_left, 1),
            "tyre_temp_c": [round(t, 1) for t in self.tyre_temp_c],
            "tyre_pressure_psi": [round(p, 1) for p in self.tyre_pressure_psi],
            "laps": self.laps,
            "current_ms": self.current_ms,
            "last_ms": self.last_ms,
            "best_ms": self.best_ms,
            "lap_valid": self.lap_valid,
            "flag": self.flag,
            "in_pit_lane": self.in_pit_lane,
            "in_pit": self.in_pit,
            "session_time_left": round(self.session_time_left),
            "pit_window_start": self.pit_window_start,
            "pit_window_end": self.pit_window_end,
            "mandatory_pit_done": self.mandatory_pit_done,
            "missing_mandatory_pits": self.missing_mandatory_pits,
            "lap_pos": round(self.lap_pos, 4),
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "throttle": round(self.throttle, 2),
            "brake": round(self.brake, 2),
            "steer": round(self.steer, 2),
            "wheel_slip": [round(s, 2) for s in self.wheel_slip],
            "abs": round(self.abs, 2),
            "tc": round(self.tc, 2),
            "brake_bias": round(self.brake_bias, 3),
            "abs_level": self.abs_level,
            "tc_level": self.tc_level,
            "engine_map": self.engine_map,
            "wet_tyres": self.wet_tyres,
            "grip": self.grip,
            "rain": self.rain,
            "air_c": round(self.air_c),
            "road_c": round(self.road_c),
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Snapshot:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        for key in ("tyre_temp_c", "tyre_pressure_psi", "wheel_slip"):
            if key in known:
                known[key] = tuple(known[key])
        return cls(**known)


def diff(prev: dict[str, Any] | None, cur: dict[str, Any]) -> dict[str, Any]:
    if prev is None:
        return dict(cur)
    return {k: v for k, v in cur.items() if prev.get(k) != v}
