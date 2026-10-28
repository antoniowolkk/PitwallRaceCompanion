import asyncio
import json
import random

from fastapi.testclient import TestClient

from pitwall import personal, style, wording
from pitwall.config import Settings, load_thresholds
from pitwall.events import EventEngine
from pitwall.models import Snapshot
from pitwall.server import create_app
from pitwall.track import TrackMap, Turn

STYLE = load_thresholds()["style"]


def rec(lap, time_s=15.0, brake_m=20, min_kmh=90, coast_s=0.2, full_m=-60, lockup=False, valid=True):
    return {"turn": "8", "name": "Bruxelles", "index": 4, "lap": lap, "time_s": time_s, "valid": valid,
            "lockup": lockup, "wheelspin": False, "entry_kmh": 170, "brake_m": brake_m, "peak_brake": 1.0,
            "min_kmh": min_kmh, "full_m": full_m, "coast_s": coast_s, "abs_s": 0.0, "session": "qualify",
            "brake_bias": 0.575, "abs_level": 2, "tc_level": 3, "engine_map": 1, "grip": "optimum",
            "rain": "none", "wet_tyres": False, "road_c": 31}


SESSION_PASSES = [
    rec(0, 15.6, brake_m=8, lockup=True),
    rec(1, 15.0),  # the best clean pass
    rec(2, 15.7, brake_m=10, lockup=True),
    rec(3, 15.5, coast_s=0.9),
    rec(4, 15.6, coast_s=0.8),
]


def test_patterns_find_repeated_habits_against_the_best_pass():
    [turn] = style.patterns(SESSION_PASSES, STYLE)
    habits = {p["pattern"]: p for p in turn["patterns"]}
    assert set(habits) == {"lockup", "coasting"}
    assert habits["lockup"]["fact"] == "locked a front wheel under braking on 2 of 5 passes, braking about 11 m later than on the best pass"
    assert habits["coasting"]["count"] == 2 and turn["best"]["time_s"] == 15.0
    assert style.patterns(SESSION_PASSES[:2], STYLE) == []  # too few passes to call it a habit


def fake_ollama(tips_per_call):
    sent = []

    def post(url, body):
        sent.append(body)
        if url.endswith("/api/chat"):
            return {"message": {"content": json.dumps({"tips": tips_per_call.pop(0) if tips_per_call else []})}}
        return {}

    return post, sent


def test_ai_lines_are_checked_and_missing_habits_retried(tmp_path):
    data = style.save_session(tmp_path, SESSION_PASSES, "Spa", "porsche_992_gt3_r", STYLE)
    post, sent = fake_ollama([
        [{"habit": "lockup", "line": "Brake about 10 metres earlier."},  # digits: rejected
         {"habit": "coasting", "line": "Get back on the power sooner, no coasting through there."}],
        [{"habit": "lockup", "line": "Lockups here, brake a touch earlier and squeeze it on."}],
    ])
    lines = personal.generate(data, post=post, report=lambda s: None)
    assert lines == {"8": {"coasting": "Get back on the power sooner, no coasting through there.",
                           "lockup": "Lockups here, brake a touch earlier and squeeze it on."}}
    prompt = sent[0]["messages"][1]["content"]
    assert "brake bias 57.5 percent front, ABS 2, TC 3" in prompt and "grip optimum" in prompt and "qualify at Spa" in prompt
    personal.save(tmp_path, data, lines, "qwen3:4b")
    assert personal.load(tmp_path, "Spa")["dry"]["8"]["lockup"].startswith("Lockups here")


