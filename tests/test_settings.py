import asyncio
import json
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pitwall.config import Prefs, Settings, load_thresholds
from pitwall.events import EventEngine, Radio
from pitwall.models import Snapshot
from pitwall.poller import Poller
from pitwall.server import RADIO_CHECK, create_app
from pitwall.voice import Voice

from test_voice import FakeSynth, fake_model, wait_for


def test_prefs_validate_and_roundtrip(tmp_path):
    prefs = Prefs().updated({"voice": "female", "volume": 0.6, "calls": "urgent", "subtitles": False})
    assert (prefs.voice, prefs.volume, prefs.calls, prefs.subtitles) == ("female", 0.6, "urgent", False)
    for bad in ({"voice": "robot"}, {"volume": 1.5}, {"volume": True}, {"subtitles": "yes"}, {"colour": "red"}):
        with pytest.raises(ValueError):
            Prefs().updated(bad)
    path = tmp_path / "settings.json"
    prefs.save(path)
    assert Prefs.load(path) == prefs


def test_prefs_load_survives_missing_broken_and_bad_values(tmp_path):
    assert Prefs.load(tmp_path / "nope.json") == Prefs()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert Prefs.load(broken) == Prefs()
    partly = tmp_path / "partly.json"
    partly.write_text(json.dumps({"voice": "female", "volume": 9, "engine": "piper"}), encoding="utf-8")
    assert Prefs.load(partly) == Prefs(voice="female", engine="piper")


class Recorder:
    """Voice factory that hands out fake voices and remembers them."""

    def __init__(self, root):
        self.root = root
        self.voices = []
        self.played = []
        self.synth = FakeSynth()

    def __call__(self, prefs):
        v = Voice(prefs.voice, prefs.volume, root=self.root, engine=prefs.engine,
                  load=self.synth.load, player=self.played.append)
        self.voices.append(v)
        return v


class Src:
    name = "fake"

    def __init__(self):
        self.snap = None

    def read(self):
        return self.snap

    def close(self):
        pass


def test_settings_api_applies_live_saves_and_broadcasts(tmp_path):
    fake_model(tmp_path)
    voices = Recorder(tmp_path)
    settings = Settings(source="mock", prefs_path=tmp_path / "settings.json")
    app = create_app(settings, source=Src(), run_poller=False, voice_factory=voices)

    with TestClient(app) as client:
        first = voices.voices[0]
        wait_for(lambda: first.status == "ready")
        with client.websocket_connect("/ws") as ws:
            msgs = [ws.receive_json() for _ in range(5)]
            assert msgs[-2]["type"] == "settings" and msgs[-1]["type"] == "coach" and msgs[-2]["voice"] == "male"

            r = client.put("/api/settings", json={"volume": 0.5, "calls": "urgent", "subtitles": False})
            assert r.status_code == 200 and r.json()["volume"] == 0.5
            assert ws.receive_json()["calls"] == "urgent"  # pushed to every dashboard
        assert first.volume == 0.5 and len(voices.voices) == 1  # volume alone keeps the voice
        assert app.state.poller.max_priority == 1
        assert json.loads((tmp_path / "settings.json").read_text())["subtitles"] is False

        r = client.put("/api/settings", json={"voice": "female"})
        assert r.status_code == 200
        assert len(voices.voices) == 2 and voices.voices[1].voice_id == "af_heart"
        assert app.state.poller.voice is voices.voices[1]
        wait_for(lambda: voices.voices[1].status == "ready")

        assert client.put("/api/settings", json={"voice": "robot"}).status_code == 400
        assert client.get("/api/settings").json()["voice"] == "female"

        assert client.post("/api/voice/test").status_code == 200
        wait_for(lambda: voices.synth.rendered.count(RADIO_CHECK) >= 1 and voices.played)

        client.put("/api/settings", json={"voice": "off"})
        assert client.post("/api/voice/test").status_code == 409


def test_call_filter_keeps_urgent_calls_only():
    sent = []

    async def broadcast(msg):
        sent.append(msg)

    src = Src()
    ok = dict(tyre_pressure_psi=(26.5,) * 4, tyre_temp_c=(85,) * 4, status="live")
    src.snap = Snapshot(laps=1, best_ms=118_000, last_ms=118_000, **ok)
    now = [0.0]
    poller = Poller(src, broadcast, engine=EventEngine(load_thresholds()), radio=Radio(), clock=lambda: now[0])
    poller.max_priority = 1

    async def scenario():
        await poller.tick()
        src.snap = Snapshot(laps=2, best_ms=117_000, last_ms=117_000, flag="blue", **ok)  # PB + blue
        now[0] = 10
        await poller.tick()
        src.snap = Snapshot(laps=2, best_ms=117_000, last_ms=117_000, flag="yellow", **ok)
        now[0] = 20
        await poller.tick()

    asyncio.run(scenario())
    assert [m["kind"] for m in sent if m["type"] == "radio"] == ["flag_yellow"]


def test_volume_applies_at_play_time_without_re_rendering(tmp_path):
    fake_model(tmp_path)
    played = []

    def load(root, voice_id, volume):
        assert volume == 1.0  # cache is always full volume

        def synth(text, out):
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(np.full(100, 20000, np.int16).tobytes())

        return synth

    def player(path):
        with wave.open(str(path)) as w:
            played.append(int(np.frombuffer(w.readframes(w.getnframes()), np.int16).max()))

    v = Voice("male", volume=1.0, root=tmp_path, load=load, player=player)
    v.start()
    wait_for(lambda: v.status == "ready")
    v.say("Box, box.")
    wait_for(lambda: len(played) == 1)
    v.volume = 0.5
    v.say("Box, box.")
    wait_for(lambda: len(played) == 2)
    v.close()
    assert played == [20000, 10000]
