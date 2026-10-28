import asyncio

from fastapi.testclient import TestClient

from pitwall.config import Settings, load_thresholds
from pitwall.models import Snapshot
from pitwall.server import create_app
from pitwall.telemetry.mock import MockSource
from pitwall.track import BINS, TrackMap, find_turns, load_turns


def drive(track_map, laps=2.2, dt=0.1):
    """Mock laps through the map; returns the track messages it sent."""
    src = MockSource(clock=None)
    sent = []
    for _ in range(int(laps * 105 / dt)):
        src.step(dt)
        msg = track_map.update(src.snapshot())
        if msg:
            sent.append(msg)
    return sent


def test_official_turns_are_in_lap_order():
    spa = load_turns()["Spa"]
    assert [t.number for t in spa][:3] == ["1", "2-4", "5-6"] and spa[-1].name == "Bus Stop"
    for a, b in zip(spa, spa[1:]):
        assert 0 <= a.start < a.end <= b.start < b.end <= 1


def test_find_turns_at_speed_dips():
    speeds = [250.0] * 100
    for i, v in zip(range(20, 31), [200, 160, 120, 90, 80, 85, 110, 150, 190, 230, 250]):
        speeds[i] = v
    speeds[70] = 240  # a 10 km/h lift is not a turn
    turns = find_turns(speeds, drop_kmh=25, window=0.03, half_m=60, length_m=5000)
    assert [(t.number, t.start, t.end) for t in turns] == [("1", 0.228, 0.252)]


def test_learns_outline_from_one_lap_saves_and_reloads(tmp_path):
    th = load_thresholds()
    m = TrackMap(th, root=tmp_path, turns={})
    sent = drive(m)
    assert sent[0]["points"] is None and sent[0]["track"] == "mock_circuit"  # known track, no outline yet
    ready = sent[-1]
    assert len(ready["points"]) == BINS and not ready["official"]
    assert 4400 < ready["length_m"] < 5000  # mock lap is ~4.65 km
    assert len(ready["turns"]) >= 6 and ready["turns"][0]["number"] == "1"
    assert (tmp_path / "mock_circuit.json").exists()

    again = TrackMap(th, root=tmp_path, turns={})
    first = again.update(MockSource(clock=None).snapshot())
    assert first["points"] == ready["points"] and first["turns"] == ready["turns"]


def test_pit_lane_and_grid_do_not_draw_the_line():
    m = TrackMap(load_thresholds(), turns={})
    m.update(Snapshot(status="live", track="x", laps=0, lap_pos=0.5, speed_kmh=60, in_pit_lane=True, x=1, y=1))
    m.update(Snapshot(status="live", track="x", laps=0, lap_pos=0.6, speed_kmh=5, x=2, y=2))
    assert all(p is None for p in m._xy)


def test_websocket_sends_track_on_connect():
    app = create_app(Settings(source="mock"), source=None, run_poller=False)
    poller = app.state.poller
    poller.source = MockSource(clock=None)

    async def run():
        for _ in range(int(2.2 * 1050)):
            poller.source.step(0.1)
            await poller.tick()

    asyncio.run(run())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        msgs = [ws.receive_json() for _ in range(4)]
    track = next(m for m in msgs if m["type"] == "track")
    assert track["track"] == "mock_circuit" and len(track["points"]) == BINS
