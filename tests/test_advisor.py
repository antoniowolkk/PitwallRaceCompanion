import dataclasses
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from pitwall import setups
from pitwall.advisor import SetupAdvisor, symptoms
from pitwall.config import Settings, load_thresholds
from pitwall.models import Snapshot
from pitwall.server import create_app
from test_setups import acc_copy

C = load_thresholds()["setup"]
SNAP = Snapshot(status="live", car="ferrari_296_gt3", track="Spa")


def lap(n):
    return {"lap": n, "stint": 1, "time_s": 140.0, "valid": True, "pit": False, "partial": False,
            "lockups": 0, "wheelspin": 0, "cuts": 0, "front_c": 80.0, "rear_c": 88.0, "wet": False}


def passes(n, entry, mid=0.5, exit_=0.5, min_kmh=100, turns=4):
    return [{"lap": n, "index": i, "min_kmh": min_kmh, "wheelspin": False,
             "bal_entry": entry, "bal_mid": mid, "bal_exit": exit_} for i in range(turns)]


def drive(adv, turns, laps, entry, **kw):
    """Add laps (with their passes) and let the advisor judge each one."""
    for _ in range(laps):
        n = len(turns.laps)
        turns.passes += passes(n, entry, **kw)
        turns.laps.append(lap(n))
        adv.update(SNAP)


def setup(tmp_path):
    base = acc_copy(tmp_path / "acc")
    turns = SimpleNamespace(laps=[], passes=[])
    adv = SetupAdvisor(load_thresholds(), turns, tmp_path / "data", tmp_path / "acc")
    adv.update(SNAP)
    return adv, turns, base


def test_symptoms_from_balance():
    found = symptoms(passes(1, -1.0) + passes(2, -1.0), C)
    assert found["entry_oversteer"]["strength"] == 2.0 and found["entry_oversteer"]["unit"] == "deg"
    assert symptoms(passes(1, -1.0)[:2], C) == {}  # too few samples
    fast = symptoms(passes(1, 0.0, mid=2.0, min_kmh=200) + passes(2, 0.0, mid=2.0, min_kmh=200), C)
    assert fast["fast_understeer"]["strength"] > 1 and "slow_understeer" not in fast


def test_no_proposal_without_base_or_enough_clean_laps(tmp_path):
    adv, turns, _ = setup(tmp_path)
    drive(adv, turns, 3, entry=-1.0)
    assert adv.draft is None and "Pick the setup" in adv.note
    adv.set_base("BGK_RACE.json")
    adv.state["current"] = "BGK_RACE.json"
    turns.laps.clear(), turns.passes.clear()
    adv.update(SNAP)
    drive(adv, turns, 2, entry=-1.0)
    assert adv.draft is None and adv.note == "Collecting: 2 of 3 clean laps."


def test_entry_oversteer_proposes_rear_wing_apply_load_and_check(tmp_path):
    adv, turns, base = setup(tmp_path)
    adv.set_base("BGK_RACE.json")
    drive(adv, turns, 3, entry=-1.0)
    d = adv.draft
    assert (d["symptom"], d["param"], d["from"], d["to"]) == ("entry_oversteer", "rear_wing", 12, 13)
    assert (tmp_path / "data" / "setup" / "drafts").exists()  # rewritten every lap, in Pit Wall's folder

    name = adv.apply()
    assert name == "BGK_RACE_pw_v2.json" and adv.state["waiting"] and adv.draft is None
    assert setups.values(json.loads((base.parent / name).read_bytes()))["rear_wing"] == 13
    drive(adv, turns, 3, entry=-1.0)
    assert adv.draft is None and "Load BGK_RACE_pw_v2.json" in adv.note  # one change at a time

    adv.loaded()
    drive(adv, turns, 3, entry=-0.2)  # better on the new setup
    assert adv.state["history"][-1]["result"] == "better" and adv.note.startswith("Better after the change")