def test_personal_line_replaces_canned_tip_in_matching_conditions(tmp_path):
    data = style.save_session(tmp_path, SESSION_PASSES, "test", "car", STYLE)
    personal.save(tmp_path, {**data, "track": "test"}, {"1": {"lockup": "Squeeze the brake, do not stamp on it."}}, "m")
    th = load_thresholds()
    tm = TrackMap(th, turns={"test": [Turn("1", "Hairpin", 0.30, 0.34)]})
    e = EventEngine(th, track_map=tm, rng=random.Random(1), personal_root=tmp_path)

    def run(rain):
        calls, pos, lap, ms, t = [], 0.05, 0, 0, 0.0
        while lap < 2:
            locking = lap == 0 and 0.285 <= pos <= 0.295
            snap = Snapshot(status="live", track="test", session="qualify", laps=lap, lap_pos=pos, speed_kmh=180,
                            current_ms=ms, tyre_temp_c=(85,) * 4, tyre_pressure_psi=(26.5,) * 4, rain=rain,
                            brake=1.0 if locking else 0.0, wheel_slip=(4.0, 1, 1, 1) if locking else (0.5,) * 4)
            tm.update(snap)
            tm.length_m = 5000.0
            calls += [c.text for c in e.update(snap, t) if c.kind.startswith("tip_")]
            t, ms, pos = t + 0.1, ms + 100, pos + 0.001
            if pos >= 1:
                pos, lap, ms = pos - 1, lap + 1, 0
        return calls

    assert run("none") == ["Coming up, Hairpin. Squeeze the brake, do not stamp on it."]
    e.reset()
    assert run("light") == ["Coming up, Hairpin. You locked up there last lap, brake a touch earlier."]  # wet: canned


def test_race_tips_only_big_mistakes_and_laps_apart():
    th = load_thresholds()
    tm = TrackMap(th, turns={"test": [Turn("1", "Hairpin", 0.30, 0.34)]})
    e = EventEngine(th, track_map=tm, rng=random.Random(1))
    said, pos, lap, ms, t = [], 0.05, 0, 0, 0.0
    while lap < 8:
        locking = 0.285 <= pos <= 0.295
        snap = Snapshot(status="live", track="test", session="race", laps=lap, lap_pos=pos, speed_kmh=180,
                        current_ms=ms, tyre_temp_c=(85,) * 4, tyre_pressure_psi=(26.5,) * 4,
                        brake=1.0 if locking else 0.0, wheel_slip=(4.0, 1, 1, 1) if locking else (0.5,) * 4)
        tm.update(snap)
        tm.length_m = 5000.0
        said += [lap for c in e.update(snap, t) if c.kind.startswith("tip_")]
        t, ms, pos = t + 0.1, ms + 100, pos + 0.001
        if pos >= 1:
            pos, lap, ms = pos - 1, lap + 1, 0
    assert said == [1, 4, 7]  # locks up every lap, told every third


def test_session_end_offers_tips_and_generate_runs_only_on_request(tmp_path, monkeypatch):
    app = create_app(Settings(source="mock", coach_dir=tmp_path), source=None, run_poller=False)
    desk, engine = app.state.desk, app.state.poller.engine
    engine.coach.finished.append({"passes": SESSION_PASSES, "track": "Spa", "car": "porsche_992_gt3_r"})
    asyncio.run(app.state.poller._sessions_ended())
    assert desk.offer["track"] == "Spa" and desk.offer["turns"][0]["habits"] == ["lockup", "coasting"]
    assert not (tmp_path / "tips").exists()  # nothing written until asked

    post, _ = fake_ollama([[{"habit": "lockup", "line": "Brake a touch earlier, it keeps locking."},
                            {"habit": "coasting", "line": "Less coasting, get back on the power."}]])
    desk._post = post
    monkeypatch.setattr(wording, "ollama_models", lambda: ["qwen3:4b"])
    with TestClient(app) as client:
        assert client.get("/api/coach").json()["offer"]["session"] == desk.offer["session"]
        sid = desk.offer["session"]
        assert client.post("/api/coach/generate", json={"session": "nope"}).status_code == 404
        assert client.post("/api/coach/generate", json={"session": sid}).status_code == 200
        for _ in range(50):
            if desk.done or desk.error:
                break
            asyncio.run(asyncio.sleep(0.05))
    assert desk.error == "" and desk.done == "2 personal tips ready for Spa (dry)." and desk.offer is None
    assert personal.load(tmp_path, "Spa")["dry"]["8"]["coasting"] == "Less coasting, get back on the power."
    assert style.latest_offer(tmp_path) is None  # tips written: not offered again


def test_dismiss_stops_the_offer(tmp_path):
    data = style.save_session(tmp_path, SESSION_PASSES, "Spa", "car", STYLE)
    app = create_app(Settings(source="mock", coach_dir=tmp_path), source=None, run_poller=False)
    assert app.state.desk.offer["session"] == data["id"]  # offered again after a restart
    with TestClient(app) as client:
        assert client.post("/api/coach/dismiss", json={"session": data["id"]}).json()["offer"] is None
    assert style.latest_offer(tmp_path) is None
