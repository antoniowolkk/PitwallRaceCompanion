from __future__ import annotations

import json
import logging
import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = Path(__file__).with_name("thresholds.toml")


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    with open(path or DEFAULT_THRESHOLDS, "rb") as f:
        return tomllib.load(f)


def default_source() -> str:
    return "acc" if sys.platform == "win32" else "mock"


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share"
    return Path(base) / "PitWall"


# Driver preferences: changed on the Settings screen, saved to settings.json.
CHOICES = {
    "voice": ("male", "female", "off"),
    "engine": ("kokoro", "piper"),
    "calls": ("all", "important", "urgent"),
    "subtitle_size": ("normal", "large"),
}
CALL_PRIORITY = {"all": 3, "important": 2, "urgent": 1}  # highest call priority still spoken


@dataclass
class Prefs:
    voice: str = "male"
    engine: str = "kokoro"
    volume: float = 1.0
    calls: str = "all"
    subtitles: bool = True
    subtitle_size: str = "normal"
    lite: bool = False  # Lite mode: canned lines only, no AI-written variants
    tips: bool = True  # per-turn tips before a turn where the last lap went wrong

    def updated(self, changes: dict[str, Any]) -> Prefs:
        """A copy with changes applied; raises ValueError on unknown keys or bad values."""
        known = {f.name for f in fields(self)}
        data = asdict(self)
        for key, value in changes.items():
            if key not in known:
                raise ValueError(f"unknown setting: {key}")
            if key in CHOICES and value not in CHOICES[key]:
                raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
            if key == "volume":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise ValueError("volume must be between 0 and 1")
                value = float(value)
            if key in ("subtitles", "lite", "tips") and not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            data[key] = value
        return Prefs(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Prefs:
        """Missing or broken file: defaults. A bad value is skipped, not fatal."""
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.warning("ignoring unreadable settings file %s", path)
            return cls()
        prefs = cls()
        for key, value in saved.items() if isinstance(saved, dict) else ():
            try:
                prefs = prefs.updated({key: value})
            except ValueError as e:
                log.warning("ignoring saved setting: %s", e)
        return prefs


@dataclass
class Settings:
    source: str = field(default_factory=default_source)  # acc | mock | replay
    replay_path: Path | None = None
    mock_speed: float = 1.0
    poll_hz: float = 10.0
    host: str = "127.0.0.1"
    port: int = 8765
    prefs: Prefs = field(default_factory=Prefs)
    prefs_path: Path | None = None  # where the Settings screen saves; None keeps changes in memory
    variants: dict[str, list[str]] = field(default_factory=dict)  # AI-written lines (wording.py)
    tracks_dir: Path | None = None  # learned track outlines; None keeps them in memory
    coach_dir: Path | None = None  # saved sessions and personal tips; None: not saved, no offer
    thresholds: dict[str, Any] = field(default_factory=load_thresholds)
