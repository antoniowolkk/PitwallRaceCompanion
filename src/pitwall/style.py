"""Driving style patterns: what keeps happening in a turn across a session.

The per-turn passes (tips.py) of one session are saved when it ends. For every turn
with enough passes, code compares each pass with the best clean one and keeps a habit
when it shows up often enough: lockups, wheelspin, cuts, braking earlier, a lower
minimum speed, coasting, full throttle later. Those facts, with the car settings and
track conditions they happened in, are what the local AI writes personal tips from.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

WET_GRIP = {"damp", "wet", "flooded"}


def condition(rec: dict[str, Any]) -> str:
    """dry or wet: personal tips are kept per condition, a wet line is wrong in the dry."""
    rain = rec.get("rain") or "none"
    return "wet" if rec.get("wet_tyres") or rec.get("grip") in WET_GRIP or rain != "none" else "dry"


def _most(recs: list[dict[str, Any]], key: str) -> Any:
    values = [r[key] for r in recs if r.get(key) not in (None, "")]
    return Counter(values).most_common(1)[0][0] if values else None


def context(passes: list[dict[str, Any]], track: str, car: str) -> dict[str, Any]:
    """What the session was like: most common setting or condition over its passes."""
    return {
        "track": track,
        "car": car,
        "session": _most(passes, "session") or "practice",
        "laps": len({r["lap"] for r in passes}),
        "condition": _most([{"c": condition(r)} for r in passes], "c") or "dry",
        "grip": _most(passes, "grip"),
        "rain": _most(passes, "rain"),
        "road_c": _most(passes, "road_c"),
        "brake_bias": _most(passes, "brake_bias"),
        "abs_level": _most(passes, "abs_level"),
        "tc_level": _most(passes, "tc_level"),
        "engine_map": _most(passes, "engine_map"),
    }


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def patterns(passes: list[dict[str, Any]], c: dict[str, Any]) -> list[dict[str, Any]]:
    """Per turn (in lap order): its best clean pass and the habits found, strongest first."""
    by_turn: dict[int, list[dict[str, Any]]] = {}
    for r in passes:
        by_turn.setdefault(r["index"], []).append(r)
    out = []
    for index in sorted(by_turn):
        recs = by_turn[index]
        n = len(recs)
        if n < c["min_passes"]:
            continue
        clean = [r for r in recs if r["valid"] and not r["lockup"] and not r["wheelspin"] and r["min_kmh"] is not None]
        best = min(clean, key=lambda r: r["time_s"]) if clean else None
        found: list[dict[str, Any]] = []

        def keep(pattern: str, hits: int, fact: str) -> None:
            if hits >= c["min_repeats"]:
                found.append({"pattern": pattern, "count": hits, "fact": fact})

        locks = [r for r in recs if r["lockup"]]
        fact = f"locked a front wheel under braking on {len(locks)} of {n} passes"
        later = [r["brake_m"] for r in locks if best and r["brake_m"] is not None and best["brake_m"] is not None]
        if later and best and best["brake_m"] - _median(later) >= 10:
            fact += f", braking about {round(best['brake_m'] - _median(later))} m later than on the best pass"
        keep("lockup", len(locks), fact)
        spins = [r for r in recs if r["wheelspin"]]
        keep("wheelspin", len(spins), f"rear wheelspin on the way out on {len(spins)} of {n} passes")
        offs = [r for r in recs if not r["valid"]]
        keep("off", len(offs), f"the lap went invalid here (track limits or contact) on {len(offs)} of {n} passes")

        if best:
            others = [r for r in clean if r is not best]
            diffs = {
                "early_brake": [r["brake_m"] - best["brake_m"] for r in others
                                if r["brake_m"] is not None and best["brake_m"] is not None],
                "low_min_speed": [best["min_kmh"] - r["min_kmh"] for r in others],
                "coasting": [r["coast_s"] - best["coast_s"] for r in others],
                "late_throttle": [r["full_m"] - best["full_m"] for r in others
                                  if r["full_m"] is not None and best["full_m"] is not None],
            }
            limits = {"early_brake": c["brake_m"], "low_min_speed": c["min_kmh"], "coasting": c["coast_s"], "late_throttle": c["full_m"]}
            say = {
                "early_brake": "braked about {v:.0f} m earlier than on the best pass",
                "low_min_speed": "minimum speed about {v:.0f} km/h lower than on the best pass",
                "coasting": "coasted (no throttle, no brake) about {v:.1f} s longer than on the best pass",
                "late_throttle": "back to full throttle about {v:.0f} m later than on the best pass",
            }
            for pattern, values in diffs.items():
                hits = [v for v in values if v >= limits[pattern]]
                keep(pattern, len(hits), f"{say[pattern].format(v=_median(hits))} on {len(hits)} of {n} passes")

        if found:
            found.sort(key=lambda f: -f["count"])
            out.append({
                "turn": recs[0]["turn"], "name": recs[0]["name"], "passes": n,
                "best": {k: best[k] for k in ("time_s", "brake_m", "min_kmh", "full_m", "coast_s")} if best else None,
                "patterns": found,
            })
    return out


# Sessions: saved when one ends, so the offer to write tips survives a restart.

def sessions_dir(root: Path) -> Path:
    return root / "sessions"


def save_session(root: Path, passes: list[dict[str, Any]], track: str, car: str, c: dict[str, Any]) -> dict[str, Any] | None:
    """Save a finished session; returns its summary, or None when it had nothing to learn from."""
    found = patterns(passes, c)
    if not passes:
        return None
    ctx = context(passes, track, car)
    sid = f"{time.strftime('%Y%m%d-%H%M%S')}_{re.sub(r'[^\w-]', '_', track)}_{ctx['session']}"
    data = {"id": sid, **ctx, "patterns": found, "passes": passes, "tips": None, "dismissed": False}
    path = sessions_dir(root) / f"{sid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    log.info("session saved: %s, %d passes, %d turns with patterns", sid, len(passes), len(found))
    return data


def load_session(root: Path, sid: str) -> dict[str, Any] | None:
    path = sessions_dir(root) / f"{Path(sid).name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def update_session(root: Path, sid: str, **changes: Any) -> None:
    data = load_session(root, sid)
    if data is not None:
        data.update(changes)
        (sessions_dir(root) / f"{sid}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


def latest_offer(root: Path) -> dict[str, Any] | None:
    """The newest session with patterns that has no tips yet and was not waved off."""
    for path in sorted(sessions_dir(root).glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("patterns") and not data.get("tips") and not data.get("dismissed"):
            return data
    return None


def offer(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """The short form the dashboard shows."""
    if not data:
        return None
    return {
        "session": data["id"], "track": data["track"], "car": data["car"], "kind": data["session"],
        "laps": data["laps"], "condition": data["condition"],
        "turns": [{"turn": t["turn"], "name": t["name"], "habits": [p["pattern"] for p in t["patterns"]]} for t in data["patterns"]],
    }
