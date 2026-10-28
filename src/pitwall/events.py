"""Event engine: plain code rules turn player-car snapshots into radio calls.

Code decides when to speak; the line comes from calls.toml (phase 3 lets a local
model reword non-urgent ones). The Radio queue decides what goes out next.
"""

from __future__ import annotations

import random
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Snapshot
from . import personal
from .style import condition
from .tips import Mistake, TurnCoach
from .track import TrackMap, Turn

DEFAULT_CALLS = Path(__file__).with_name("calls.toml")
TYRES = ("front left", "front right", "rear left", "rear right")

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def load_calls(path: Path | None = None) -> dict[str, dict[str, Any]]:
    with open(path or DEFAULT_CALLS, "rb") as f:
        return tomllib.load(f)


# Numbers go to the voice as words: TTS reads "1:50.260" like a stopwatch, an
# engineer says "one fifty point two". The subtitle shows the same words.
def words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    rest = n % 100
    return f"{_ONES[n // 100]} hundred" + ("" if rest == 0 else " and " + words(rest))


def spoken_decimal(x: float) -> str:
    tenths = round(x * 10)
    return f"{words(tenths // 10)} point {_ONES[tenths % 10]}"


def spoken_loss(s: float) -> str:
    """0.3 -> "three tenths", 0.5 -> "half a second", 1.4 -> "one point four seconds"."""
    tenths = round(s * 10)
    if tenths == 5:
        return "half a second"
    if tenths < 10:
        return f"{words(tenths)} tenths" if tenths > 1 else "a tenth"
    if tenths % 10 == 0:
        return "a second" if tenths == 10 else f"{words(tenths // 10)} seconds"
    return f"{spoken_decimal(s)} seconds"


def spoken_turn(turn: Turn) -> str:
    return turn.name or f"turn {words(int(turn.number.split('-')[0]))}"


def spoken_lap(ms: int) -> str:
    """1:50.260 -> "one fifty point two", 2:05.311 -> "two oh five point three"."""
    tenths = ms // 100
    minutes, rest = divmod(tenths, 600)
    secs, tenth = divmod(rest, 10)
    sec_words = words(secs) if secs >= 10 else "oh oh" if secs == 0 else "oh " + _ONES[secs]
    return f"{words(minutes)} {sec_words} point {_ONES[tenth]}"


@dataclass(slots=True)
class Call:
    kind: str  # key in calls.toml
    priority: int  # 1 urgent, 2 normal, 3 info
    text: str
    at: float  # engine clock, seconds
    lap: int = 0  # lap number shown to the driver (completed + 1)
    expire_s: float | None = None  # drop it if not said by then (default: the Radio's)

    def to_wire(self) -> dict[str, Any]:
        return {"kind": self.kind, "priority": self.priority, "text": self.text, "lap": self.lap}


