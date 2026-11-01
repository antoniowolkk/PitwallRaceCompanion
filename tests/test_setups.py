import json
from pathlib import Path

import pytest

from pitwall import setups

REF = Path(__file__).parent.parent / "docs" / "reference" / "BGK_RACE.json"


def acc_copy(tmp_path: Path, car="ferrari_296_gt3", track="Spa") -> Path:
    """The reference setup saved the way ACC saves files: CRLF line breaks."""
    folder = tmp_path / car / track
    folder.mkdir(parents=True)
    text = setups.read_text(REF).replace("\r\n", "\n").replace("\n", "\r\n")
    path = folder / "BGK_RACE.json"
    path.write_bytes(text.encode("utf-8"))
    return path


def test_values_read_from_the_reference_setup():
    data = json.loads(REF.read_text(encoding="utf-8"))
    v = setups.values(data)
    assert v["rear_wing"] == 12 and v["brake_bias"] == 43 and v["ride_rear"] == 14 and v["ride_front"] == 0


def test_one_value_changes_and_every_other_byte_stays(tmp_path):
    base = acc_copy(tmp_path)
    before = base.read_bytes()
    out = setups.write_version(base, setups.PARAMS["rear_wing"], 13)
    assert out.name == "BGK_RACE_pw_v2.json"
    after = out.read_bytes()
    assert base.read_bytes() == before  # the base is never touched
    assert after.count(b"\r\n") == before.count(b"\r\n") and b"\t" in after
    assert after.replace(b'"rearWing": 13', b'"rearWing": 12') == before
    assert setups.values(json.loads(after))["rear_wing"] == 13


def test_list_values_keep_their_layout(tmp_path):
    base = acc_copy(tmp_path)
    out = setups.write_version(base, setups.PARAMS["ride_rear"], 15)
    assert b'"rideHeight": [ 0, 6, 15, 18 ]' in out.read_bytes()
    assert setups.values(json.loads(out.read_bytes()))["ride_rear"] == 15


def test_versions_count_up_and_never_overwrite(tmp_path):
    base = acc_copy(tmp_path)
    v2 = setups.write_version(base, setups.PARAMS["abs"], 3)
    v3 = setups.write_version(v2, setups.PARAMS["tc"], 4)  # building on a version keeps the base name
    assert (v2.name, v3.name) == ("BGK_RACE_pw_v2.json", "BGK_RACE_pw_v3.json")
    with pytest.raises(FileExistsError):
        with open(v3, "x"):
            pass


def test_setups_listed_per_car_and_track_ignoring_case(tmp_path):
    acc_copy(tmp_path, track="Spa")
    assert [p.name for p in setups.list_setups(tmp_path, "ferrari_296_gt3", "spa")] == ["BGK_RACE.json"]
    assert setups.list_setups(tmp_path, "ferrari_296_gt3", "monza") == []
    assert setups.list_setups(tmp_path, "bmw_m4_gt3", "Spa") == []
