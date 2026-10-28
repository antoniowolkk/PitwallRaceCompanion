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


def clean(lap: dict[str, Any]) -> bool:
    """A lap that says something about pace (the PRD's "clean lap"): valid, no pit, not joined halfway.
    Small lockups are allowed; they are what the coach is there to talk about."""
    return lap["valid"] and not lap["pit"] and not lap["partial"]


def tidy(lap: dict[str, Any]) -> bool:
    """A clean lap without a single lockup, wheelspin or cut: worth a word of praise."""
    return clean(lap) and not (lap["lockups"] or lap["wheelspin"] or lap["cuts"])


def extra_corrections(passes: list[dict[str, Any]]) -> dict[int, float]:
    """Per position in `passes`: steering reversals beyond the fewest seen in that turn
    (every corner needs some; a chicane more than a kink)."""
    fewest: dict[Any, int] = {}
    for p in passes:
        fewest[p["index"]] = min(fewest.get(p["index"], 99), p.get("corrections", 0))
    return {i: p.get("corrections", 0) - fewest[p["index"]] for i, p in enumerate(passes)}


def summary(passes: list[dict[str, Any]], laps: list[dict[str, Any]], c: dict[str, Any]) -> dict[str, Any]:
    """The driving style of a session in a few plain traits, each good or worth a look.
    Shown on the Coach screen and saved with the session for the setup advisor (phase 5)."""
    traits: list[dict[str, str]] = []
    if len(passes) < c["summary_passes"]:
        return {"traits": [], "text": "Not enough driving yet for a summary."}

    def trait(key: str, good: bool, good_label: str, watch_label: str, detail: str) -> None:
        traits.append({"key": key, "level": "good" if good else "watch",
                       "label": good_label if good else watch_label, "detail": detail})

    braking = [p for p in passes if p.get("brake_m") is not None]
    if braking:
        rate = sum(p["lockup"] for p in braking) / len(braking)
        trait("braking", rate < c["lockup_rate"], "Clean on the brakes", "Locks the fronts",
              f"lockups in {rate:.0%} of braking zones")
    rate = sum(p["wheelspin"] for p in passes) / len(passes)
    trait("throttle", rate < c["wheelspin_rate"], "Smooth on the throttle", "Rear steps out on exit",
          f"wheelspin in {rate:.0%} of turns")
    dry = [p for p in passes if not p.get("wet_tyres")]
    if len(dry) >= c["summary_passes"]:
        extra = statistics.mean(extra_corrections(dry).values())
        trait("steering", extra < c["steer_extra"], "Smooth steering", "Busy hands mid-corner",
              f"{extra:.1f} extra steering corrections a turn")
    coast = statistics.mean(p["coast_s"] for p in passes)
    trait("coasting", coast < c["coast_avg_s"], "Commits from brake to throttle", "Coasts into corners",
          f"{coast:.1f} s coasting a turn")
    cuts = sum(not p["valid"] for p in passes)
    trait("limits", cuts < c["cuts"], "Inside track limits", "Track limits", f"{cuts} turns where the lap went invalid")
    times = [l["time_s"] for l in laps if clean(l) and l["time_s"]]
    if len(times) >= 3:
        spread = statistics.pstdev(times)
        trait("consistency", spread < c["spread_s"], "Consistent lap times", "Lap times vary",
              f"clean laps within {spread:.2f} s of each other (spread)")
    hot = [l["front_c"] - l["rear_c"] for l in laps if l["front_c"] and l["rear_c"]]
    if hot:
        gap = statistics.mean(hot)
        trait("tyres", gap < c["front_hot_c"], "Tyres balanced", "Works the fronts hard",
              f"fronts {abs(gap):.0f} °C {'hotter' if gap > 0 else 'cooler'} than the rears")
    watch = [t["label"].lower() for t in traits if t["level"] == "watch"]
    good = [t["label"].lower() for t in traits if t["level"] == "good"]
    text = (f"Worth a look: {', '.join(watch)}. " if watch else "Nothing stands out. ") + \
           (f"Good: {', '.join(good)}." if good else "")
    return {"traits": traits, "text": text[0].upper() + text[1:]}


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


def save_session(
    root: Path, passes: list[dict[str, Any]], track: str, car: str, c: dict[str, Any], laps: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """Save a finished session; returns its summary, or None when it had nothing to learn from.
    Habits come only from the session types in learn_from: in a race, traffic makes the
    driver brake early or lift, which is not a habit. Other sessions are kept, without habits."""
    laps = laps or []
    if not passes and not laps:
        return None
    ctx = context(passes or [{"lap": 0, "session": laps[0].get("session")}], track, car)
    if laps:
        ctx["laps"] = len(laps)
    found = patterns(passes, c) if ctx["session"] in c["learn_from"] else []
    sid = f"{time.strftime('%Y%m%d-%H%M%S')}_{re.sub(r'[^\w-]', '_', track)}_{ctx['session']}"
    times = [l["time_s"] for l in laps if clean(l) and l["time_s"]]
    data = {"id": sid, **ctx, "patterns": found, "passes": passes, "lap_records": laps,
            "best_s": min(times) if times else None, "clean_laps": len(times),
            "style": summary(passes, laps, c) if passes else {"traits": [], "text": ""},
            "tips": None, "dismissed": False}
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


def list_sessions(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Newest first, without the raw passes: what the Coach screen lists."""
    out = []
    for path in sorted(sessions_dir(root).glob("*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({
            "id": d["id"], "track": d["track"], "car": d["car"], "session": d["session"],
            "condition": d.get("condition"), "laps": d.get("laps"),
            "best_s": d.get("best_s"), "clean_laps": d.get("clean_laps"),
            "style": d.get("style") or {"traits": [], "text": ""},
            "habits": sum(len(t["patterns"]) for t in d.get("patterns") or []),
            "tips_written": bool(d.get("tips")),
        })
    return out


def offer(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """The short form the dashboard shows."""
    if not data:
        return None
    return {
        "session": data["id"], "track": data["track"], "car": data["car"], "kind": data["session"],
        "laps": data["laps"], "condition": data["condition"],
        "turns": [{"turn": t["turn"], "name": t["name"], "habits": [p["pattern"] for p in t["patterns"]]} for t in data["patterns"]],
    }