def test_worse_suggests_going_back_and_reject_hides_a_change(tmp_path):
    adv, turns, _ = setup(tmp_path)
    adv.set_base("BGK_RACE.json")
    drive(adv, turns, 3, entry=-1.0)
    adv.apply()
    adv.loaded()
    drive(adv, turns, 3, entry=-1.6)
    assert adv.state["history"][-1]["result"] == "worse" and "Go back to BGK_RACE.json" in adv.note
    drive(adv, turns, 2, entry=-1.6)  # still on the worse version: no click on top of it (Monza, 2026-11-01)
    assert adv.draft is None and "Go back to BGK_RACE.json" in adv.note
    adv.go_back()
    assert adv.state["current"] == "BGK_RACE.json" and adv.state["waiting"]
    adv.loaded()

    drive(adv, turns, 3, entry=-1.0)
    assert adv.draft["param"] == "ride_rear" and adv.draft["step"] == -1  # rear wing +1 made it worse: next rule
    adv.reject()
    assert adv.draft is None


def test_setup_api_picks_base_and_applies(tmp_path):
    acc_copy(tmp_path / "acc")
    app = create_app(Settings(source="mock", coach_dir=tmp_path / "data", setups_dir=tmp_path / "acc"),
                     source=None, run_poller=False)
    adv = app.state.poller.engine.setup
    adv.update(SNAP)
    with TestClient(app) as client:
        assert client.get("/api/setup").json()["files"] == ["BGK_RACE.json"]
        assert client.post("/api/setup/base", json={"file": "nope.json"}).status_code == 400
        view = client.post("/api/setup/base", json={"file": "BGK_RACE.json"}).json()
        assert view["current"] == "BGK_RACE.json" and view["values"]["rear_wing"]["value"] == 12
        assert client.post("/api/setup/apply").status_code == 400  # no draft yet


def test_new_proposal_is_announced_once_on_a_straight(tmp_path):
    import random

    from pitwall.events import EventEngine
    from pitwall.track import TrackMap

    acc_copy(tmp_path / "acc")
    th = load_thresholds()
    tm = TrackMap(th, turns={})
    e = EventEngine(th, track_map=tm, rng=random.Random(1), personal_root=tmp_path / "data", setups_root=tmp_path / "acc")
    adv, turns = e.setup, e.coach
    snap = Snapshot(status="live", car="ferrari_296_gt3", track="Spa", session="qualify", tyre_temp_c=(85,) * 4,
                    tyre_pressure_psi=(26.5,) * 4)
    adv.update(snap)
    adv.set_base("BGK_RACE.json")
    drive(adv, turns, 3, entry=-1.0)
    said = [c.text for c in e.update(snap, 1.0) if c.kind == "coach_setup"]
    assert said == ["Setup idea for when you pit. You've got oversteer on corner entry, so rear wing one click up, "
                    "twelve to thirteen. It's on the Setup screen."]
    drive(adv, turns, 1, entry=-1.0)  # same proposal next lap: not said again
    assert not [c for c in e.update(snap, 20.0) if c.kind == "coach_setup"]


def test_proposal_applied_before_it_was_said_is_not_said(tmp_path):
    import random

    from pitwall.events import EventEngine
    from pitwall.track import TrackMap

    acc_copy(tmp_path / "acc")
    th = load_thresholds()
    e = EventEngine(th, track_map=TrackMap(th, turns={}), rng=random.Random(1), personal_root=tmp_path / "data",
                    setups_root=tmp_path / "acc")
    adv, turns = e.setup, e.coach
    snap = Snapshot(status="live", car="ferrari_296_gt3", track="Spa", session="race", tyre_temp_c=(85,) * 4,
                    tyre_pressure_psi=(26.5,) * 4)
    adv.update(snap)
    adv.set_base("BGK_RACE.json")
    drive(adv, turns, 3, entry=-1.0)
    assert not [c for c in e.update(snap, 1.0) if c.kind == "coach_setup"]  # race: held back
    adv.apply()  # the driver applied it from the Setup screen (Monza, 2026-11-01)
    quali = dataclasses.replace(snap, session="qualify")
    assert not [c for c in e.update(quali, 30.0) if c.kind == "coach_setup"]
