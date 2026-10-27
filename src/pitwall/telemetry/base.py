from __future__ import annotations

from typing import Protocol

from ..models import Snapshot


class SourceUnavailable(RuntimeError):
    """The source cannot run on this machine (for example ACC on macOS)."""


class TelemetrySource(Protocol):
    name: str

    def read(self) -> Snapshot | None:
        """Latest player-car snapshot, or None while the game has no live data."""
        ...

    def close(self) -> None: ...
