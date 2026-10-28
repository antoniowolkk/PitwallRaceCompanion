import json
import random

from fastapi.testclient import TestClient

from pitwall import wording
from pitwall.config import Settings, load_thresholds
from pitwall.events import EventEngine, load_calls
from pitwall.models import Snapshot
from pitwall.server import create_app, warm_lines

FUEL = "Heads up, fuel's getting low. About {laps} laps left in the tank."


def test_check_keeps_radio_ready_lines_only():
    ok = wording.check('  "Fuel is getting tight, about {laps} laps to go."  ', FUEL)
    assert ok == "Fuel is getting tight, about {laps} laps to go."
    for bad in [
        "Fuel is low, about 3 laps left.",            # digit
        "Fuel is low.",                                # lost the placeholder
        "Fuel is low, {laps} laps, {tyre} too.",      # new placeholder
        "fuel's low, about {laps} laps left.",         # lower-case start
        "Fuel's low, about {laps} laps left",          # no full stop
        "Tell him fuel is low, {laps} laps.",          # pronoun
        "Fuel " + "very " * 20 + "low {laps}.",        # too long
        "Fuel is low {laps.",                          # broken brace
    ]:
        assert wording.check(bad, FUEL) is None, bad


def fake_ollama(replies):
    """A post() stand-in: answers /api/chat with the next canned reply."""
    sent = []

    def post(url, body):
        sent.append((url, body))
        if url.endswith("/api/chat"):
            return {"message": {"content": json.dumps({"lines": replies.pop(0) if replies else []})}}
        return {}

    return post, sent


def test_ask_sends_think_off_and_json_schema():
    post, sent = fake_ollama([["Box this lap, fuel's critical."]])
    assert wording.ask("Box, box. Fuel's critical, pit this lap.", 3, "qwen3:4b", post) == ["Box this lap, fuel's critical."]
    url, body = sent[0]
    assert url.endswith("/api/chat") and body["think"] is False and body["format"]["required"] == ["lines"]
    assert "3 different ways" in body["messages"][1]["content"]


def test_generate_filters_and_retries_and_unload():
    calls = {"fuel_warn": {"priority": 2, "text": FUEL}}
    post, sent = fake_ollama([
        ["Fuel is low, 3 laps.", "Fuel's getting tight, {laps} laps to go."],  # one bad, one good
        ["Keep an eye on fuel, {laps} laps left.", "Fuel's getting tight, {laps} laps to go."],  # retry; dupe dropped
    ])
    data = wording.generate(calls, n=4, model="qwen3:4b", post=post, report=lambda s: None)
    assert data["variants"]["fuel_warn"] == ["Fuel's getting tight, {laps} laps to go.", "Keep an eye on fuel, {laps} laps left."]
    assert len([u for u, _ in sent if u.endswith("/api/chat")]) == 2
    wording.unload("qwen3:4b", post)
    assert sent[-1] == (f"{wording.OLLAMA}/api/generate", {"model": "qwen3:4b", "keep_alive": 0})


def test_load_drops_stale_and_broken_lines(tmp_path):
    path = tmp_path / "lines.json"
    assert wording.load(path, load_calls()) == {}
    wording.save({"variants": {
        "fuel_warn": ["Fuel's getting tight, {laps} laps to go.", "Old line without placeholder."],
        "gone_kind": ["Whatever."],
    }}, path)
    assert wording.load(path, load_calls()) == {"fuel_warn": ["Fuel's getting tight, {laps} laps to go."]}
    path.write_text("{oops", encoding="utf-8")
    assert wording.load(path, load_calls()) == {}


def test_engine_varies_lines_and_never_repeats_back_to_back():
    variants = {"flag_yellow": ["Yellow, yellow. Careful out there.", "Yellow flag ahead, no passing."]}
    e = EventEngine(load_thresholds(), variants=variants, rng=random.Random(1))
    said = []
    for i in range(12):
        e.update(Snapshot(status="live", flag="none", tyre_pressure_psi=(26.5,) * 4, tyre_temp_c=(85,) * 4), i * 20)
        calls = e.update(Snapshot(status="live", flag="yellow", tyre_pressure_psi=(26.5,) * 4, tyre_temp_c=(85,) * 4), i * 20 + 1)
        said.append(calls[0].text)
    assert len(set(said)) == 3  # canned + both variants
    assert all(a != b for a, b in zip(said, said[1:]))


def test_warm_lines_include_fixed_variants_and_filled_fuel_lines():
    variants = {"flag_yellow": ["Yellow, yellow. Careful out there."], "fuel_warn": ["Fuel's tight, {laps} to go."]}
    lines = warm_lines(Settings(source="mock"), variants)
    assert "Yellow, yellow. Careful out there." in lines
    assert "Fuel's tight, three to go." in lines
    assert lines.index("Yellow, yellow. Careful out there.") < lines.index("Fuel's tight, three to go.")  # urgent first


def test_lite_mode_switches_variants_off_live():
    variants = {"flag_yellow": ["Yellow, yellow. Careful out there."]}
    app = create_app(Settings(source="mock", variants=variants), source=None, run_poller=False)
    poller = app.state.poller
    with TestClient(app) as client:
        assert client.get("/api/status").json()["ai"] == "ready"
        assert client.get("/api/settings").json()["ai_lines"] == 1
        assert client.put("/api/settings", json={"lite": True}).status_code == 200
        assert poller.engine.variants == {} and client.get("/api/status").json()["ai"] == "lite"
        client.put("/api/settings", json={"lite": False})
        assert poller.engine.variants == variants and poller.ai_status == "ready"


def test_check_rejects_what_the_first_real_run_got_wrong():
    for bad, original in [
        ("Chequered flag! Nice lap, take the win.", "Chequered flag! Good job, bring it home."),
        ("Your {tyre} is hot at {temp}. Drive slow.", "The {tyre}'s running hot, {temp} degrees. Go easy on it."),
        ("Orange flag. Car issue - fix it quickly.", "Orange flag. Something's up with the car, check it."),
        ("Orange flag. Check car, problem detected.", "Orange flag. Something's up with the car, check it."),
        ("Window closes {minutes} from now, stop and box ASAP.", "Window closes in about {minutes}, we still need to stop. Box soon."),
        ("Window closes in {minutes}, need to stop soon � box now!", "Window closes in about {minutes}, we still need to stop. Box soon."),
        ("Pressures a bit high. {tyre} at {psi}.", "Pressures are a bit high. The {tyre}'s at {psi}."),
        ("Great call, personal best {time}.", "Nice one, personal best, {time}."),
    ]:
        assert wording.check(bad, original) is None, bad
    assert wording.check("Yellow flag, stay back. No overtaking.", "Yellow flag, careful. No overtaking.")


def test_check_rejects_garbled_and_wrong_meaning_lines_from_second_run():
    assert wording.check("Yellow flag, be careful. No overt: overtake.", "Yellow flag, careful. No overtaking.") is None
    for bad in ("Stop's complete, good job. Window's secure.", "Stop's completed, great effort. Window's closed."):
        assert wording.check(bad, "Stop's done, nice work. Window's covered.") is None
    assert wording.check("Stop's done, well done. Window's covered.", "Stop's done, nice work. Window's covered.")
