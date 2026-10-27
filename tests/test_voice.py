import asyncio
import time
import wave

from pitwall.events import EventEngine, Radio
from pitwall.config import load_thresholds
from pitwall.models import Snapshot
from pitwall.poller import Poller
from pitwall.voice import Voice, model_path


def fake_model(root):
    path = model_path("female", root)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"onnx")


class FakeSynth:
    def __init__(self):
        self.rendered = []

    def load(self, path, volume):
        def synth(text, out):
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
    played = []
    missing = Voice("female", root=tmp_path, player=played.append)
    assert missing.status == "missing"
    missing.start()
    missing.say("Box this lap.")
    assert Voice("off", root=tmp_path).status == "off"
    assert played == []


def test_warms_fixed_lines_then_plays_from_cache(tmp_path):
    fake_model(tmp_path)
    synth, played = FakeSynth(), []
    v = Voice("female", root=tmp_path, load=synth.load, player=played.append)
    assert v.status == "loading"
    v.start(["Fuel critical, box this lap."])
    wait_for(lambda: v.status == "ready")
    assert synth.rendered == ["Fuel critical, box this lap."]

    v.say("Fuel critical, box this lap.")  # cached: no new render
    v.say("Personal best, 1:51.025.")
    wait_for(lambda: len(played) == 2)
    v.close()
    assert synth.rendered == ["Fuel critical, box this lap.", "Personal best, 1:51.025."]
    assert played[0].parent == v.cache_dir and played[0].suffix == ".wav"


def test_load_failure_reports_error(tmp_path):
    fake_model(tmp_path)

    def broken(path, volume):
        raise RuntimeError("bad model")

    v = Voice("female", root=tmp_path, load=broken, player=lambda p: None)
    v.start()
    wait_for(lambda: v.status == "error")


def test_poller_speaks_released_calls_and_reports_voice_status(tmp_path):
    fake_model(tmp_path)
    synth, played = FakeSynth(), []
    voice = Voice("female", root=tmp_path, load=synth.load, player=played.append)

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
    poller = Poller(src, broadcast, engine=EventEngine(load_thresholds()), radio=Radio(), voice=voice, clock=lambda: 0.0)
    assert poller.status()["voice"] == "loading"
    voice.start()
    wait_for(lambda: voice.status == "ready")

    async def scenario():
        await poller.tick()
        src.snap = Snapshot(status="live", flag="yellow", tyre_pressure_psi=(26.5,) * 4, tyre_temp_c=(85,) * 4)
        await poller.tick()

    asyncio.run(scenario())
    wait_for(lambda: len(played) == 1)
    voice.close()
    assert synth.rendered == ["Yellow flag, careful. No overtaking."]
    assert [m["voice"] for m in sent if m["type"] == "status"][0] == "ready"
