from pitwall.models import NO_TIME, Snapshot, diff, gear_label, lap_ms


def test_gear_label_follows_acc_convention():
    assert [gear_label(g) for g in range(4)] == ["R", "N", "1", "2"]


def test_lap_ms_treats_int_max_and_zero_as_no_time():
    assert lap_ms(NO_TIME) is None
    assert lap_ms(0) is None
    assert lap_ms(101_234) == 101_234


def test_diff_sends_everything_first_then_only_changes():
    a = Snapshot(speed_kmh=100.2, fuel_l=30.04).to_wire()
    b = Snapshot(speed_kmh=100.4, fuel_l=29.96).to_wire()  # same after rounding
    c = Snapshot(speed_kmh=151.0, fuel_l=29.96).to_wire()
    assert diff(None, a) == a
    assert diff(a, b) == {}
    assert diff(b, c) == {"speed_kmh": 151}


def test_wire_roundtrip():
    snap = Snapshot(car="ferrari_296_gt3", tyre_temp_c=(80.0, 81.0, 82.0, 83.0), best_ms=100_000)
    back = Snapshot.from_wire(snap.to_wire())
    assert back.car == "ferrari_296_gt3"
    assert back.tyre_temp_c == (80.0, 81.0, 82.0, 83.0)
    assert back.best_ms == 100_000
