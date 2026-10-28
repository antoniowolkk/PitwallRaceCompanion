"""Per-turn tips: spot a mistake in a turn, mention it on the way into that turn next lap.

Each pass through a turn is timed (from a little before it to a little after it) and
measured: where the braking starts, how hard, the slowest point, coasting, when full
throttle comes back, ABS, a lockup (front wheel slip under braking), wheelspin on the
way out, or the lap going invalid. A pass with a mistake, or clearly slower than the
best pass through that turn this session, is remembered. Next time round, a few
seconds before the turn, the engineer mentions it: never while in a turn, never the
same turn two laps running, at most one a lap in practice and qualifying, and in a
race only lockups and cuts, every few laps at most.

Every pass is also kept in `passes`, and every lap in `laps`, so the stint coach
(coach.py) can judge the driving over a stint and, after the session, `style.py` can
look for patterns and the local AI can write personal tips from them (only when asked).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Snapshot
from .track import TrackMap, Turn

KIND_PATTERN = {"tip_lockup": "lockup", "tip_wheelspin": "wheelspin", "tip_off": "off"}


@dataclass
class Pass:
    lap: int
    entry_kmh: float
    last_ms: int  # current_ms of the previous sample
    took_ms: int = 0  # time through the zone so far
    lock: int = 0  # samples with a front wheel locking under braking
    spin: int = 0  # samples with a rear wheel spinning under throttle
    invalid: bool = False  # the lap went invalid here (track limits, contact)
    brake_at: float | None = None  # lap fraction where braking (>= 30%) began
    peak_brake: float = 0.0
    min_kmh: float = 999.0
    min_at: float = 0.0
    full_at: float | None = None  # lap fraction where full throttle came back after the slowest point
    coast_ms: int = 0  # neither pedal, on the way in and through the turn
    abs_ms: int = 0
    tc_ms: int = 0
    corrections: int = 0  # steering reversals inside the turn (a clean corner has about one: in, then unwind)
    steer_ext: float | None = None  # furthest steering in the current direction
    steer_dir: int = 0


@dataclass
class Mistake:
    kind: str  # tip_off | tip_lockup | tip_wheelspin | tip_slow
    turn: Turn
    lap: int  # lap (completed count) the mistake happened on
    loss_s: float = 0.0
    pattern: str = ""  # what personal line fits: lockup, wheelspin, off, early_brake, low_min_speed, coasting, late_throttle
    extra: dict[str, Any] = field(default_factory=dict)


def _inside(pos: float, start: float, end: float) -> bool:
    start, end = start % 1, end % 1
    return start <= pos <= end if start <= end else pos >= start or pos <= end


def _signed(frac: float) -> float:
    """A lap-fraction difference folded into -0.5..0.5."""
    return (frac + 0.5) % 1 - 0.5


def why_slow(rec: dict[str, Any], best: dict[str, Any], c: dict[str, Any]) -> str:
    """The biggest difference between a slow pass and the best one, as a pattern name."""
    gaps = []
    if rec["brake_m"] is not None and best["brake_m"] is not None:
        gaps.append(((rec["brake_m"] - best["brake_m"]) / c["brake_m"], "early_brake"))
    gaps.append(((best["min_kmh"] - rec["min_kmh"]) / c["min_kmh"], "low_min_speed"))
    gaps.append(((rec["coast_s"] - best["coast_s"]) / c["coast_s"], "coasting"))
    if rec["full_m"] is not None and best["full_m"] is not None:
        gaps.append(((rec["full_m"] - best["full_m"]) / c["full_m"], "late_throttle"))
    size, name = max(gaps)
    return name if size >= 1 else "slow"


class TurnCoach:
    def __init__(self, thresholds: dict[str, Any], track_map: TrackMap):
        c = thresholds["tips"]
        self._map = track_map
        self._pre_m, self._post_m = c["pre_m"], c["post_m"]
        self._lead_s, self._min_lead_m, self._min_gap_m = c["lead_s"], c["min_lead_m"], c["min_gap_m"]
        self._lock_slip, self._spin_slip = c["lock_slip"], c["spin_slip"]
        self._spin_throttle, self._samples = c["spin_throttle"], c["slip_samples"]
        self._swing = c["steer_swing"]
        self._loss_s, self._per_lap = c["loss_s"], c["max_per_lap"]
        self._race_kinds, self._race_gap = set(c["race_kinds"]), c["race_gap_laps"]
        self.differs = thresholds["style"]  # how big a difference to the best pass counts
        self.finished: list[dict[str, Any]] = []  # ended sessions, for the poller to save
        self.passes: list[dict[str, Any]] = []
        self.reset()

    def finish(self) -> None:
        """The session is over: hand its passes on (see `finished`)."""
        if self.passes or self.laps:
            self.finished.append({"passes": self.passes, "laps": self.laps, "track": self._track or "", "car": self._car})
        self.passes, self.laps = [], []

    def reset(self) -> None:
        self._track: str | None = None
        self._session: str | None = None
        self._car = ""
        self._active: dict[int, Pass] = {}
        self._best: dict[int, dict[str, Any]] = {}  # turn index -> best clean pass record
        self.pending: dict[int, Mistake] = {}  # turn index -> what to say next time
        self._tipped: dict[int, int] = {}  # turn index -> lap it was last tipped on
        self._tips_on_lap: tuple[int, int] = (-1, 0)
        self._last_tip_lap = -99
        self._prev: Snapshot | None = None
        self.passes = []  # every finished pass this session
        self.laps: list[dict[str, Any]] = []  # every finished lap this session
        self._lap_no: int | None = None
        self._lap: dict[str, Any] = {}
        self.stint = 0

    def update(self, snap: Snapshot) -> tuple[list[Mistake], list[Mistake]]:
        """Feed one snapshot; returns (mistakes just found, tips to say now)."""
        prev, self._prev = self._prev, snap
        turns, length = self._map.turns, self._map.length_m
        if snap.track != self._track or snap.session != self._session:
            self.finish()
            self.reset()
            self._track, self._session, self._car, self._prev = snap.track, snap.session, snap.car, snap
            return [], []
        if snap.status == "live":
            self._count_lap(prev, snap)
        if not turns or not length or snap.status != "live" or snap.in_pit_lane or snap.current_ms is None:
            self._active.clear()  # a pass through the pit lane says nothing
            return [], []
        found = self._watch(prev, snap, turns, length)
        return found, self._tip(snap, turns, length)

    def _count_lap(self, prev: Snapshot | None, snap: Snapshot) -> None:
        """Laps and stints: a stint starts when the car leaves the pit lane."""
        if prev is not None and prev.in_pit_lane and not snap.in_pit_lane:
            self.stint += 1
        if snap.laps != self._lap_no:
            if self._lap_no is not None and prev is not None and self._lap:
                self._finish_lap(prev, snap)
            # A lap we joined halfway (Pit Wall started mid-lap) is never a clean lap.
            self._lap = {"front": 0.0, "rear": 0.0, "n": 0, "pit": snap.in_pit_lane, "partial": self._lap_no is None}
            self._lap_no = snap.laps
        lap = self._lap
        lap["pit"] = lap["pit"] or snap.in_pit_lane
        if snap.speed_kmh > 60:
            lap["front"] += (snap.tyre_temp_c[0] + snap.tyre_temp_c[1]) / 2
            lap["rear"] += (snap.tyre_temp_c[2] + snap.tyre_temp_c[3]) / 2
            lap["n"] += 1

    def _finish_lap(self, prev: Snapshot, snap: Snapshot) -> None:
        lap, n = self._lap, max(1, self._lap["n"])
        number = self._lap_no or 0
        mine = [p for p in self.passes if p["lap"] == number]
        self.laps.append({
            "lap": number, "stint": self.stint,
            "time_s": round(snap.last_ms / 1000, 3) if snap.last_ms else None,
            "valid": prev.lap_valid, "pit": lap["pit"], "partial": lap["partial"],
            "lockups": sum(p["lockup"] for p in mine), "wheelspin": sum(p["wheelspin"] for p in mine),
            "cuts": sum(not p["valid"] for p in mine),
            "front_c": round(lap["front"] / n, 1), "rear_c": round(lap["rear"] / n, 1),
            "wet": prev.wet_tyres, "session": prev.session,
        })

    def _watch(self, prev: Snapshot | None, snap: Snapshot, turns: list[Turn], length: float) -> list[Mistake]:
        pre, post = self._pre_m / length, self._post_m / length
        found = []
        for i, turn in enumerate(turns):
            inside = _inside(snap.lap_pos, turn.start - pre, turn.end + post)
            p = self._active.get(i)
            if inside and p is None:
                # Only a pass that starts at the start of the zone counts (not after a pit exit).
                if _inside(snap.lap_pos, turn.start - pre, turn.start - pre / 2):
                    self._active[i] = Pass(snap.laps, snap.speed_kmh, snap.current_ms or 0)
            elif inside and p is not None:
                self._sample(p, prev, snap, turn, pre)
            elif p is not None:
                del self._active[i]
                rec = self._record(i, turn, p, snap, length)
                self.passes.append(rec)
                mistake = self._judge(i, turn, p, rec)
                if mistake:
                    self.pending[i] = mistake
                    found.append(mistake)
                else:
                    self.pending.pop(i, None)
        return found

    def _sample(self, p: Pass, prev: Snapshot | None, snap: Snapshot, turn: Turn, pre: float) -> None:
        ms = snap.current_ms or 0
        # The lap timer restarts at the line (also on an out lap, where no lap is counted):
        # then this step took about one poll.
        dt = ms - p.last_ms if ms >= p.last_ms else 100
        p.took_ms += dt
        p.last_ms = ms
        pos = snap.lap_pos
        front, rear = max(snap.wheel_slip[:2]), max(snap.wheel_slip[2:])
        if snap.brake >= 0.3:
            if p.brake_at is None:
                p.brake_at = pos
            if front >= self._lock_slip:
                p.lock += 1
        p.peak_brake = max(p.peak_brake, snap.brake)
        if snap.speed_kmh < p.min_kmh:
            p.min_kmh, p.min_at, p.full_at = snap.speed_kmh, pos, None
        elif p.full_at is None and snap.throttle >= 0.9:
            p.full_at = pos
        if snap.throttle < 0.1 and snap.brake < 0.1 and _inside(pos, turn.start - pre, turn.end):
            p.coast_ms += dt
        if snap.abs > 0:
            p.abs_ms += dt
        if snap.tc > 0:
            p.tc_ms += dt
        if _inside(pos, turn.start, turn.end):
            self._steer(p, snap.steer)
        if snap.throttle >= self._spin_throttle and rear >= self._spin_slip:
            p.spin += 1
        if prev is not None and prev.lap_valid and not snap.lap_valid and snap.laps == prev.laps:
            p.invalid = True

    def _steer(self, p: Pass, s: float) -> None:
        """Count reversals of the steering by at least steer_swing (hysteresis, so noise is not one)."""
        if p.steer_ext is None:
            p.steer_ext = s
        elif p.steer_dir == 0:
            if abs(s - p.steer_ext) >= self._swing:
                p.steer_dir, p.steer_ext = (1 if s > p.steer_ext else -1), s
        elif (s - p.steer_ext) * p.steer_dir > 0:
            p.steer_ext = s
        elif abs(s - p.steer_ext) >= self._swing:
            p.corrections += 1
            p.steer_dir, p.steer_ext = -p.steer_dir, s

    def _record(self, i: int, turn: Turn, p: Pass, snap: Snapshot, length: float) -> dict[str, Any]:
        """One pass as plain numbers: metres are + before the turn (braking) or after it (throttle)."""
        return {
            "turn": turn.number, "name": turn.name, "index": i, "lap": p.lap,
            "time_s": round(p.took_ms / 1000, 2),
            "valid": not p.invalid, "lockup": p.lock >= self._samples, "wheelspin": p.spin >= self._samples,
            "entry_kmh": round(p.entry_kmh),
            "brake_m": round(_signed(turn.start - p.brake_at) * length) if p.brake_at is not None else None,
            "peak_brake": round(p.peak_brake, 2),
            "min_kmh": round(p.min_kmh) if p.min_kmh < 999 else None,
            "full_m": round(_signed(p.full_at - turn.end) * length) if p.full_at is not None else None,
            "coast_s": round(p.coast_ms / 1000, 1),
            "abs_s": round(p.abs_ms / 1000, 1),
            "tc_s": round(p.tc_ms / 1000, 1),
            "corrections": p.corrections,
            "stint": self.stint,
            # What the car and track were like, for the AI that writes the personal tips.
            "session": snap.session, "brake_bias": round(snap.brake_bias, 3), "abs_level": snap.abs_level,
            "tc_level": snap.tc_level, "engine_map": snap.engine_map, "grip": snap.grip, "rain": snap.rain,
            "wet_tyres": snap.wet_tyres, "road_c": round(snap.road_c),
        }

    def _judge(self, i: int, turn: Turn, p: Pass, rec: dict[str, Any]) -> Mistake | None:
        best = self._best.get(i)
        clean = rec["valid"] and not rec["lockup"] and not rec["wheelspin"]
        if clean and rec["time_s"] > 0 and rec["min_kmh"] is not None and (best is None or rec["time_s"] < best["time_s"]):
            self._best[i] = rec
        if p.invalid:
            return Mistake("tip_off", turn, p.lap, pattern="off")
        if rec["lockup"]:
            return Mistake("tip_lockup", turn, p.lap, pattern="lockup")
        if rec["wheelspin"]:
            return Mistake("tip_wheelspin", turn, p.lap, pattern="wheelspin")
        if clean and best is not None and rec["time_s"] - best["time_s"] >= self._loss_s:
            loss = rec["time_s"] - best["time_s"]
            return Mistake("tip_slow", turn, p.lap, loss_s=loss, pattern=why_slow(rec, best, self.differs))
        return None

    def _tip(self, snap: Snapshot, turns: list[Turn], length: float) -> list[Mistake]:
        if any(_inside(snap.lap_pos, t.start, t.end) for t in turns):
            return []  # never talk the driver through a corner
        lap, said = self._tips_on_lap
        if lap != snap.laps:
            self._tips_on_lap = (snap.laps, 0)
            said = 0
        race = snap.session == "race"
        lead = max(self._min_lead_m, snap.speed_kmh / 3.6 * self._lead_s)
        for i, m in list(self.pending.items()):
            gap = ((turns[i].start - snap.lap_pos) % 1) * length if i < len(turns) else 0
            if gap > lead:
                continue
            del self.pending[i]  # said now or too late: either way only once
            if gap < self._min_gap_m or said >= self._per_lap or self._tipped.get(i) == snap.laps - 1:
                continue
            if race and (m.kind not in self._race_kinds or snap.laps - self._last_tip_lap < self._race_gap):
                continue  # in a race the driver has other things on; only the big ones, now and then
            self._tipped[i] = snap.laps
            self._tips_on_lap = (snap.laps, said + 1)
            self._last_tip_lap = snap.laps
            return [m]
        return []
