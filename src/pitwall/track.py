"""Track map: the circuit outline learned from the player's own laps, and its turns.

ACC gives the player car's world position and how far around the lap it is (0..1).
One full lap fills BINS slots with x/y; that outline is saved per track, so later
sessions have the map at once. Turns come, best first, from tracks.toml (official
numbers, checked by hand), from Lovely Track Data (every ACC track, downloaded with
`pitwall tracks download`), or from the speed dips of that lap, numbered in order.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import Snapshot

log = logging.getLogger(__name__)

TURNS_FILE = Path(__file__).with_name("tracks.toml")
# Lovely Track Data, (c) Lovely Sim Racing, CC BY-NC-SA 4.0: fetched to the user's PC, not shipped.
LOVELY = "https://raw.githubusercontent.com/Lovely-Sim-Racing/lovely-track-data/main/data/"
LOVELY_SIM = "assettocorsacompetizione"
BINS = 500
FILL = 0.97  # share of slots one lap must fill; small gaps are interpolated
MIN_KMH = 20  # slower than this (grid, spins) says nothing about the line
MAX_STEP = 25  # slots between two samples that are filled in a straight line (fast replay, slow polling)


@dataclass(frozen=True)
class Turn:
    number: str  # "1", or "5-6" for a complex counted as one
    name: str
    start: float  # lap fraction where the car starts turning
    end: float

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


def load_turns(path: Path = TURNS_FILE) -> dict[str, list[Turn]]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    return {track: [Turn(**t) for t in turns] for track, turns in data.items()}


def track_key(name: str) -> str:
    """ACC's track name and Lovely's file name agree once case and - vs _ are ignored."""
    return name.lower().replace("-", "_")


def lovely_turns(data: dict[str, Any], half_m: float = 50.0) -> list[Turn]:
    """Turns from a Lovely Track Data file, numbered in lap order. Back-to-back parts with
    the same name (Monza's two Rettifilo turns) become one turn, "1-2". Some files give
    only the apex ("marker"); those turns span half_m either side of it."""
    half = half_m / float(data.get("length") or 5000)
    parts = []
    for t in data.get("turn", []):
        if "start" in t and "end" in t:
            parts.append((float(t["start"]), float(t["end"]), t.get("name", "")))
        elif "marker" in t:
            m = float(t["marker"])
            parts.append((round((m - half) % 1, 4), round((m + half) % 1, 4), t.get("name", "")))
    turns: list[Turn] = []
    for n, (start, end, name) in enumerate(sorted(parts), 1):
        last = turns[-1] if turns else None
        if last and name and last.name == name:
            turns[-1] = Turn(f"{last.number.split('-')[0]}-{n}", name, last.start, end)
        else:
            turns.append(Turn(str(n), name, start, end))
    return turns


def http_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as res:
        return json.loads(res.read())


def download_lovely(root: Path, get: Callable[[str], Any] = http_json, report: Callable[[str], None] = print) -> int:
    """Fetch every ACC track file into root/lovely; returns how many were saved."""
    out = root / "lovely"
    out.mkdir(parents=True, exist_ok=True)
    saved = 0
    for entry in get(LOVELY + "manifest.json")["tracks"][LOVELY_SIM]:
        data = get(LOVELY + entry["path"])
        # The file name, not trackId: the manifest has typos there ("murburgring").
        (out / f"{track_key(Path(entry['path']).stem)}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
        report(f"{entry['trackName']}: {len(lovely_turns(data))} turns")
        saved += 1
    return saved


def find_turns(speeds: list[float], drop_kmh: float, window: float, half_m: float, length_m: float) -> list[Turn]:
    """Unnamed turns at the speed dips: a slot is a turn's slowest point if nothing within
    `window` (lap fraction) either side is slower and the car was `drop_kmh` faster before."""
    n = len(speeds)
    w = max(1, round(window * n))
    half = half_m / length_m if length_m else 0.01
    apexes = []
    for i, s in enumerate(speeds):
        before = [speeds[(i - k) % n] for k in range(1, 2 * w + 1)]
        after = [speeds[(i + k) % n] for k in range(1, w + 1)]
        if all(s < b for b in before[:w]) and all(s <= a for a in after) and max(before) >= s + drop_kmh:
            apexes.append(i / n)
    return [
        Turn(str(k), "", round((a - half) % 1, 4), round((a + half) % 1, 4))
        for k, a in enumerate(sorted(apexes), 1)
    ]


def _fill_gaps(slots: list[Any]) -> list[Any]:
    """Linear interpolation over empty slots, around the lap."""
    n = len(slots)
    known = [i for i, v in enumerate(slots) if v is not None]
    out = list(slots)
    for a, b in zip(known, known[1:] + [known[0] + n]):
        for j in range(a + 1, b):
            f = (j - a) / (b - a)
            va, vb = slots[a], slots[b % n]
            if isinstance(va, tuple):
                out[j % n] = tuple(round(p + (q - p) * f, 1) for p, q in zip(va, vb))
            else:
                out[j % n] = round(va + (vb - va) * f, 1)
    return out


def _length(points: list[tuple[float, float]]) -> float:
    return sum(math.dist(p, q) for p, q in zip(points, points[1:] + points[:1]))


class TrackMap:
    def __init__(self, thresholds: dict[str, Any], root: Path | None = None, turns: dict[str, list[Turn]] | None = None):
        cfg = thresholds["track"]
        self._drop = cfg["turn_drop_kmh"]
        self._window = cfg["turn_window"]
        self._half_m = cfg["turn_half_m"]
        self.root = root  # where outlines are saved; None keeps them in memory only
        self.official = load_turns() if turns is None else turns
        self.track: str | None = None
        self.points: list[tuple[float, float]] | None = None
        self.turns: list[Turn] = []
        self.source = "auto"  # official | lovely | auto: where the turns came from
        self.length_m = 0.0
        self._new_lap(None)

    def _path(self, track: str) -> Path | None:
        return self.root / f"{re.sub(r'[^\w-]', '_', track)}.json" if self.root else None

    def _new_lap(self, lap: int | None) -> None:
        self._lap = lap
        self._xy: list[tuple[float, float] | None] = [None] * BINS
        self._speed: list[float | None] = [None] * BINS
        self._prev: tuple[int, tuple[float, float], float] | None = None

    def _set_track(self, track: str) -> None:
        self.track = track
        self.points = None
        # Lovely's length lets tips work before our own outline (and its length) is learned.
        lovely, self.length_m = self._lovely(track)
        if track in self.official:
            self.turns, self.source = self.official[track], "official"
        else:
            self.turns, self.source = lovely, "lovely" if lovely else "auto"
        self._new_lap(None)
        path = self._path(track)
        if path and path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self._finish([tuple(p) for p in saved["points"]], saved["speeds"], save=False)
            except (OSError, ValueError, KeyError, TypeError):
                log.warning("ignoring unreadable %s", path)

    def _lovely(self, track: str) -> tuple[list[Turn], float]:
        path = self.root / "lovely" / f"{track_key(track)}.json" if self.root else None
        if not path or not path.exists():
            return [], 0.0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return lovely_turns(data), float(data.get("length") or 0)
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("ignoring unreadable %s", path)
            return [], 0.0

    def _finish(self, points: list[tuple[float, float]], speeds: list[float], save: bool = True) -> None:
        self.points = points
        self.length_m = _length(points)
        if self.source == "auto":
            self.turns = find_turns(speeds, self._drop, self._window, self._half_m, self.length_m)
        log.info("track map ready: %s, %.0f m, %d turns", self.track, self.length_m, len(self.turns))
        path = self._path(self.track or "")
        if save and path:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"track": self.track, "made": time.strftime("%Y-%m-%d %H:%M"), "points": points, "speeds": speeds}
            path.write_text(json.dumps(data), encoding="utf-8")

    def update(self, snap: Snapshot) -> dict[str, Any] | None:
        """Feed one snapshot; returns a "track" message when the map changed."""
        if not snap.track:
            return None
        changed = snap.track != self.track
        if changed:
            self._set_track(snap.track)
        if self.points is None and snap.status == "live":
            if snap.laps != self._lap:
                # Crossing the line: keep the lap just done if it went all the way round.
                filled = sum(p is not None for p in self._xy)
                if self._lap is not None and filled >= FILL * BINS:
                    self._finish(_fill_gaps(self._xy), _fill_gaps(self._speed))
                    changed = True
                self._new_lap(snap.laps)
            if not snap.in_pit_lane and snap.speed_kmh >= MIN_KMH and self.points is None:
                self._add(int(snap.lap_pos * BINS) % BINS, (snap.x, snap.y), snap.speed_kmh)
            else:
                self._prev = None
        return self.message() if changed else None

    def _add(self, i: int, xy: tuple[float, float], kmh: float) -> None:
        step = (i - self._prev[0]) % BINS if self._prev else 0
        if self._prev and 1 < step <= MAX_STEP:
            j0, xy0, kmh0 = self._prev
            for k in range(1, step):
                f = k / step
                self._put((j0 + k) % BINS, (xy0[0] + (xy[0] - xy0[0]) * f, xy0[1] + (xy[1] - xy0[1]) * f), kmh0 + (kmh - kmh0) * f)
        self._put(i, xy, kmh)
        self._prev = (i, xy, kmh)

    def _put(self, i: int, xy: tuple[float, float], kmh: float) -> None:
        self._xy[i] = (round(xy[0], 1), round(xy[1], 1))
        old = self._speed[i]
        self._speed[i] = kmh if old is None else min(old, kmh)

    def message(self) -> dict[str, Any]:
        return {
            "type": "track",
            "track": self.track,
            "points": self.points,
            "length_m": round(self.length_m),
            "turns": [t.to_wire() for t in self.turns],
            "source": self.source,
        }
