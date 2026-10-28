"""Engineer voice: local text-to-speech on the CPU, played on the gaming PC.

Two engines. Kokoro (default) sounds natural but needs ~3 s per new line on 2 cores;
Piper sounds flatter but renders in ~0.5 s. Either way, one worker thread loads the
model, renders each line to a cached WAV file (so a repeated call plays at once) and
plays calls one after another. The poll loop only ever puts text on a queue, so speech
never slows telemetry.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.request import urlopen

from .config import data_dir

log = logging.getLogger(__name__)

VOICES = {
    "kokoro": {"female": "af_heart", "male": "bm_george"},
    "piper": {"female": "en_US-amy-medium", "male": "en_US-lessac-medium"},
}
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/{}"
KOKORO_FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")  # 92 MB + 28 MB, all voices
KOKORO_SPEED = 1.05
PIPER_RATE = 0.9  # Piper length_scale: below 1 is faster
RADIO_DRIVE = 3.0  # compression strength; 1 is barely any
MAX_WAIT_S = 15.0  # a non-urgent line that waited longer than this is stale, drop it
PREPARE = 9  # queue priority of render-only jobs: after every real call
STYLE = f"k{KOKORO_SPEED}-p{PIPER_RATE}-d{RADIO_DRIVE}"  # in the cache key, so a sound change re-renders

Synth = Callable[[str, Path], None]  # render text to a WAV file
Loader = Callable[[Path, str, float], Synth]  # (data dir, voice id, volume) -> Synth; called with 1.0
Player = Callable[[Path], None]  # play a WAV file, blocking


def model_files(engine: str, choice: str, root: Path | None = None) -> list[Path]:
    root = root or data_dir()
    if engine == "kokoro":
        return [root / "kokoro" / name for name in KOKORO_FILES]
    voice_id = VOICES["piper"][choice]
    return [root / "voices" / f"{voice_id}.onnx", root / "voices" / f"{voice_id}.onnx.json"]


def download(engine: str, choice: str, root: Path | None = None) -> list[Path]:
    """Fetch what a voice needs: Kokoro from the kokoro-onnx release, Piper (~63 MB a
    voice) from the official rhasspy/piper-voices repo. Present files are skipped."""
    files = model_files(engine, choice, root)
    files[0].parent.mkdir(parents=True, exist_ok=True)
    if engine == "piper":
        from piper.download_voices import download_voice

        download_voice(VOICES["piper"][choice], files[0].parent)
        return files
    for path in files:
        if path.exists():
            continue
        tmp = path.with_suffix(".part")
        with urlopen(KOKORO_URL.format(path.name)) as response, open(tmp, "wb") as f:
            shutil.copyfileobj(response, f)
        tmp.replace(path)
    return files


def _lean_session(path: Path):
    """onnxruntime defaults reserve a big memory arena and use every core. Two
    threads keep speech off the game's cores (more barely helps Kokoro anyway)."""
    import onnxruntime

    opts = onnxruntime.SessionOptions()
    opts.enable_cpu_mem_arena = False
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def _write_wav(out: Path, audio, sample_rate: int, volume: float) -> None:
    tmp = out.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(radio_loud(audio, volume).tobytes())
    tmp.replace(out)  # never leave a half-written cache file


def kokoro_synth(root: Path, voice_id: str, volume: float) -> Synth:
    from kokoro_onnx import Kokoro

    model, voices = model_files("kokoro", "", root)
    kokoro = Kokoro.from_session(_lean_session(model), str(voices))
    lang = "en-gb" if voice_id.startswith("b") else "en-us"  # bf_/bm_ voices are British

    def synth(text: str, out: Path) -> None:
        audio, sample_rate = kokoro.create(text, voice=voice_id, speed=KOKORO_SPEED, lang=lang)
        _write_wav(out, audio, sample_rate, volume)

    return synth


def piper_synth(root: Path, voice_id: str, volume: float) -> Synth:
    import json

    import numpy as np
    from piper import PiperVoice, SynthesisConfig
    from piper.config import PiperConfig

    model = root / "voices" / f"{voice_id}.onnx"
    voice = PiperVoice(
        config=PiperConfig.from_dict(json.loads(Path(f"{model}.json").read_text(encoding="utf-8"))),
        session=_lean_session(model),
    )
    # A touch faster than the default reads more like radio than an audiobook.
    config = SynthesisConfig(length_scale=PIPER_RATE)

    def synth(text: str, out: Path) -> None:
        chunks = list(voice.synthesize(text, syn_config=config))
        audio = np.concatenate([c.audio_float_array for c in chunks]) if chunks else np.zeros(1, np.float32)
        _write_wav(out, audio, voice.config.sample_rate, volume)

    return synth


LOADERS: dict[str, Loader] = {"kokoro": kokoro_synth, "piper": piper_synth}


