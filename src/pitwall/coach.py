"""Stint coach (phase 4): plain driving tips after a lap, at most one or two a stint.

Code rules look at the laps of the current stint, as tips.py measured them: lockups,
wheelspin, steering corrections against the cleanest pass through each turn, front
against rear tyre temperature, and how close the clean laps are. After each lap the
strongest rule not yet said this stint becomes the tip, said on the next straight
(never in a corner). A clean lap close to the best gets a word of praise, once a stint.
No tip before min_clean_laps clean laps in the stint; none in a race unless race = true.
The Coach screen gets the laps, the tips said, a per-turn table and the style summary.
"""

from __future__ import annotations

import statistics
from typing import Any

from .models import Snapshot
from .style import clean, extra_corrections, summary, tidy
from .tips import TurnCoach, _inside
from .track import TrackMap


class StintCoach:
    def __init__(self, thresholds: dict[str, Any], turns: TurnCoach, track_map: TrackMap):
        self.c = thresholds["coach"]
        self._style = thresholds["style"]
        self._turns = turns
        self._map = track_map
        self.reset()

    def reset(self) -> None:
        self._seen = 0  # laps already judged
        self._said: dict[int, set[str]] = {}  # stint -> rules said
        self._praised: set[int] = set()
        self.pending: tuple[str, int] | None = None  # (call kind, lap it is about)
        self.log: list[dict[str, Any]] = []  # tips said this session, for the Coach screen
        self.dirty = True  # the Coach screen needs a fresh view

    def update(self, snap: Snapshot) -> list[str]:
        """Feed one snapshot; returns call kinds to say now (coach_braking, coach_praise, ...)."""
        laps = self._turns.laps
        if len(laps) < self._seen:  # a new session started in the turn coach
            self.reset()
        while self._seen < len(laps):
            self._seen += 1
            self._judge(laps[: self._seen])
            self.dirty = True
        if self.pending and self._safe(snap):
            kind, _ = self.pending
            self.pending = None
            return [kind]
        return []

    def said(self, kind: str, text: str, lap: int) -> None:
        self.log.append({"kind": kind, "text": text, "lap": lap})
        self.dirty = True

    def _safe(self, snap: Snapshot) -> bool:
        """On a straight, with a few seconds before the next turn."""
        turns, length = self._map.turns, self._map.length_m
        if snap.in_pit_lane or snap.status != "live":
            return False
        if not turns or not length:
            return True
        if any(_inside(snap.lap_pos, t.start, t.end) for t in turns):
            return False
        gap = min(((t.start - snap.lap_pos) % 1) * length for t in turns)
        return gap >= max(self.c["safe_min_m"], snap.speed_kmh / 3.6 * self.c["safe_s"])

    def _judge(self, laps: list[dict[str, Any]]) -> None:
        """Judge the last of `laps` (only what was known when it ended)."""
        c, lap = self.c, laps[-1]
        if lap.get("session") == "race" and not c["race"]:
            return
        stint = [l for l in laps if l["stint"] == lap["stint"] and not l["pit"] and not l["partial"]]
        clean_laps = [l for l in stint if clean(l)]
        if len(clean_laps) < c["min_clean_laps"]:
            return
        said = self._said.setdefault(lap["stint"], set())
        best = min((l["time_s"] for l in laps if clean(l) and l["time_s"]), default=None)
        if tidy(lap) and lap["stint"] not in self._praised and best and lap["time_s"] \
                and 0 < lap["time_s"] - best <= c["praise_within_s"]:
            self._praised.add(lap["stint"])
            self.pending = ("coach_praise", lap["lap"])
            return
        if len(said) >= c["max_per_stint"]:
            return
        scores = {r: s for r, s in self._scores(stint, clean_laps).items() if r not in said and s >= 1}
        if scores:
            rule = max(scores, key=scores.get)
            said.add(rule)
            self.pending = (f"coach_{rule}", lap["lap"])

    def _scores(self, stint: list[dict[str, Any]], clean_laps: list[dict[str, Any]]) -> dict[str, float]:
        """How far each rule is over its limit (1 = just at it) over the last few laps."""
        c = self.c
        window = stint[-c["window_laps"]:]
        numbers = {l["lap"] for l in window}
        scores = {
            "braking": sum(l["lockups"] for l in window) / c["lockups"],
            "throttle": sum(l["wheelspin"] for l in window) / c["wheelspin"],
            "tyres_front": (window[-1]["front_c"] - window[-1]["rear_c"]) / c["front_hot_c"],
        }
        if not any(l["wet"] for l in window):  # in the wet, catching slides is the job
            stint_passes = [p for p in self._turns.passes if p.get("stint") == window[-1]["stint"]]
            extra = extra_corrections(stint_passes)
            recent = [extra[i] for i, p in enumerate(stint_passes) if p["lap"] in numbers]
            if recent:
                scores["steering"] = statistics.mean(recent) / c["steer_extra"]
        times = [l["time_s"] for l in clean_laps[-c["spread_laps"]:] if l["time_s"]]
        if len(times) >= c["spread_laps"]:
            scores["consistency"] = statistics.pstdev(times) / c["spread_s"]
        return scores

    def view(self) -> dict[str, Any]:
        """Everything the Coach screen shows for the session so far."""
        laps, passes = self._turns.laps, self._turns.passes
        clean_times = [l["time_s"] for l in laps if clean(l) and l["time_s"]]
        turns: dict[int, dict[str, Any]] = {}
        for p in passes:
            t = turns.setdefault(p["index"], {"turn": p["turn"], "name": p["name"], "passes": 0, "best_s": None,
                                              "lockups": 0, "wheelspin": 0, "cuts": 0})
            t["passes"] += 1
            t["lockups"] += p["lockup"]
            t["wheelspin"] += p["wheelspin"]
            t["cuts"] += not p["valid"]
            if p["valid"] and not p["lockup"] and p["time_s"] > 0 and (t["best_s"] is None or p["time_s"] < t["best_s"]):
                t["best_s"] = p["time_s"]
        return {
            "type": "coach_view",
            "track": self._turns._track, "car": self._turns._car, "session": self._turns._session,
            "laps": [{**l, "clean": clean(l), "tidy": tidy(l)} for l in laps],
            "best_s": min(clean_times) if clean_times else None,
            "spread_s": round(statistics.pstdev(clean_times[-5:]), 2) if len(clean_times) >= 3 else None,
            "tips": self.log,
            "turns": [turns[i] for i in sorted(turns)],
            "style": summary(passes, laps, self._style),
        }
