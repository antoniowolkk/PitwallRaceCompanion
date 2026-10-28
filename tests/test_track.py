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
    assert len(ready["points"]) == BINS and ready["source"] == "auto"
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


def test_lovely_turns_merge_parts_and_handle_marker_only():
    from pitwall.track import lovely_turns

    data = {"length": 5000, "turn": [
        {"name": "Rettifilo", "start": 0.15, "end": 0.16},
        {"name": "Rettifilo", "start": 0.165, "end": 0.18},
        {"name": "Curva Grande", "start": 0.22, "end": 0.31},
        {"name": "Snake", "marker": 0.5},  # apex only: 50 m either side
    ]}
    assert [(t.number, t.name, t.start, t.end) for t in lovely_turns(data)] == [
        ("1-2", "Rettifilo", 0.15, 0.18), ("3", "Curva Grande", 0.22, 0.31), ("4", "Snake", 0.49, 0.51)]


def test_download_lovely_saves_by_file_name_and_map_uses_it(tmp_path):
    from pitwall.track import LOVELY, download_lovely

    files = {
        LOVELY + "manifest.json": {"tracks": {"assettocorsacompetizione": [
            {"trackName": "Nurburgring GP", "trackId": "murburgring", "path": "assettocorsacompetizione/nurburgring.json"}]}},
        LOVELY + "assettocorsacompetizione/nurburgring.json": {"length": 5137, "turn": [{"name": "Castrol S", "start": 0.05, "end": 0.08}]},
    }
    assert download_lovely(tmp_path, get=files.__getitem__, report=lambda s: None) == 1
    m = TrackMap(load_thresholds(), root=tmp_path, turns={})
    msg = m.update(Snapshot(status="live", track="nurburgring", laps=0))
    assert msg["source"] == "lovely" and msg["turns"][0]["name"] == "Castrol S" and msg["length_m"] == 5137
