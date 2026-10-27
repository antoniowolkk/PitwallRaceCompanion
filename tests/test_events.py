import asyncio

from pitwall.config import load_thresholds
from pitwall.events import Call, EventEngine, Radio, load_calls, spoken_decimal, spoken_lap, words
from pitwall.models import Snapshot
from pitwall.poller import Poller

OK_TYRES = dict(tyre_temp_c=(84, 85, 86, 86), tyre_pressure_psi=(26.5, 26.5, 26.5, 26.5))


def snap(**kw) -> Snapshot:
    base = dict(status="live", fuel_l=50.0, fuel_per_lap_l=3.0, fuel_laps_left=16.0, **OK_TYRES)
    return Snapshot(**{**base, **kw})


def engine() -> EventEngine:
    return EventEngine(load_thresholds())


def kinds(calls: list[Call]) -> list[str]:
    return [c.kind for c in calls]


def test_every_call_line_formats():
    fields = dict(laps="three", tyre="front left", temp="ninety seven", psi="twenty seven point nine", time="one fifty one point zero")
    for kind, line in load_calls().items():
        assert line["priority"] in (1, 2, 3), kind
        text = line["text"].format(**fields)
        assert text[0].isupper() and text[-1] in ".!", kind


def test_flag_calls_on_change_with_cooldown():
    e = engine()
    assert kinds(e.update(snap(flag="none"), 0)) == []
    assert kinds(e.update(snap(flag="yellow"), 1)) == ["flag_yellow"]
    assert kinds(e.update(snap(flag="yellow"), 2)) == []  # still yellow, no repeat
    e.update(snap(flag="none"), 3)
    assert kinds(e.update(snap(flag="yellow"), 4)) == []  # flicker inside cooldown
    e.update(snap(flag="none"), 20)
    assert kinds(e.update(snap(flag="yellow"), 21)) == ["flag_yellow"]


def test_fuel_warns_once_then_critical_every_lap_and_rearms_after_refuel():
    e = engine()
    assert kinds(e.update(snap(fuel_laps_left=3.5, laps=10), 0)) == []
    calls = e.update(snap(fuel_laps_left=2.9, laps=10), 1)
    assert kinds(calls) == ["fuel_warn"] and "About three laps" in calls[0].text
    assert kinds(e.update(snap(fuel_laps_left=2.5, laps=10), 2)) == []
    assert kinds(e.update(snap(fuel_laps_left=1.4, laps=10), 3)) == ["fuel_critical"]
    assert kinds(e.update(snap(fuel_laps_left=1.3, laps=11), 4)) == []  # crossed near the line
    assert kinds(e.update(snap(fuel_laps_left=0.4, laps=12), 110)) == ["fuel_critical"]
    e.update(snap(fuel_laps_left=20.0, laps=12), 150)  # pit stop
    assert kinds(e.update(snap(fuel_laps_left=2.9, laps=25), 1500)) == ["fuel_warn"]


def test_no_fuel_call_without_acc_estimate():
    e = engine()
    assert kinds(e.update(snap(fuel_per_lap_l=0.0, fuel_laps_left=0.0), 0)) == []


def test_hot_tyre_needs_to_last_then_cools_down():
    e = engine()
    hot = dict(tyre_temp_c=(84, 85, 86, 97), tyre_pressure_psi=OK_TYRES["tyre_pressure_psi"])
    assert kinds(e.update(snap(**hot), 0)) == []
    assert kinds(e.update(snap(**hot), 5)) == []
    calls = e.update(snap(**hot), 10)
    assert kinds(calls) == ["tyre_hot"] and calls[0].text == "The rear right's running hot, ninety seven degrees. Go easy on it."
    assert kinds(e.update(snap(**hot), 60)) == []  # cooldown


def test_kerb_spike_does_not_call():
    e = engine()
    e.update(snap(tyre_temp_c=(84, 85, 86, 99), tyre_pressure_psi=OK_TYRES["tyre_pressure_psi"]), 0)
    e.update(snap(), 2)  # back in the window
    assert kinds(e.update(snap(tyre_temp_c=(84, 85, 86, 99), tyre_pressure_psi=OK_TYRES["tyre_pressure_psi"]), 11)) == []


def test_high_pressure_call():
    e = engine()
    high = dict(tyre_temp_c=OK_TYRES["tyre_temp_c"], tyre_pressure_psi=(27.4, 27.9, 27.4, 27.5))
    e.update(snap(**high), 0)
    calls = e.update(snap(**high), 10)
    assert [c.text for c in calls] == ["Pressures are a bit high. The front right's at twenty seven point nine."]


def test_personal_best_after_first_timed_laps_only():
    e = engine()
    e.update(snap(laps=1, best_ms=118_150, last_ms=118_150), 0)
    calls = e.update(snap(laps=2, best_ms=117_822, last_ms=117_822), 1)
    assert [c.text for c in calls] == ["Nice one, personal best, one fifty seven point eight."]
    assert kinds(e.update(snap(laps=3, best_ms=117_822, last_ms=119_000), 2)) == []


def test_silent_while_paused():
    e = engine()
    assert e.update(snap(status="pause", flag="yellow", fuel_laps_left=0.5), 0) == []


def test_radio_urgent_first_spacing_and_expiry():
    r = Radio(spacing_s=4, expire_s=15)
    info = Call("best_lap", 3, "Personal best.", 0)
    blue = Call("flag_blue", 2, "Blue flag.", 0)
    r.push([info, blue])
    assert r.next(0).kind == "flag_blue"
    assert r.next(1) is None  # spacing
    r.push([Call("flag_yellow", 1, "Yellow.", 2)])
    assert r.next(2).kind == "flag_yellow"  # urgent skips the gap
    assert r.next(5) is None  # gap restarts after the yellow call
    assert r.next(6).kind == "best_lap"
    r.push([Call("best_lap", 3, "Personal best.", 10)])
    assert r.next(26) is None  # went stale while nothing could be said


def test_poller_sends_radio_calls_and_keeps_log():
    class Src:
        name = "fake"
        snap = snap(flag="none")

        def read(self):
            return self.snap

        def close(self):
            pass

    sent = []
    now = [0.0]

    async def broadcast(msg):
        sent.append(msg)

    src = Src()
    poller = Poller(src, broadcast, engine=engine(), radio=Radio(), clock=lambda: now[0])

    async def scenario():
        await poller.tick()
        src.snap = snap(flag="yellow")
        now[0] = 1.0
        await poller.tick()

    asyncio.run(scenario())
    radio = [m for m in sent if m["type"] == "radio"]
    assert [m["kind"] for m in radio] == ["flag_yellow"]
    assert radio[0]["priority"] == 1 and radio[0]["lap"] == 1
    assert list(poller.radio_log) == radio


def test_numbers_are_spoken_like_an_engineer():
    assert words(3) == "three" and words(97) == "ninety seven" and words(40) == "forty"
    assert words(105) == "one hundred and five"
    assert spoken_decimal(27.6) == "twenty seven point six"
    assert spoken_lap(110_260) == "one fifty point two"
    assert spoken_lap(125_311) == "two oh five point three"
    assert spoken_lap(142_005) == "two twenty two point zero"
