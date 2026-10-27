"""Engineer voice: Piper text-to-speech on the CPU, played on the gaming PC.

One worker thread loads the model, renders each line to a cached WAV file (so a
repeated call plays at once) and plays calls one after another. The poll loop only
ever puts text on a queue, so speech never slows telemetry.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import subprocess
import sys
import threading
import wave
from collections.abc import Callable, Iterable
from pathlib import Path

log = logging.getLogger(__name__)

VOICES = {"female": "en_US-amy-medium", "male": "en_US-lessac-medium"}

Synth = Callable[[str, Path], None]  # render text to a WAV file
Player = Callable[[Path], None]  # play a WAV file, blocking


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share"
    return Path(base) / "PitWall"


def model_path(choice: str, root: Path | None = None) -> Path:
    return (root or data_dir()) / "voices" / f"{VOICES[choice]}.onnx"


def download(choice: str, root: Path | None = None) -> Path:
    """Fetch one voice (~63 MB) from the official rhasspy/piper-voices repo."""
    from piper.download_voices import download_voice

    path = model_path(choice, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    download_voice(VOICES[choice], path.parent)
    return path


def piper_synth(path: Path, volume: float) -> Synth:
    import json

    import onnxruntime
    from piper import PiperVoice, SynthesisConfig
    from piper.config import PiperConfig

    # PiperVoice.load uses default session options: a big memory arena and every
    # core. Two threads keep speech off the game's cores; lines still render in ~0.3 s.
    opts = onnxruntime.SessionOptions()
    opts.enable_cpu_mem_arena = False
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    voice = PiperVoice(
        config=PiperConfig.from_dict(json.loads(Path(f"{path}.json").read_text(encoding="utf-8"))),
        session=onnxruntime.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"]),
    )
    config = SynthesisConfig(volume=volume)

    def synth(text: str, out: Path) -> None:
        tmp = out.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as wav:
            voice.synthesize_wav(text, wav, syn_config=config)
        tmp.replace(out)  # never leave a half-written cache file

    return synth


def play(path: Path) -> None:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", str(path)], check=False)
    else:
        subprocess.run(["aplay", "-q", str(path)], check=False)


class Voice:
    """status: off | missing | loading | ready | error."""

    def __init__(
        self,
        choice: str = "female",
        volume: float = 0.8,
        root: Path | None = None,
        load: Callable[[Path, float], Synth] = piper_synth,
        player: Player = play,
    ):
        self.choice = choice
        self.volume = volume
        self._root = root or data_dir()
        self._load = load
        self._player = player
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._synth: Synth | None = None
        if choice == "off":
            self.status = "off"
        elif not model_path(choice, self._root).exists():
            self.status = "missing"
        else:
            self.status = "loading"

    @property
    def cache_dir(self) -> Path:
        return self._root / "cache" / VOICES.get(self.choice, "off")

    def start(self, warm: Iterable[str] = ()) -> None:
        """Load the model in the background and pre-render fixed lines."""
        if self.status != "loading":
            if self.status == "missing":
                log.warning("voice %s not downloaded; run: pitwall voices download %s", self.choice, self.choice)
            return
        self._thread = threading.Thread(target=self._run, args=(list(warm),), name="voice", daemon=True)
        self._thread.start()

    def say(self, text: str) -> None:
        if self.status in ("loading", "ready"):
            self._queue.put(text)

    def close(self) -> None:
        if self._thread:
            self._queue.put(None)
            self._thread.join(timeout=5)

    def _wav(self, text: str) -> Path:
        key = hashlib.sha1(f"{self.volume:.2f}|{text}".encode()).hexdigest()[:16]
        path = self.cache_dir / f"{key}.wav"
        if not path.exists():
            assert self._synth is not None
            self._synth(text, path)
        return path

    def _run(self, warm: list[str]) -> None:
        try:
            self._synth = self._load(model_path(self.choice, self._root), self.volume)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            for text in warm:
                self._wav(text)
        except Exception:
            log.exception("voice failed to load")
            self.status = "error"
            return
        self.status = "ready"
        log.info("voice ready: %s", VOICES[self.choice])
        while (text := self._queue.get()) is not None:
            try:
                self._player(self._wav(text))
            except Exception:
                log.exception("voice failed to speak: %s", text)