class EventEngine:
    def __init__(
        self,
        thresholds: dict[str, Any],
        calls: dict[str, dict[str, Any]] | None = None,
        variants: dict[str, list[str]] | None = None,
        rng: random.Random | None = None,
        track_map: TrackMap | None = None,
        personal_root: Path | None = None,
    ):
        self._lines = calls or load_calls()
        # AI-written alternatives per kind (wording.py); empty means canned lines only (Lite mode).
        self.variants = variants or {}
        self._rng = rng or random.Random()
        self._last_text: dict[str, str] = {}
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
        self._closing_warn_ms = thresholds["pit"]["closing_warn_s"] * 1000
        self.coach = TurnCoach(thresholds, track_map) if track_map else None
        self.tips = True  # Settings: per-turn tips on or off
        self._tip_expire = thresholds["tips"]["expire_s"]
        self.prepare: list[str] = []  # tip lines to render now, so they play at once later
        self._personal_root = personal_root
        self.personal: dict[str, dict[str, dict[str, str]]] = {}  # condition -> turn -> habit -> AI-written line
        self._personal_track: str | None = None
        self.reset()

    def reset(self) -> None:
        self._prev: Snapshot | None = None
        self._last_call: dict[str, float] = {}
        self._fuel_level = 0  # 0 ok, 1 warn, 2 critical
        self._hot_since: dict[str, float] = {}
        self._pit_said: set[str] = set()  # pit window calls go out once per race
        self._last_flag = "none"  # last flag other than none
        self._green_for: tuple[str, str] | None = None  # session the start green was called in
        if self.coach:
            self.coach.finish()
            self.coach.reset()

    def _call(self, kind: str, now: float, snap: Snapshot, **fields: Any) -> Call | None:
        line = self._lines[kind]
        cooldown = self._flag_cooldown if kind.startswith("flag_") else self._cooldown.get(kind, 0.0)
        last = self._last_call.get(kind)
        if last is not None and now - last < cooldown:
            return None
        self._last_call[kind] = now
        return Call(kind, int(line["priority"]), self._pick(kind, line["text"]).format(**fields), now, snap.laps + 1)

    def _pick(self, kind: str, canned: str) -> str:
        """The canned line or one of its variants, never the same one twice in a row."""
        options = [canned, *self.variants.get(kind, [])]
        if len(options) > 1:
            options = [o for o in options if o != self._last_text.get(kind)]
        text = self._rng.choice(options)
        self._last_text[kind] = text
        return text

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
            *self._pit_window(prev, snap, now),
            *self._tips(snap, now),
        ]
        return [c for c in calls if c is not None]

    def _flags(self, prev: Snapshot | None, snap: Snapshot, now: float) -> list[Call | None]:
        if snap.flag == "none" or (prev is not None and prev.flag == snap.flag):
            return []
        last, self._last_flag = self._last_flag, snap.flag
        if snap.flag == "green":
            # On the Nordschleife ACC flashes green after every local yellow zone (6 calls in
            # 6 minutes, online race 2026-10-28). Green is news at the start, or after a yellow.
            session = (snap.track, snap.session)
            if self._green_for == session and last != "yellow":
                return []
            self._green_for = session
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
            return [self._call("fuel_warn", now, snap, laps=words(round(laps)))]
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
                    temp=words(round(snap.tyre_temp_c[i])), psi=spoken_decimal(snap.tyre_pressure_psi[i]),
                ))
        return calls

    def _pit_window(self, prev: Snapshot | None, snap: Snapshot, now: float) -> list[Call | None]:
        """Mandatory pit stop window, race only.

        ACC gives the window in ms and centres it on half distance: in a 5-minute race
        start + end = 300000 = the race length (2026-10-28 recording). So race time
        elapsed = start + end - time left, which also works when Pit Wall joins mid-race.
        start >= end means no window.
        """
        if snap.session != "race":
            return []
        calls: list[Call | None] = []

        def once(kind: str, **fields: Any) -> None:
            if kind not in self._pit_said:
                self._pit_said.add(kind)
                calls.append(self._call(kind, now, snap, **fields))

        # Not yet seen live which field ACC updates first, so either one counts.
        if prev is not None and prev.missing_mandatory_pits > 0 and not prev.mandatory_pit_done and (
            snap.missing_mandatory_pits < prev.missing_mandatory_pits or snap.mandatory_pit_done
        ):
            once("pit_stop_done")
        start, end = snap.pit_window_start, snap.pit_window_end
        if end <= start or snap.missing_mandatory_pits <= 0 or snap.mandatory_pit_done:
            return calls
        elapsed = start + end - snap.session_time_left
        left_ms = end - elapsed

        if start <= elapsed <= end:
            once("pit_window_open")
            if left_ms <= self._closing_warn_ms:
                minutes = max(1, round(left_ms / 60000))
                once("pit_window_closing", minutes="a minute" if minutes == 1 else f"{words(minutes)} minutes")
        elif elapsed > end:
            once("pit_window_missed")
        return calls

    def _tips(self, snap: Snapshot, now: float) -> list[Call | None]:
        if not self.coach:
            return []
        if snap.track != self._personal_track:
            self.reload_personal(snap.track)
        found, due = self.coach.update(snap)
        for m in found:
            # Worded now, a lap before it is said, so the voice has time to render it.
            line = self.personal_line(m, snap)
            if line:
                m.extra["text"], m.extra["personal"] = f"Coming up, {spoken_turn(m.turn)}. {line}", True
            else:
                m.extra["text"] = self._pick(m.kind, self._lines[m.kind]["text"]).format(
                    turn=spoken_turn(m.turn), loss=spoken_loss(m.loss_s)
                )
            if self.tips:
                self.prepare.append(m.extra["text"])
        if not self.tips:
            return []
        return [self._tip_call(m, now, snap) for m in due]

    def reload_personal(self, track: str | None) -> None:
        self._personal_track = track
        self.personal = personal.load(self._personal_root, track or "")

    def personal_line(self, m: Mistake, snap: Snapshot) -> str | None:
        """The AI-written line for this habit in this turn, written for today's conditions."""
        cond = condition({"rain": snap.rain, "grip": snap.grip, "wet_tyres": snap.wet_tyres})
        return self.personal.get(cond, {}).get(m.turn.number, {}).get(m.pattern)

    def _tip_call(self, m: Mistake, now: float, snap: Snapshot) -> Call:
        priority = int(self._lines[m.kind]["priority"])
        return Call(m.kind, priority, m.extra["text"], now, snap.laps + 1, expire_s=self._tip_expire)

    def _best_lap(self, prev: Snapshot | None, snap: Snapshot, now: float) -> list[Call | None]:
        if prev is None or snap.laps < 2 or snap.best_ms is None or snap.laps == prev.laps:
            return []
        if prev.best_ms is not None and snap.best_ms < prev.best_ms:
            return [self._call("best_lap", now, snap, time=spoken_lap(snap.best_ms))]
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
        self._queue = [c for c in self._queue if now - c.at <= (c.expire_s or self.expire_s)]
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
