from __future__ import annotations

from ..config import Settings
from .base import SourceUnavailable, TelemetrySource


def make_source(settings: Settings) -> TelemetrySource:
    if settings.source == "acc":
        from .acc import AccSource

        return AccSource()
    if settings.source == "mock":
        from .mock import MockSource

        return MockSource(speed=settings.mock_speed)
    if settings.source == "replay":
        from .replay import ReplaySource

        if settings.replay_path is None:
            raise SourceUnavailable("replay needs a recording file")
        return ReplaySource(settings.replay_path, speed=settings.mock_speed)
    raise SourceUnavailable(f"unknown source: {settings.source}")


__all__ = ["SourceUnavailable", "TelemetrySource", "make_source"]
