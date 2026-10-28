import random
from types import SimpleNamespace

from fastapi.testclient import TestClient

from pitwall import style
from pitwall.coach import StintCoach
from pitwall.config import Settings, load_thresholds
from pitwall.events import EventEngine
from pitwall.models import Snapshot
from pitwall.server import create_app
from pitwall.track import TrackMap, Turn

TURNS = {"test": [Turn("1", "Hairpin", 0.30, 0.34), Turn("2", "Kink", 0.70, 0.74)]}


def drive(e, tm, laps, session="practice", lock_on=(), flag_at=None):
    """Laps at 180 km/h on a 5 km track; a front lockup into the hairpin on lock_on laps.
    Returns (lap, lap_pos, kind) for every coach or tip call."""
    out, t, pos, lap, ms = [], 0.0, 0.05, 0, 0
    while lap < laps:
        locking = lap in lock_on and 0.285 <= pos <= 0.295
        flag = "chequered" if flag_at is not None and lap >= flag_at else "none"
        snap = Snapshot(status="live", track="test", session=session, laps=lap, lap_pos=pos, speed_kmh=180,
                        current_ms=ms, last_ms=100_000, tyre_temp_c=(80, 80, 88, 88), tyre_pressure_psi=(26.5,) * 4,
                        brake=1.0 if locking else 0.0, wheel_slip=(4.0, 1, 1, 1) if locking else (0.5,) * 4, flag=flag)
        tm.update(snap)
        tm.length_m = 5000.0
        out += [(lap, round(pos, 3), c.kind) for c in e.update(snap, t) if c.kind.startswith(("coach", "tip"))]
        t, ms, pos = t + 0.1, ms + 100, pos + 0.001
        if pos >= 1:
            pos, lap, ms = pos - 1, lap + 1, 0
    return out


def setup(**changes):
    th = load_thresholds()
    th["coach"].update(changes)
    tm = TrackMap(th, turns=TURNS)
    return EventEngine(th, track_map=tm, rng=random.Random(1)), tm


def test_braking_tip_after_three_clean_laps_on_a_straight():
    e, tm = setup()
    calls = [c for c in drive(e, tm, 7, lock_on={1, 2, 3, 4, 5, 6}) if c[2].startswith("coach")]
    # Lap 0 is joined halfway, laps 1-3 are clean (valid) with a lockup each: judged at the end of lap 3.
    assert calls == [(4, 0.0, "coach_braking")]  # said right after the line, 1.5 km before the hairpin
    lap, pos, _ = calls[0]
    assert not 0.30 <= pos <= 0.34 and not 0.70 <= pos <= 0.74  # on a straight
    assert [t["kind"] for t in e.stint.log if t["kind"].startswith("coach")] == ["coach_braking"]


def test_no_coach_in_a_race_when_muted_or_after_the_chequered_flag():
    e, tm = setup()
    assert not [c for c in drive(e, tm, 7, session="race", lock_on=set(range(7))) if c[2].startswith("coach")]
    e, tm = setup()
    e.coach_on = False
    assert not [c for c in drive(e, tm, 7, lock_on=set(range(7))) if c[2].startswith("coach")]
    assert e.stint.view()["laps"]  # still counting for the Coach screen
    e, tm = setup()
    assert not [c for c in drive(e, tm, 7, lock_on=set(range(7)), flag_at=4) if c[0] >= 4]  # nothing after the flag


def fake_turns(laps):
    return SimpleNamespace(laps=laps, passes=[], _track="test", _car="car", _session="practice")


def lap(n, time_s, **kw):
    base = {"lap": n, "stint": 1, "time_s": time_s, "valid": True, "pit": False, "partial": False, "lockups": 0,
            "wheelspin": 0, "cuts": 0, "front_c": 80.0, "rear_c": 88.0, "wet": False, "session": "practice"}
    return {**base, **kw}


def test_praise_once_a_stint_for_a_tidy_lap_near_the_best():
    th = load_thresholds()
    turns = fake_turns([lap(1, 100.0), lap(2, 100.5), lap(3, 99.9)])
    coach = StintCoach(th, turns, TrackMap(th, turns={}))
    snap = Snapshot(status="live", track="test")
    assert coach.update(snap) == []  # 99.9 is a new best: the best-lap call has that
    turns.laps.append(lap(4, 100.1))
    assert coach.update(snap) == ["coach_praise"]
    turns.laps.append(lap(5, 100.0))
    assert coach.update(snap) == []  # once a stint


def test_consistency_and_front_tyre_rules():
    th = load_thresholds()
    times = [100.0, 102.5, 99.0, 101.8, 98.6]
    turns = fake_turns([lap(i + 1, t, front_c=95.0 if i == 4 else 80.0) for i, t in enumerate(times)])
    coach = StintCoach(th, turns, TrackMap(th, turns={}))
    scores = coach._scores(turns.laps, turns.laps)
    assert scores["consistency"] > 1 and scores["tyres_front"] > 1 and scores["braking"] == 0


def test_style_summary_traits():
    passes = [{"index": i % 4, "lap": i // 4, "lockup": i < 4, "wheelspin": False, "valid": True, "brake_m": 40,
               "coast_s": 0.1, "corrections": 1, "wet_tyres": False, "time_s": 10.0} for i in range(20)]
    laps = [lap(n, 100 + n * 0.1) for n in range(1, 5)]
    out = style.summary(passes, laps, load_thresholds()["style"])
    levels = {t["key"]: t["level"] for t in out["traits"]}
    assert levels == {"braking": "watch", "throttle": "good", "steering": "good", "coasting": "good",
                      "limits": "good", "consistency": "good", "tyres": "good"}
    assert out["text"].startswith("Worth a look: locks the fronts.")
    assert style.summary(passes[:3], laps, load_thresholds()["style"])["traits"] == []


def test_sessions_api_lists_saved_sessions(tmp_path):
    passes = [{"index": 0, "turn": "1", "name": "Hairpin", "lap": n, "lockup": False, "wheelspin": False, "valid": True,
               "brake_m": 40, "min_kmh": 90, "full_m": -20, "coast_s": 0.1, "corrections": 1, "time_s": 10.0,
               "session": "practice"} for n in range(12)]
    style.save_session(tmp_path, passes, "Spa", "car", load_thresholds()["style"], laps=[lap(n, 140.0 + n) for n in range(4)])
    app = create_app(Settings(source="mock", coach_dir=tmp_path), source=None, run_poller=False)
    with TestClient(app) as client:
        [s] = client.get("/api/sessions").json()
    assert (s["track"], s["laps"], s["best_s"], s["clean_laps"]) == ("Spa", 4, 140.0, 4)
    assert s["style"]["traits"] and not s["tips_written"]
