"""ACC setup files: find them, read the values the advisor may change, write a new version.

ACC keeps setups as JSON in Documents\\Assetto Corsa Competizione\\Setups\\<car>\\<track>.
Files saved by ACC use Windows line breaks, tabs, and number lists on one line
("[ 57, 69, 50, 56 ]"). Pit Wall never re-serialises a setup: a new version is the
base file byte for byte with one value changed, so ACC reads exactly its own format.
The base file is never touched; a version gets the next free name <base>_pw_v<N>.json.

Values are click steps (rear wing 12, brake bias 26), not real units, and the valid
range per car is not in the file, so a change is always one click from a working setup.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Param:
    key: str  # short name used in rules and the dashboard
    label: str
    path: tuple[str, ...]  # keys down to the value
    index: int | None = None  # position in a number list (ride height: 0 front, 2 rear)


# Click values the advisor may change. Static camber, toe out linear and rod length are
# computed by the game and never touched. Ride height lists have four numbers; across the
# setups on this PC only the 1st (front) and 3rd (rear) vary, the others are constant per car.
PARAMS = {p.key: p for p in [
    Param("rear_wing", "Rear wing", ("advancedSetup", "aeroBalance", "rearWing")),
    Param("splitter", "Splitter", ("advancedSetup", "aeroBalance", "splitter")),
    Param("ride_front", "Front ride height", ("advancedSetup", "aeroBalance", "rideHeight"), 0),
    Param("ride_rear", "Rear ride height", ("advancedSetup", "aeroBalance", "rideHeight"), 2),
    Param("arb_front", "Front anti-roll bar", ("advancedSetup", "mechanicalBalance", "aRBFront")),
    Param("arb_rear", "Rear anti-roll bar", ("advancedSetup", "mechanicalBalance", "aRBRear")),
    Param("brake_bias", "Brake bias", ("advancedSetup", "mechanicalBalance", "brakeBias")),
    Param("preload", "Diff preload", ("advancedSetup", "drivetrain", "preload")),
    Param("tc", "Traction control", ("basicSetup", "electronics", "tC1")),
    Param("abs", "ABS", ("basicSetup", "electronics", "abs")),
]}


def setups_root() -> Path:
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / "Assetto Corsa Competizione" / "Setups"


def track_folder(root: Path, car: str, track: str) -> Path | None:
    """ACC names track folders with mixed case ("Spa", "monza"): match ignoring case."""
    car_dir = root / car
    if not car_dir.is_dir():
        return None
    for d in car_dir.iterdir():
        if d.is_dir() and d.name.lower() == track.lower():
            return d
    return None


def list_setups(root: Path, car: str, track: str) -> list[Path]:
    folder = track_folder(root, car, track)
    return sorted(folder.glob("*.json"), key=lambda p: p.name.lower()) if folder else []


def read_text(path: Path) -> str:
    # newline="" keeps the CRLF line breaks exactly as ACC wrote them.
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def value(data: dict[str, Any], param: Param) -> int:
    v: Any = data
    for key in param.path:
        v = v[key]
    return int(v[param.index] if param.index is not None else v)


def values(data: dict[str, Any]) -> dict[str, int]:
    """Every advisor parameter present in this setup."""
    out = {}
    for key, p in PARAMS.items():
        try:
            out[key] = value(data, p)
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    return out


def change(text: str, param: Param, new: int) -> str:
    """The same text with one value replaced; everything else stays byte for byte."""
    name = re.escape(param.path[-1])
    if param.index is None:
        pattern = re.compile(rf'("{name}":\s*)(-?\d+)')
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ValueError(f"{param.path[-1]} found {len(matches)} times")
        m = matches[0]
        out = text[: m.start(2)] + str(new) + text[m.end(2):]
    else:
        pattern = re.compile(rf'("{name}":\s*\[)([^\]]*)(\])')
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ValueError(f"{param.path[-1]} found {len(matches)} times")
        m = matches[0]
        items = m.group(2).split(",")
        if param.index >= len(items):
            raise ValueError(f"{param.path[-1]} has no position {param.index}")
        old = items[param.index]
        items[param.index] = re.sub(r"-?\d+", str(new), old, count=1)  # keep the spaces around it
        out = text[: m.start(2)] + ",".join(items) + text[m.end(2):]
    # The result must parse, with exactly that one value different.
    before, after = json.loads(text), json.loads(out)
    if value(after, param) != new:
        raise ValueError("change did not land")
    _set(before, param, new)
    if before != after:
        raise ValueError("more than one value changed")
    return out


def _set(data: dict[str, Any], param: Param, new: int) -> None:
    v: Any = data
    for key in param.path[:-1]:
        v = v[key]
    if param.index is None:
        v[param.path[-1]] = new
    else:
        v[param.path[-1]][param.index] = new


def version_path(base: Path) -> Path:
    """<base>_pw_v<N>.json next to the base, N one higher than any Pit Wall version of it."""
    stem = re.sub(r"_pw_v\d+$", "", base.stem)
    taken = [int(m.group(1)) for p in base.parent.glob(f"{stem}_pw_v*.json")
             if (m := re.search(r"_pw_v(\d+)$", p.stem))]
    return base.parent / f"{stem}_pw_v{max(taken, default=1) + 1}.json"


def write_version(base: Path, param: Param, new: int) -> Path:
    """Write a new version with one value changed. Never overwrites a file."""
    text = change(read_text(base), param, new)
    out = version_path(base)
    with open(out, "x", encoding="utf-8", newline="") as f:  # "x": fail rather than overwrite
        f.write(text)
    return out
