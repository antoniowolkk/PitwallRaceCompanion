import random

from pitwall.config import load_thresholds
from pitwall.events import EventEngine, spoken_loss, spoken_turn
from pitwall.models import Snapshot
from pitwall.track import TrackMap, Turn

TURNS = {"test": [Turn("1", "Hairpin", 0.30, 0.34), Turn("2", "", 0.70, 0.74)]}
LENGTH = 5000.0  # 50 m/s -> 100 s laps, 5 m per 0.1 s sample


def setup(tips=True):
    th = load_thresholds()
    tm = TrackMap(th, turns=TURNS)
    e = EventEngine(th, track_map=tm, rng=random.Random(1))
    e.tips = tips
    return tm, e


def drive(tm, e, laps, lock_on=(), slow_on=()):
    """Laps at 180 km/h; a lockup before the hairpin on lock_on laps, half speed through it on slow_on.
    Returns (lap, lap_pos, call) for every call."""
    out, t, pos, lap, ms = [], 0.0, 0.05, 0, 0
    while lap < laps:
        in_zone = 0.27 <= pos <= 0.35
        step = 0.001 * (0.5 if lap in slow_on and in_zone else 1.0)
        locking = lap in lock_on and 0.285 <= pos <= 0.295
        snap = Snapshot(
            status="live", track="test", laps=lap, lap_pos=pos, speed_kmh=180, current_ms=ms, last_ms=100_000,
            tyre_temp_c=(85,) * 4, tyre_pressure_psi=(26.5,) * 4, brake=1.0 if locking else 0.0,
            wheel_slip=(4.0, 1.0, 1.0, 1.0) if locking else (0.5,) * 4,
        )
        if tm.update(snap) is not None or not tm.length_m:
            tm.length_m = LENGTH
        for call in e.update(snap, t):
            out.append((lap, round(pos, 3), call))
        t, ms, pos = t + 0.1, ms + 100, pos + step
        if pos >= 1:
            pos, lap, ms = pos - 1, lap + 1, 0
    return out


def test_lockup_is_mentioned_once_before_the_turn_next_lap():
    tm, e = setup()
    calls = drive(tm, e, laps=4, lock_on={0})
    tips = [(lap, pos, c.text) for lap, pos, c in calls if c.kind.startswith("tip_")]
    assert len(tips) == 1
    lap, pos, text = tips[0]
    assert lap == 1 and 0.25 <= pos < 0.30  # ~250 m (5 s at 180 km/h) before the hairpin, not in it
    assert text == "Coming up, Hairpin. You locked up there last lap, brake a touch earlier."
    assert e.prepare == [text]  # handed to the voice a lap early, to render in time


def test_slow_pass_against_best_gives_the_time_lost():
    tm, e = setup()
    calls = drive(tm, e, laps=3, slow_on={1})
    tips = [(lap, c) for lap, _, c in calls if c.kind.startswith("tip_")]
    assert [(lap, c.kind) for lap, c in tips] == [(2, "tip_slow")]
    assert tips[0][1].text == "Coming up, Hairpin. You dropped eight seconds there last lap, compared to your best."


def test_tips_off_says_nothing_and_renders_nothing():
    tm, e = setup(tips=False)
    calls = drive(tm, e, laps=3, lock_on={0})
    assert not [c for _, _, c in calls if c.kind.startswith("tip_")] and e.prepare == []


def test_same_turn_not_two_laps_running():
    tm, e = setup()
    calls = drive(tm, e, laps=5, lock_on={0, 1, 2, 3})
    laps = [lap for lap, _, c in calls if c.kind.startswith("tip_")]
    assert laps == [1, 3]


def test_spoken_helpers():
    assert spoken_loss(0.3) == "three tenths" and spoken_loss(0.5) == "half a second"
    assert spoken_loss(1.0) == "a second" and spoken_loss(3.0) == "three seconds" and spoken_loss(1.4) == "one point four seconds"
    assert spoken_turn(Turn("5-6", "", 0, 0)) == "turn five" and spoken_turn(Turn("1", "La Source", 0, 0)) == "La Source"
