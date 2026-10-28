import asyncio

from fastapi.testclient import TestClient

from pitwall.config import Settings
from pitwall.models import Snapshot
from pitwall.poller import Poller
from pitwall.server import create_app


class FakeSource:
    name = "fake"

    def __init__(self):
        self.snap: Snapshot | None = None

    def read(self):
        return self.snap

    def close(self):
        pass


def test_poller_tracks_connection_and_sends_only_changes():
    src = FakeSource()
    sent = []

    async def broadcast(msg):
        sent.append(msg)

    poller = Poller(src, broadcast)

    async def scenario():
        await poller.tick()  # no game
        src.snap = Snapshot(speed_kmh=100)
        await poller.tick()  # connects, full snapshot
        await poller.tick()  # nothing changed
        src.snap = Snapshot(speed_kmh=120)
        await poller.tick()
        src.snap = None
        await poller.tick()  # game gone

    asyncio.run(scenario())
    kinds = [(m["type"], m.get("acc")) for m in sent]
    assert kinds == [
        ("status", "connected"),
        ("telemetry", None),
        ("telemetry", None),
        ("status", "waiting"),
    ]
    assert sent[2]["data"] == {"speed_kmh": 120}


def test_poller_survives_read_errors():
    class Broken(FakeSource):
        def read(self):
            raise OSError("map gone")

    sent = []

    async def broadcast(msg):
        sent.append(msg)

    asyncio.run(Poller(Broken(), broadcast).tick())
    assert sent == []


def test_websocket_sends_config_status_and_snapshot():
    src = FakeSource()
    app = create_app(Settings(source="mock"), source=src, run_poller=False)
    src.snap = Snapshot(speed_kmh=180, car="ferrari_296_gt3")
    asyncio.run(app.state.poller.tick())

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            cfg = ws.receive_json()
            status = ws.receive_json()
            tele = ws.receive_json()
        assert cfg["type"] == "config" and cfg["tyre_temp_c"] == [80.0, 90.0]
        assert status == {"type": "status", "acc": "connected", "source": "fake", "reason": "", "ai": "missing", "voice": "off"}
        assert tele["data"]["speed_kmh"] == 180

        assert client.get("/").status_code == 200
        assert client.get("/api/status").json()["acc"] == "connected"


def test_acc_unavailable_off_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    app = create_app(Settings(source="acc"), run_poller=False)
    status = app.state.poller.status()
    assert status["acc"] == "unavailable"
    assert "Windows" in status["reason"]


def test_cli_parses_every_command():
    # Importing and running the CLI parser catches errors the other tests never reach.
    import pytest

    from pitwall.__main__ import main

    for argv in (["--help"], ["record", "--help"], ["voices", "--help"], ["lines", "--help"], ["tracks", "--help"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 0
