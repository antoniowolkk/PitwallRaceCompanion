import asyncio
import threading
import time
import wave

from pitwall.config import load_thresholds
from pitwall.events import EventEngine, Radio
from pitwall.models import Snapshot
from pitwall.poller import Poller
import pitwall.voice as voice_mod
from pitwall.voice import Voice, model_files, radio_loud


def fake_model(root, engine="kokoro"):
    for path in model_files(engine, "male", root):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")


class FakeSynth:
    def __init__(self, delay=0.0):
        self.rendered = []
        self.delay = delay

    def load(self, root, voice_id, volume):
        self.voice_id = voice_id

        def synth(text, out):
            time.sleep(self.delay)
            self.rendered.append(text)
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(b"\x00\x00" * 10)

        return synth


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_missing_model_and_off_never_speak(tmp_path):
    played, started = [], []
    missing = Voice("male", root=tmp_path, player=played.append)
    assert missing.status == "missing"
    missing.start()
    missing.say("Box, box.", on_start=lambda: started.append(1))
    assert Voice("off", root=tmp_path).status == "off"
    assert played == []
    assert started == [1]  # no voice: the subtitle still goes out at once


def test_engines_pick_their_voice(tmp_path):
    fake_model(tmp_path, "piper")
    assert Voice("male", root=tmp_path, engine="piper").voice_id == "en_US-lessac-medium"
    assert Voice("male", root=tmp_path, engine="piper").status == "loading"
    assert Voice("male", root=tmp_path).voice_id == "bm_george"
    assert Voice("female", root=tmp_path).voice_id == "af_heart"


def test_warms_lines_then_plays_from_cache(tmp_path):
    fake_model(tmp_path)
    synth, played = FakeSynth(), []
    v = Voice("male", root=tmp_path, load=synth.load, player=played.append)
    assert v.status == "loading"
    v.start(["Box, box. Fuel's critical, pit this lap."])
    wait_for(lambda: v.status == "ready" and synth.rendered)
    assert synth.voice_id == "bm_george"

    v.say("Box, box. Fuel's critical, pit this lap.")  # cached: no new render
    v.say("Nice one, personal best, one fifty point two.")
    wait_for(lambda: len(played) == 2)
    v.close()
    assert synth.rendered == ["Box, box. Fuel's critical, pit this lap.", "Nice one, personal best, one fifty point two."]
    assert played[0].parent == v.cache_dir and played[0].suffix == ".wav"


def test_calls_jump_ahead_of_warming(tmp_path):
    fake_model(tmp_path)
    synth, played = FakeSynth(delay=0.05), []
    v = Voice("male", root=tmp_path, load=synth.load, player=played.append)
    v.start([f"warm {i}" for i in range(20)])
    wait_for(lambda: v.status == "ready")
    v.say("Yellow flag, careful. No overtaking.")
    wait_for(lambda: len(played) == 1)
    assert synth.rendered.index("Yellow flag, careful. No overtaking.") < 5
    v.close()


def test_load_failure_reports_error(tmp_path):
    fake_model(tmp_path)

    def broken(root, voice_id, volume):
        raise RuntimeError("bad model")

    v = Voice("male", root=tmp_path, load=broken, player=lambda p: None)
    v.start()
    wait_for(lambda: v.status == "error")


def test_radio_loud_lifts_quiet_speech():
    import numpy as np

    t = np.linspace(0, 1, 22050, dtype=np.float32)
    speech = 0.2 * np.sin(2 * np.pi * 200 * t)
    speech[:100] = 1.0  # one loud peak, like a plosive
    out = radio_loud(speech, 1.0).astype(np.float32) / 32767
    assert np.sqrt(np.mean(out**2)) > 2 * np.sqrt(np.mean((speech / 1.0) ** 2))
    assert np.max(np.abs(out)) <= 1.0


def test_subtitle_goes_out_when_the_voice_starts(tmp_path):
    fake_model(tmp_path)
    synth, played = FakeSynth(delay=0.2), []
    gate = threading.Event()

    def player(path):
        played.append(path)
        gate.set()

    voice = Voice("male", root=tmp_path, load=synth.load, player=player)

    class Src:
        name = "fake"
        snap = Snapshot(status="live", flag="none", tyre_pressure_psi=(26.5,) * 4, tyre_temp_c=(85,) * 4)

        def read(self):
            return self.snap

        def close(self):
            pass

    sent = []

    async def broadcast(msg):
        sent.append(msg)

    src = Src()
    now = [0.0]
    poller = Poller(src, broadcast, engine=EventEngine(load_thresholds()), radio=Radio(), voice=voice, clock=lambda: now[0])
    voice.start()
    wait_for(lambda: voice.status == "ready")

    async def scenario():
        await poller.tick()
        src.snap = Snapshot(status="live", flag="yellow", tyre_pressure_psi=(26.5,) * 4, tyre_temp_c=(85,) * 4)
        await poller.tick()
        now[0] = 1.5  # a flag is called once it has shown for a second
        await poller.tick()
        assert not [m for m in sent if m["type"] == "radio"]  # still rendering, nothing on screen yet
        for _ in range(200):
            if [m for m in sent if m["type"] == "radio"]:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    voice.close()
    radio = [m for m in sent if m["type"] == "radio"]
    assert [m["text"] for m in radio] == ["Yellow flag, careful. No overtaking."]
    assert gate.is_set() and "time" in radio[0]
    assert list(poller.radio_log) == radio
    assert [m["voice"] for m in sent if m["type"] == "status"][0] == "ready"


def test_urgent_line_overtakes_queue_and_stale_lines_drop(tmp_path, monkeypatch):
    fake_model(tmp_path)
    synth, played, gate = FakeSynth(), [], threading.Event()

    def player(path):
        played.append(path)
        gate.wait(5)  # hold the first line "on air" while others queue

    v = Voice("male", root=tmp_path, load=synth.load, player=player)
    v.start()
    wait_for(lambda: v.status == "ready")
    monkeypatch.setattr(voice_mod, "MAX_WAIT_S", 0.1)
    v.say("Nice one, personal best, one forty point five.", priority=3)
    wait_for(lambda: len(played) == 1)
    v.say("Pressures are a bit high.", priority=2)
    v.say("Nice one, personal best, one forty point four.", priority=3)
    v.say("Yellow flag, careful. No overtaking.", priority=1)
    time.sleep(0.3)  # the non-urgent ones go stale while the first plays
    gate.set()
    wait_for(lambda: len(played) == 2)
    time.sleep(0.1)
    v.close()
    assert synth.rendered == ["Nice one, personal best, one forty point five.", "Yellow flag, careful. No overtaking."]
