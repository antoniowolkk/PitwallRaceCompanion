"""Per-turn tips: spot a mistake in a turn, mention it on the way into that turn next lap.

Each pass through a turn is timed (from a little before it to a little after it) and
watched for a lockup (front wheel slip under braking), wheelspin on the way out, or
the lap going invalid. A pass with one of those, or clearly slower than the best pass
through that turn this session, is remembered. Next time round, a few seconds before
the turn, the engineer mentions it: at most one tip a lap, never while in a turn, and
never the same turn two laps running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Snapshot
from .track import TrackMap, Turn


@dataclass
class Pass:
    lap: int
    t0: int  # current_ms when the pass began
    t1: int = 0  # current_ms at its last sample
    lock: int = 0  # samples with a front wheel locking under braking
    spin: int = 0  # samples with a rear wheel spinning under throttle
    invalid: bool = False  # the lap went invalid here (track limits, contact)


@dataclass
class Mistake:
    kind: str  # tip_off | tip_lockup | tip_wheelspin | tip_slow
    turn: Turn
    lap: int  # lap (completed count) the mistake happened on
    loss_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def _inside(pos: float, start: float, end: float) -> bool:
    start, end = start % 1, end % 1
    return start <= pos <= end if start <= end else pos >= start or pos <= end


class TurnCoach:
    def __init__(self, thresholds: dict[str, Any], track_map: TrackMap):
        c = thresholds["tips"]
        self._map = track_map
        self._pre_m, self._post_m = c["pre_m"], c["post_m"]
        self._lead_s, self._min_lead_m, self._min_gap_m = c["lead_s"], c["min_lead_m"], c["min_gap_m"]
        self._lock_slip, self._spin_slip = c["lock_slip"], c["spin_slip"]
        self._spin_throttle, self._samples = c["spin_throttle"], c["slip_samples"]
        self._loss_s, self._per_lap = c["loss_s"], c["max_per_lap"]
        self.reset()

    def reset(self) -> None:
        self._track: str | None = None
        self._active: dict[int, Pass] = {}
        self._best: dict[int, int] = {}  # turn index -> best pass, ms
        self.pending: dict[int, Mistake] = {}  # turn index -> what to say next time
        self._tipped: dict[int, int] = {}  # turn index -> lap it was last tipped on
        self._tips_on_lap: tuple[int, int] = (-1, 0)
        self._prev: Snapshot | None = None

    def update(self, snap: Snapshot) -> tuple[list[Mistake], list[Mistake]]:
        """Feed one snapshot; returns (mistakes just found, tips to say now)."""
        prev, self._prev = self._prev, snap
        turns, length = self._map.turns, self._map.length_m
        if snap.track != self._track:
            self.reset()
            self._track, self._prev = snap.track, snap
            return [], []
        if not turns or not length or snap.status != "live" or snap.in_pit_lane or snap.current_ms is None:
            self._active.clear()  # a pass through the pit lane says nothing
            return [], []
        found = self._watch(prev, snap, turns, length)
        return found, self._tip(snap, turns, length)

    def _watch(self, prev: Snapshot | None, snap: Snapshot, turns: list[Turn], length: float) -> list[Mistake]:
        pre, post = self._pre_m / length, self._post_m / length
        found = []
        for i, turn in enumerate(turns):
            inside = _inside(snap.lap_pos, turn.start - pre, turn.end + post)
            p = self._active.get(i)
            if inside and p is None:
                # Only a pass that starts at the start of the zone counts (not after a pit exit).
                if _inside(snap.lap_pos, turn.start - pre, turn.start - pre / 2):
                    self._active[i] = Pass(snap.laps, snap.current_ms or 0)
            elif inside and p is not None:
                self._sample(p, prev, snap)
            elif p is not None:
                del self._active[i]
                mistake = self._judge(i, turn, p, snap)
                if mistake:
                    self.pending[i] = mistake
                    found.append(mistake)
                else:
                    self.pending.pop(i, None)
        return found

    def _sample(self, p: Pass, prev: Snapshot | None, snap: Snapshot) -> None:
        p.t1 = snap.current_ms or 0
        if snap.laps != p.lap:  # crossed the line inside the zone
            p.t1 += (snap.last_ms or 0)
        front, rear = max(snap.wheel_slip[:2]), max(snap.wheel_slip[2:])
        if snap.brake >= 0.3 and front >= self._lock_slip:
            p.lock += 1
        if snap.throttle >= self._spin_throttle and rear >= self._spin_slip:
            p.spin += 1
        if prev is not None and prev.lap_valid and not snap.lap_valid and snap.laps == prev.laps:
            p.invalid = True

    def _judge(self, i: int, turn: Turn, p: Pass, snap: Snapshot) -> Mistake | None:
        took = p.t1 - p.t0
        best = self._best.get(i)
        clean = not p.invalid and p.lock < self._samples and p.spin < self._samples
        if took > 0 and not p.invalid and (best is None or took < best):
            self._best[i] = took
        if p.invalid:
            return Mistake("tip_off", turn, p.lap)
        if p.lock >= self._samples:
            return Mistake("tip_lockup", turn, p.lap)
        if p.spin >= self._samples:
            return Mistake("tip_wheelspin", turn, p.lap)
        if clean and best is not None and took - best >= self._loss_s * 1000:
            return Mistake("tip_slow", turn, p.lap, loss_s=(took - best) / 1000)
        return None

    def _tip(self, snap: Snapshot, turns: list[Turn], length: float) -> list[Mistake]:
        if any(_inside(snap.lap_pos, t.start, t.end) for t in turns):
            return []  # never talk the driver through a corner
        lap, said = self._tips_on_lap
        if lap != snap.laps:
            self._tips_on_lap = (snap.laps, 0)
            said = 0
        lead = max(self._min_lead_m, snap.speed_kmh / 3.6 * self._lead_s)
        for i, m in list(self.pending.items()):
            gap = ((turns[i].start - snap.lap_pos) % 1) * length if i < len(turns) else 0
            if gap > lead:
                continue
            del self.pending[i]  # said now or too late: either way only once
            if gap < self._min_gap_m or said >= self._per_lap or self._tipped.get(i) == snap.laps - 1:
                continue
            self._tipped[i] = snap.laps
            self._tips_on_lap = (snap.laps, said + 1)
            return [m]
        return []
