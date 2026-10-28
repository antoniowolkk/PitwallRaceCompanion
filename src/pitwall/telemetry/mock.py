"""Synthetic player-car telemetry so the app runs without ACC.

A fictional ~4.6 km circuit: speed comes from corner minimum speeds with
constant braking and acceleration, which gives believable laps around 1:40.
"""

from __future__ import annotations

import bisect
import math
import random
import time
from collections.abc import Callable

from ..models import Snapshot

LAP_M = 4650.0
V_MAX = 272 / 3.6
BRAKE = 14.0  # m/s^2
ACCEL = 3.0  # m/s^2, averaged over the rev range
# (position as lap fraction, corner minimum speed km/h)
CORNERS = [
    (0.07, 115), (0.13, 165), (0.22, 210), (0.30, 95), (0.38, 140),
    (0.47, 80), (0.55, 175), (0.66, 120), (0.78, 90), (0.88, 150), (0.95, 105),
]
GEARS_KMH = [0, 85, 120, 155, 190, 228, 999]  # 6 gears, upshift points
TYRE_BIAS = (4.0, 1.5, -2.0, -1.0)  # FL runs hottest
COLD_PSI = (23.2, 23.4, 23.0, 23.1)
FUEL_PER_LAP = 2.9
SUBSTEP = 0.05


def _speed_at(x: float) -> float:
    v = V_MAX
    for frac, vmin_kmh in CORNERS:
        c = frac * LAP_M
        vmin = vmin_kmh / 3.6
        after = (x - c) % LAP_M
        before = (c - x) % LAP_M
        v = min(v, math.sqrt(vmin**2 + 2 * ACCEL * after), math.sqrt(vmin**2 + 2 * BRAKE * before))
    return v


class MockSource:
    name = "mock"

    def __init__(
        self,
        seed: int = 1,
        speed: float = 1.0,
        start_fuel_l: float = 30.0,
        max_fuel_l: float = 120.0,
        clock: Callable[[], float] | None = time.monotonic,
    ):
        self._rng = random.Random(seed)
        self._speed = speed
        self._clock = clock
        self._last_t = clock() if clock else 0.0
        # Precomputed speed profile, one sample per metre.
        self._profile = [_speed_at(float(x)) for x in range(int(LAP_M) + 1)]
        self.pos_m = 0.0
        self.lap_time_s = 0.0
        self.laps = 0
        self.last_ms: int | None = None
        self.best_ms: int | None = None
        self.lap_valid = True
        self.fuel_l = start_fuel_l
        self.max_fuel_l = max_fuel_l
        self.tyre_temp = [55.0, 55.0, 55.0, 55.0]
        self.flag = "none"
        self._pace = self._new_pace()
        self._yellow_at = self._rng.choice([0.3, 0.6])

    def _new_pace(self) -> float:
        return 1.0 + self._rng.gauss(0, 0.004)

    def _v(self) -> float:
        return self._profile[int(self.pos_m) % len(self._profile)] / self._pace

    def step(self, dt: float) -> None:
        while dt > 0:
            h = min(dt, SUBSTEP)
            dt -= h
            self._substep(h)

    def _substep(self, h: float) -> None:
        v = self._v()
        self.pos_m += v * h
        self.lap_time_s += h
        self.fuel_l = max(0.0, self.fuel_l - FUEL_PER_LAP * v * h / LAP_M)

        # Tyres heat with cornering load (slow sections) and settle toward ~84 C.
        load = 1.0 - v / V_MAX
        for i, bias in enumerate(TYRE_BIAS):
            target = 78.0 + 14.0 * load + bias
            self.tyre_temp[i] += (target - self.tyre_temp[i]) * min(1.0, h / 25.0)

        frac = self.pos_m / LAP_M
        yellow = self.laps % 4 == 3 and abs(frac - self._yellow_at) < 0.03
        self.flag = "yellow" if yellow else "none"
        if self.pos_m >= LAP_M:
            self._finish_lap()

    def _finish_lap(self) -> None:
        self.pos_m -= LAP_M
        ms = int(self.lap_time_s * 1000)
        self.last_ms = ms
        if self.lap_valid and (self.best_ms is None or ms < self.best_ms):
            self.best_ms = ms
        self.laps += 1
        self.lap_time_s = 0.0
        self.lap_valid = self._rng.random() > 0.1
        self._pace = self._new_pace()
        if self.fuel_l < FUEL_PER_LAP:
            self.fuel_l = 60.0  # pretend pit stop so the replay never runs dry

    def _xy(self, frac: float) -> tuple[float, float]:
        # A lumpy loop for the map: three bulges, clockwise, about the lap length round.
        a = 2 * math.pi * frac
        r = 700 * (1 + 0.18 * math.sin(3 * a))
        return r * math.cos(a), r * math.sin(a)

    def snapshot(self) -> Snapshot:
        kmh = self._v() * 3.6
        frac = (self.pos_m / LAP_M) % 1
        x, y = self._xy(frac)
        gear = bisect.bisect_right(GEARS_KMH, kmh)
        lo, hi = GEARS_KMH[gear - 1], min(GEARS_KMH[gear], 280)
        rpm = 4200 + (kmh - lo) / max(hi - lo, 1) * 4300
        temps = tuple(t + self._rng.uniform(-0.3, 0.3) for t in self.tyre_temp)
        psi = tuple(c + (t - 20.0) * 0.055 for c, t in zip(COLD_PSI, temps))
        return Snapshot(
            status="live",
            session="practice",
            car="ferrari_296_gt3",
            track="mock_circuit",
            speed_kmh=kmh,
            gear=str(gear),
            rpm=int(min(rpm, 8500)),
            max_rpm=8500,
            fuel_l=self.fuel_l,
            max_fuel_l=self.max_fuel_l,
            fuel_per_lap_l=FUEL_PER_LAP,
            fuel_laps_left=self.fuel_l / FUEL_PER_LAP,
            tyre_temp_c=temps,  # type: ignore[arg-type]
            tyre_pressure_psi=psi,  # type: ignore[arg-type]
            laps=self.laps,
            current_ms=int(self.lap_time_s * 1000),
            last_ms=self.last_ms,
            best_ms=self.best_ms,
            lap_valid=self.lap_valid,
            flag=self.flag,
            in_pit_lane=False,
            lap_pos=frac,
            x=x,
            y=y,
        )

    def read(self) -> Snapshot | None:
        if self._clock is not None:
            now = self._clock()
            self.step((now - self._last_t) * self._speed)
            self._last_t = now
        return self.snapshot()

    def close(self) -> None:
        pass
