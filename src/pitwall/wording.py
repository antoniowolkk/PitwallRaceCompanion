"""AI-written variants of the radio lines, made ahead of time (phase 3).

This PC's GPU (GTX 1650, 4 GB) is full with ACC, and a 4B model on the CPU would
take seconds per call and fight the game for cores. So instead of rewording live,
`pitwall lines generate` asks a local Ollama model, while nobody is racing, for a few
casual variants of every line in calls.toml. They are checked, saved to lines.json,
and pre-rendered by the voice. In a race the engine just picks one: no AI load, no delay.
"""

from __future__ import annotations

import json
import logging
import re
import string
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import data_dir

log = logging.getLogger(__name__)

OLLAMA = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"
MAX_WORDS = 18

SYSTEM = (
    "You are the race engineer on a GT3 team radio in Assetto Corsa Competizione. "
    "You talk to your driver like a relaxed, friendly British engineer, a mate on the radio: "
    "conversational, warm, contractions welcome, never dramatic and never robotic. "
    "Only rewrite what you are given; never add facts, results, numbers or new advice."
)

PROMPT = """Rewrite this radio call {n} different ways.
Original: "{text}"
Rules:
- Same meaning, same urgency, same relaxed tone. Do not make it shorter, blunter or more robotic than the original.
- One or two short sentences, at most 15 words. Plain words only, no dashes, no abbreviations like ASAP.
- Keep every placeholder in curly braces exactly as written: {placeholders}. Add no other braces.
  A placeholder is filled with lower-case words, so never start a sentence with one.
- No digits: numbers only come from the placeholders.
- Do not claim a result (no winning, no positions) and do not add driving advice the original does not give.
- Do not say he, she, him or her.
- Start with a capital letter and end with a full stop or exclamation mark.
Return JSON: {{"lines": ["...", "..."]}}"""

# Seen in the first qwen3:4b run: wrong facts, bad advice, or robot-speak.
BANNED = re.compile(
    r"\b(win|won|winning|victory|podium|first place|slow down|drive slow|fix it|detected|verify|"
    r"asap|immediately|great call|window's (?:sealed|secure|closed|shut))\b",
    re.I,
)

Post = Callable[[str, dict[str, Any]], dict[str, Any]]


def lines_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "lines.json"


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def check(line: str, original: str) -> str | None:
    """The cleaned line if it is fit for the radio, else None."""
    line = " ".join(line.strip().strip('"').split())
    words = line.split()
    if not 2 <= len(words) <= MAX_WORDS or len(line) > 140:
        return None
    if not line[0].isupper() or line[-1] not in ".!":
        return None
    if re.search(r"\d", line) or re.search(r"\b(he|she|him|her|his|hers)\b", line, re.I):
        return None
    if not line.isascii() or re.search(r"\s[-–—]\s|[–—:;]", line) or BANNED.search(line):
        return None
    if re.search(r"(^|[.!?]\s+)\{", line):  # placeholders are lower-case words: no sentence may start with one
        return None
    try:
        if placeholders(line) != placeholders(original):
            return None
        line.format(**{p: "x" for p in placeholders(original)})
    except (ValueError, KeyError, IndexError):
        return None
    return line


def http_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as res:
        return json.loads(res.read())


def ask(text: str, n: int, model: str, post: Post = http_post) -> list[str]:
    names = ", ".join("{" + p + "}" for p in sorted(placeholders(text))) or "none"
    body = {
        "model": model,
        "stream": False,
        "think": False,  # Qwen3 would otherwise write out its reasoning first
        "format": {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
                   "required": ["lines"]},
        "options": {"temperature": 0.9},
        "keep_alive": "2m",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT.format(n=n, text=text, placeholders=names)},
        ],
    }
    reply = post(f"{OLLAMA}/api/chat", body)
    try:
        lines = json.loads(reply["message"]["content"])["lines"]
    except (KeyError, TypeError, ValueError):
        log.warning("unreadable model reply for %r", text)
        return []
    return [line for line in lines if isinstance(line, str)]


def generate(
    calls: dict[str, dict[str, Any]],
    n: int = 5,
    model: str = DEFAULT_MODEL,
    post: Post = http_post,
    report: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Variants for every call kind. A kind keeps its canned line whatever happens."""
    variants: dict[str, list[str]] = {}
    for kind, line in calls.items():
        original = line["text"]
        good: list[str] = []
        for _ in range(2):  # one retry if too many lines fail the checks
            for candidate in ask(original, n, model, post):
                cleaned = check(candidate, original)
                if cleaned and cleaned != original and cleaned not in good:
                    good.append(cleaned)
            if len(good) >= max(2, n // 2):
                break
        variants[kind] = good[:n]
        report(f"{kind}: {len(variants[kind])} variants")
    return {"model": model, "generated": time.strftime("%Y-%m-%d %H:%M"), "variants": variants}


def unload(model: str, post: Post = http_post) -> None:
    """Free the model's memory now instead of after Ollama's 5-minute default."""
    try:
        post(f"{OLLAMA}/api/generate", {"model": model, "keep_alive": 0})
    except (OSError, urllib.error.URLError):
        pass


def save(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load(path: Path, calls: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Saved variants that still fit today's calls.toml; anything stale is dropped."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))["variants"]
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("ignoring unreadable %s", path)
        return {}
    out = {}
    for kind, lines in saved.items():
        if kind in calls and isinstance(lines, list):
            original = calls[kind]["text"]
            out[kind] = [line for line in lines if isinstance(line, str) and check(line, original)]
    return out


def ollama_models(get: Callable[[str], dict[str, Any]] | None = None) -> list[str] | None:
    """Installed model names, or None when Ollama is not running."""
    try:
        if get:
            tags = get(f"{OLLAMA}/api/tags")
        else:
            with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=3) as res:
                tags = json.loads(res.read())
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return [m["name"] for m in tags.get("models", [])]
