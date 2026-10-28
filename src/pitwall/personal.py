"""Personal turn tips: the local AI writes them from a session's patterns, only when asked.

style.py finds the habits per turn; here a local Ollama model turns each one into a
short radio line that fits the driver's car settings and the track conditions. Lines
are checked like the other AI lines (wording.check), saved per track and condition
(dry / wet) and pre-rendered by the voice. In the next session at that track, when the
same habit shows up in a turn, the engineer says the personal line instead of the
canned one. Nothing runs unless the driver says yes (dashboard card or CLI prompt).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import wording
from .track import track_key

log = logging.getLogger(__name__)

MAX_WORDS = 16
TRIES = 3  # asks per turn until every habit has a line that passes the checks

SYSTEM = (
    "You are the race engineer on a GT3 team radio in Assetto Corsa Competizione, coaching your driver "
    "between laps like a relaxed, friendly British engineer. You only use the facts you are given."
)

PROMPT = """Session: {session} at {track}, {car}. Track {condition}, grip {grip}, road {road_c} degrees.
Car settings: {settings}.
Turn: {turn}. Best pass through it: {best}.
Habits seen in this turn:
{habits}

For each habit, write three different radio tips the engineer could say just before the driver reaches this turn.
What can help, per habit (say it in your own words, fitted to the facts above):
- lockup: brake a little earlier or a little softer, release the brake smoothly as you turn in, or one click more ABS, or brake bias one click rearward.
- wheelspin: squeeze the throttle on more gently and later, straighten the wheel first, or one click more TC.
- off: give up a little entry speed to stay inside the track limits.
- early_brake: brake a little later, the best pass braked later.
- low_min_speed: carry more speed through the middle, release the brake earlier.
- coasting: go straight from brake to throttle, no gap with neither pedal.
- late_throttle: back on full power sooner on the way out.
Mention a car setting only when it fits, using the settings above.
Rules:
- One or two short sentences, at most {max_words} words, casual and friendly, like a mate on the radio.
- Tell the driver what to do differently, based only on the habit and the best pass. Do not invent anything.
- For a lockup or wheelspin you may also suggest one click of ABS, TC or brake bias as an option.
- Write numbers as words (fifteen metres), never digits. No dashes, no colons.
- Do not name the turn; the engineer says the name first.
- Do not say he, she, him or her.
- Start with a capital letter and end with a full stop or exclamation mark.
Return JSON: {{"tips": [{{"habit": "<habit name>", "line": "..."}}]}}"""

# Seen from qwen3:4b at Monza (2026-10-28): "Lock the front wheel on the inside, shift to 4th
# gear" (advice that makes no sense) and naming the turn although the engineer says it first.


def tips_path(root: Path, track: str) -> Path:
    return root / "tips" / f"{track_key(track)}.json"


def _best(b: dict[str, Any] | None) -> str:
    if not b:
        return "none clean yet"
    parts = []
    if b.get("brake_m") is not None:
        parts.append(f"braking starts {b['brake_m']} m before the turn")
    if b.get("min_kmh") is not None:
        parts.append(f"minimum speed {b['min_kmh']} km/h")
    if b.get("full_m") is not None:
        parts.append(f"full throttle {b['full_m']} m after the turn")
    return ", ".join(parts) or "no detail"


def ask_turn(session: dict[str, Any], turn: dict[str, Any], model: str, post: wording.Post) -> dict[str, str]:
    """habit -> checked line for one turn (habits the model got wrong are left out)."""
    bias = session.get("brake_bias")
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "format": {"type": "object", "properties": {"tips": {"type": "array", "items": {
            "type": "object", "properties": {"habit": {"type": "string"}, "line": {"type": "string"}},
            "required": ["habit", "line"]}}}, "required": ["tips"]},
        "options": {"temperature": 0.7},
        "keep_alive": "2m",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT.format(
                session=session["session"], track=session["track"], car=session["car"].replace("_", " "),
                condition=session["condition"], grip=session.get("grip") or "unknown", road_c=session.get("road_c"),
                settings=(
                    f"brake bias {bias * 100:.1f} percent front, ABS {session.get('abs_level')}, "
                    f"TC {session.get('tc_level')}, engine map {session.get('engine_map')}"
                ) if bias else "not recorded",
                turn=turn["name"] or f"turn {turn['turn']}", best=_best(turn.get("best")),
                habits="\n".join(f"- {p['pattern']}: {p['fact']}" for p in turn["patterns"]),
                max_words=MAX_WORDS,
            )},
        ],
    }
    try:
        reply = json.loads(post(f"{wording.OLLAMA}/api/chat", body)["message"]["content"])["tips"]
    except (KeyError, TypeError, ValueError):
        log.warning("unreadable model reply for turn %s", turn["turn"])
        return {}
    wanted = {p["pattern"] for p in turn["patterns"]}
    lines = {}
    for tip in reply if isinstance(reply, list) else []:
        habit, line = (tip.get("habit"), tip.get("line")) if isinstance(tip, dict) else (None, None)
        cleaned = check(line, turn["name"]) if isinstance(line, str) else None
        if habit in wanted and habit not in lines and cleaned:
            lines[habit] = cleaned
    return lines


def check(line: str, turn_name: str = "") -> str | None:
    """wording.check, plus: short enough, and not naming the turn (the engineer says it first).
    "in Parabolica" becomes "in there"; any other mention of the name rejects the line."""
    # qwen3:4b often drops the full stop or opens with the habit as a question ("Lockup? Brake...").
    line = line.strip()
    if line and line[-1] not in ".!?":
        line += "."
    line = re.sub(r"^(lockup|lock up|wheelspin|off|early brake|low min speed|coasting|late throttle)\?\s+", "", line, flags=re.I)
    if turn_name:
        line = re.sub(rf"\b(in|into|at|through|out of)\s+(the\s+)?{re.escape(turn_name)}\b", r"\1 there", line, flags=re.I)
    cleaned = wording.check(line, "")
    if not cleaned or len(cleaned.split()) > MAX_WORDS:
        return None
    if turn_name and turn_name.lower() in cleaned.lower():
        return None
    return cleaned


def generate(
    session: dict[str, Any],
    model: str = wording.DEFAULT_MODEL,
    post: wording.Post = wording.http_post,
    report: Callable[[str], None] = print,
) -> dict[str, dict[str, str]]:
    """turn number -> habit -> line, for every turn with patterns (asks again for missing habits)."""
    out: dict[str, dict[str, str]] = {}
    for turn in session["patterns"]:
        lines: dict[str, str] = {}
        for _ in range(TRIES):
            missing = [p for p in turn["patterns"] if p["pattern"] not in lines]
            if not missing:
                break
            lines.update({k: v for k, v in ask_turn(session, {**turn, "patterns": missing}, model, post).items() if k not in lines})
        if lines:
            out[turn["turn"]] = lines
        report(f"{turn['name'] or 'Turn ' + turn['turn']}: {len(lines)} of {len(turn['patterns'])} tips")
    return out


def save(root: Path, session: dict[str, Any], lines: dict[str, dict[str, str]], model: str) -> Path:
    """Merge into the track's tips file under the session's condition; newer lines replace older."""
    path = tips_path(root, session["track"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"track": session["track"], "conditions": {}}
    cond = data["conditions"].setdefault(session["condition"], {})
    for turn, habits in lines.items():
        cond.setdefault(turn, {}).update(habits)
    data.update(made=time.strftime("%Y-%m-%d %H:%M"), model=model, session=session["id"], car=session["car"],
                settings={k: session.get(k) for k in ("brake_bias", "abs_level", "tc_level", "engine_map")})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def load(root: Path | None, track: str) -> dict[str, dict[str, dict[str, str]]]:
    """condition -> turn number -> habit -> line; lines are re-checked, so a stricter filter applies at once."""
    if not root or not track:
        return {}
    try:
        data = json.loads(tips_path(root, track).read_text(encoding="utf-8"))["conditions"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {
        cond: {turn: {h: line for h, line in habits.items() if isinstance(line, str) and check(line)}
               for turn, habits in turns.items()}
        for cond, turns in data.items()
    }
