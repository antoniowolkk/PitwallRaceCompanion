from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLDS = Path(__file__).with_name("thresholds.toml")


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    with open(path or DEFAULT_THRESHOLDS, "rb") as f:
        return tomllib.load(f)


def default_source() -> str:
    return "acc" if sys.platform == "win32" else "mock"


@dataclass
class Settings:
    source: str = field(default_factory=default_source)  # acc | mock | replay
    replay_path: Path | None = None
    mock_speed: float = 1.0
    poll_hz: float = 10.0
    host: str = "127.0.0.1"
    port: int = 8765
    voice: str = "female"  # female | male | off
    volume: float = 1.0
    thresholds: dict[str, Any] = field(default_factory=load_thresholds)
