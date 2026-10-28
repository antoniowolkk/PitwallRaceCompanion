import io
import json
from enum import Enum
from types import SimpleNamespace

from pitwall.models import NO_TIME, Snapshot
from pitwall.telemetry.acc import BestLap, from_acc, hold_on_pause, on_track
from pitwall.telemetry.mock import MockSource
from pitwall.telemetry.replay import Recorder, ReplaySource


def run_laps(src: MockSource, laps: int) -> None:
    while src.laps < laps:
        src.step(1.0)


def test_mock_laps_look_like_gt3_laps():
    src = MockSource(seed=3, clock=None)
    run_laps(src, 3)
    snap = src.snapshot()
    assert 95_000 < snap.last_ms < 120_000
    assert snap.best_ms is None or snap.best_ms <= snap.last_ms or not snap.lap_valid


def test_mock_burns_fuel_and_warms_tyres():
    src = MockSource(seed=1, clock=None, start_fuel_l=30.0)
    run_laps(src, 2)
    snap = src.snapshot()
    assert 23.5 < snap.fuel_l < 24.5  # 2 laps at 2.9 L
    assert all(75 < t < 95 for t in snap.tyre_temp_c)
    assert all(25 < p < 28 for p in snap.tyre_pressure_psi)


def test_mock_is_deterministic_per_seed():
    a, b = MockSource(seed=7, clock=None), MockSource(seed=7, clock=None)
    run_laps(a, 2)
    run_laps(b, 2)
    assert a.last_ms == b.last_ms


def test_recorder_and_replay_roundtrip(tmp_path):
    src = MockSource(seed=2, clock=None)
    t = [0.0]
    buf = io.StringIO()
    rec = Recorder(buf, clock=lambda: t[0])
    for _ in range(20):
        src.step(0.1)
        rec.write(src.snapshot())
        t[0] += 0.1
    path = tmp_path / "laps.jsonl"
    path.write_text(buf.getvalue())
    assert json.loads(buf.getvalue().splitlines()[0])["t"] == 0.0

    now = [0.0]
    replay = ReplaySource(path, clock=lambda: now[0])
    first = replay.read()
    now[0] = 1.0
    later = replay.read()
    assert first.current_ms < later.current_ms
    now[0] = 2.05  # past the end: loops back to the start
    assert replay.read().current_ms < later.current_ms


class _Status(Enum):
    ACC_LIVE = 2


class _Session(Enum):
    ACC_RACE = 2


class _Flag(Enum):
    ACC_YELLOW_FLAG = 2


def _wheels(fl, fr, rl, rr):
    return SimpleNamespace(front_left=fl, front_right=fr, rear_left=rl, rear_right=rr)


def test_from_acc_maps_library_fields():
    physics = SimpleNamespace(
        speed_kmh=212.4, gear=4, rpm=7400, fuel=41.2, gas=0.8, brake=0.0, steer_angle=-0.1,
        wheel_slip=_wheels(0.1, 0.1, 0.3, 0.2),
        tyre_core_temp=_wheels(84, 82, 79, 80), wheel_pressure=_wheels(26.4, 26.3, 26.1, 26.2),
    )
    graphics = SimpleNamespace(
        status=_Status.ACC_LIVE, session_type=_Session.ACC_RACE, flag=_Flag.ACC_YELLOW_FLAG,
        fuel_per_lap=2.9, fuel_estimated_laps=14.2, completed_lap=5,
        current_time=34_000, last_time=104_321, best_time=NO_TIME,
        is_valid_lap=1, is_in_pit_lane=0, is_in_pit=0, session_time_left=1_800_000.0,
        mandatory_pit_done=0, missing_mandatory_pits=1,
        normalized_car_position=0.25, player_car_id=7, car_id=[3, 7, 9],
        car_coordinates=[SimpleNamespace(x=1, y=0, z=1), SimpleNamespace(x=-120.5, y=4.0, z=88.25),
                         SimpleNamespace(x=5, y=0, z=5)],
    )
    # Real ACC pads static strings with NULs.
    static = SimpleNamespace(
        car_model="ferrari_296_gt3" + "\x00" * 18, track="barcelona" + "\x00" * 24, max_rpm=8500, max_fuel=120.0,
        pit_window_start=1_200_000, pit_window_end=2_400_000,
    )
    snap = from_acc(physics, graphics, static)
    assert snap.car == "ferrari_296_gt3"
    assert snap.track == "barcelona"
    assert snap.status == "live"
    assert snap.session == "race"
    assert snap.flag == "yellow"
    assert snap.gear == "3"
    assert snap.tyre_temp_c == (84, 82, 79, 80)
    assert snap.last_ms == 104_321
    assert snap.best_ms is None
    assert (snap.pit_window_start, snap.pit_window_end, snap.missing_mandatory_pits) == (1_200_000, 2_400_000, 1)
    assert snap.to_wire()["session_time_left"] == 1_800_000
    assert (snap.lap_pos, snap.x, snap.y) == (0.25, -120.5, 88.25)  # our car, not car 0
    assert snap.throttle == 0.8 and snap.wheel_slip == (0.1, 0.1, 0.3, 0.2)


def test_pause_holds_last_live_values():
    live = Snapshot(status="live", fuel_l=61.0, gear="3", tyre_temp_c=(80, 81, 79, 78))
    zeroed = Snapshot(status="pause", fuel_l=0.0, gear="R")
    held = hold_on_pause(zeroed, live)
    assert held.status == "pause"
    assert held.fuel_l == 61.0
    assert held.gear == "3"
    # Nothing to hold yet (started while paused): pass through.
    assert hold_on_pause(zeroed, None) is zeroed
    assert hold_on_pause(live, None) is live


def test_best_lap_tracks_laps_acc_ignores():
    # Spa race, 2026-10-27: ACC kept best_time on the slow first lap all race.
    best = BestLap()
    acc_best = 226_786
    laps = [(1, 226_786), (2, 142_302), (3, 149_877), (4, 142_005), (5, 143_290)]
    snap = best.apply(Snapshot(session="race", track="Spa", laps=0))
    assert snap.best_ms is None
    for done, last in laps:
        best.apply(Snapshot(session="race", track="Spa", laps=done - 1, current_ms=100_000))
        snap = best.apply(Snapshot(session="race", track="Spa", laps=done, last_ms=last, best_ms=acc_best))
    assert snap.best_ms == 142_005


def test_best_lap_skips_invalid_laps_and_resets_per_session():
    best = BestLap()
    best.apply(Snapshot(session="practice", track="Spa", laps=0, lap_valid=False))
    snap = best.apply(Snapshot(session="practice", track="Spa", laps=1, last_ms=130_000))
    assert snap.best_ms is None  # cut the track, does not count
    best.apply(Snapshot(session="practice", track="Spa", laps=1))
    snap = best.apply(Snapshot(session="practice", track="Spa", laps=2, last_ms=141_000))
    assert snap.best_ms == 141_000
    snap = best.apply(Snapshot(session="race", track="Spa", laps=0))
    assert snap.best_ms is None


def test_empty_car_on_grid_screen_is_not_on_track():
    # Monza race, 2026-10-27: 30 s of status live with fuel 0, gear R, tyres 0 before Drive.
    assert not on_track(Snapshot(status="live", session="race", fuel_l=0.0, gear="R"))
    assert on_track(Snapshot(status="live", fuel_l=0.0, tyre_pressure_psi=(25.4, 25.5, 25.3, 25.4)))