def radio_loud(audio, volume: float):
    """Soft-clip compression: lifts the quiet parts of speech so it cuts through engine
    noise like team radio. TTS only normalises the peak, which leaves speech quiet."""
    import numpy as np

    peak = float(np.max(np.abs(audio))) or 1.0
    x = audio / peak
    y = np.tanh(RADIO_DRIVE * x) / np.tanh(RADIO_DRIVE)
    return (np.clip(y * volume, -1.0, 1.0) * 32767).astype(np.int16)


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
        choice: str = "male",
        volume: float = 1.0,
        root: Path | None = None,
        engine: str = "kokoro",
        load: Loader | None = None,
        player: Player = play,
    ):
        self.engine = engine
        self.choice = choice
        self.volume = volume
        self._root = root or data_dir()
        self._load = load or LOADERS[engine]
        self._player = player
        # (priority, order, queued at, text, on_start); urgent (1) first, text None stops.
        self._queue: queue.PriorityQueue[tuple[int, int, float, str | None, Callable[[], None] | None]] = (
            queue.PriorityQueue()
        )
        self._order = itertools.count()
        self._thread: threading.Thread | None = None
        self._synth: Synth | None = None
        if choice == "off":
            self.status = "off"
        elif not all(p.exists() for p in model_files(engine, choice, self._root)):
            self.status = "missing"
        else:
            self.status = "loading"

    @property
    def voice_id(self) -> str:
        return VOICES[self.engine].get(self.choice, "off")

    @property
    def cache_dir(self) -> Path:
        return self._root / "cache" / f"{self.engine}-{self.voice_id}"

    @property
    def active(self) -> bool:
        return self.status in ("loading", "ready")

    def start(self, warm: Iterable[str] = ()) -> None:
        """Load the model in the background and pre-render lines we know we will need."""
        if self.status != "loading":
            if self.status == "missing":
                log.warning(
                    "voice not downloaded; run: pitwall voices download %s --engine %s", self.choice, self.engine
                )
            return
        self._thread = threading.Thread(target=self._run, args=(list(warm),), name="voice", daemon=True)
        self._thread.start()

    def prepare(self, text: str) -> None:
        """Render a line into the cache when the voice is idle, without playing it."""
        if self.active:
            self._queue.put((PREPARE, next(self._order), time.monotonic(), text, None))

    def say(
        self, text: str, on_start: Callable[[], None] | None = None, priority: int = 2, max_wait: float | None = None
    ) -> None:
        """Queue a line; on_start runs (in the voice thread) right before it plays.

        A call waiting behind a slow render must not hold up an urgent one, so the
        queue is ordered by priority, and non-urgent lines go stale after MAX_WAIT_S.
        """
        if self.active:
            # A shorter wait goes in as an earlier "queued at", so the stale check stays one line.
            queued = time.monotonic() - (MAX_WAIT_S - max_wait if max_wait is not None else 0.0)
            self._queue.put((priority, next(self._order), queued, text, on_start))
        elif on_start:
            on_start()

    def close(self) -> None:
        if self._thread:
            self._queue.put((0, next(self._order), 0.0, None, None))
            self._thread.join(timeout=5)

    def _wav(self, text: str) -> Path:
        key = hashlib.sha1(f"{STYLE}|{text}".encode()).hexdigest()[:16]
        path = self.cache_dir / f"{key}.wav"
        if not path.exists():
            assert self._synth is not None
            self._synth(text, path)
        return path

    def render_all(self, lines: list[str], report: Callable[[int, int], None] = lambda done, total: None) -> int:
        """Render lines into the cache now, in this thread (for `pitwall lines generate`).
        Returns how many were new. Do not mix with start()."""
        if self.status != "loading":
            return 0
        if self._synth is None:
            self._synth = self._load(self._root, self.voice_id, 1.0)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        new = 0
        for i, text in enumerate(lines, 1):
            key = hashlib.sha1(f"{STYLE}|{text}".encode()).hexdigest()[:16]
            if not (self.cache_dir / f"{key}.wav").exists():
                self._wav(text)
                new += 1
            report(i, len(lines))
        return new

    def _at_volume(self, wav: Path) -> Path:
        volume = self.volume  # read once: the Settings screen can change it any time
        if volume >= 0.999:
            return wav
        import numpy as np

        with wave.open(str(wav)) as src:
            params = src.getparams()
            audio = np.frombuffer(src.readframes(src.getnframes()), np.int16)
        out = self.cache_dir / "_play.wav"
        with wave.open(str(out), "wb") as dst:
            dst.setparams(params)
            dst.writeframes((audio * volume).astype(np.int16).tobytes())
        return out

    def _run(self, warm: list[str]) -> None:
        try:
            # Rendered at full volume and cached once; the volume setting applies at play time.
            self._synth = self._load(self._root, self.voice_id, 1.0)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.exception("voice failed to load")
            self.status = "error"
            return
        self.status = "ready"
        log.info("voice ready: %s %s", self.engine, self.voice_id)
        # Real calls always go first; pre-rendering (Kokoro ~3 s a line on the first run,
        # cache hits after) only uses the gaps.
        while True:
            try:
                item = self._queue.get(block=not warm)
            except queue.Empty:
                text = warm.pop(0)
                try:
                    self._wav(text)
                except Exception:
                    log.exception("voice failed to pre-render: %s", text)
                continue
            priority, _, queued_at, text, on_start = item
            if text is None:
                return
            if priority == PREPARE:
                try:
                    self._wav(text)
                except Exception:
                    log.exception("voice failed to pre-render: %s", text)
                continue
            if priority > 1 and time.monotonic() - queued_at > MAX_WAIT_S:
                log.info("voice dropped stale line: %s", text)
                continue
            try:
                wav = self._at_volume(self._wav(text))
                if on_start:
                    on_start()
                self._player(wav)
            except Exception:
                log.exception("voice failed to speak: %s", text)
