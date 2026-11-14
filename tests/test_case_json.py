"""The proposed per-screenshot .json (hash plus device), next to or instead of the .txt."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html.capture import (HASH_MISMATCH, HASH_MISSING, HASH_OK, HASH_UNREADABLE,  # noqa: E402
                                          load_capture)
from chat_capture_to_html.cli import main  # noqa: E402

UDID = make_fixture.DEVICE["udid"]


def _shot(folder, stem, data=b"png", js=None, txt=None):
    (folder / f"{stem}.png").write_bytes(data)
    if js is not None:
        (folder / f"{stem}.json").write_text(js if isinstance(js, str) else json.dumps(js), encoding="utf-8")
    if txt is not None:
        (folder / f"{stem}.txt").write_text(txt)


def test_json_verifies_and_names_the_device(tmp_path):
    folder = make_fixture.make(tmp_path, "imessage", sidecar="json")
    cap = load_capture(folder)
    assert [s.status for s in cap.shots].count(HASH_MISMATCH) == 1  # the fixture's deliberately wrong one
    assert all(s.json_path and not s.txt_path for s in cap.shots)
    assert list(cap.devices) == [UDID] and cap.device_warnings() == []
    assert all(s.captured_utc and s.captured_utc.tzinfo for s in cap.shots)


def test_json_and_txt_must_agree(tmp_path):
    good = hashlib.sha256(b"a").hexdigest()
    _shot(tmp_path, "x_01_02_2026_03_04_05", b"a", js={"sha256": good}, txt=good)
    _shot(tmp_path, "x_01_02_2026_03_04_06", b"b", js={"sha256": hashlib.sha256(b"b").hexdigest()}, txt="1" * 64)
    a, b = load_capture(tmp_path).shots
    assert a.status == HASH_OK
    assert b.status == HASH_MISMATCH and "different SHA-256" in b.note


def test_bad_json_is_reported_not_ignored(tmp_path):
    _shot(tmp_path, "x_01_02_2026_03_04_05", b"a", js="{not json")
    _shot(tmp_path, "x_01_02_2026_03_04_06", b"b", js={"device": {"udid": "U"}})               # no hash
    _shot(tmp_path, "x_01_02_2026_03_04_07", b"c", js={"file": "other.png", "sha256": hashlib.sha256(b"c").hexdigest()})
    _shot(tmp_path, "x_01_02_2026_03_04_08", b"d")
    (tmp_path / "x_01_02_2026_03_04_09.json").write_text("{}")                                 # no PNG
    cap = load_capture(tmp_path)
    assert [s.status for s in cap.shots] == [HASH_UNREADABLE, HASH_UNREADABLE, HASH_UNREADABLE, HASH_MISSING]
    assert "not valid JSON" in cap.shots[0].note and "no SHA-256" in cap.shots[1].note
    assert "describes 'other.png'" in cap.shots[2].note
    assert [p.name for p in cap.orphan_hash_files] == ["x_01_02_2026_03_04_09.json"]


def test_mixed_devices_are_flagged(tmp_path):
    def rec(data, udid):
        return {"sha256": hashlib.sha256(data).hexdigest(), "device": {"udid": udid}}
    _shot(tmp_path, "x_01_02_2026_03_04_05", b"a", js=rec(b"a", "UDID-A"))
    _shot(tmp_path, "x_01_02_2026_03_04_06", b"b", js=rec(b"b", "UDID-B"))
    _shot(tmp_path, "x_01_02_2026_03_04_07", b"c", txt=hashlib.sha256(b"c").hexdigest())
    warns = load_capture(tmp_path).device_warnings()
    assert any("2 different devices" in w for w in warns)
    assert any("1 of 3 screenshots name no device" in w for w in warns)


def test_report_and_overview_group_chats_by_device(tmp_path):
    make_fixture.make(tmp_path / "in", "imessage", sidecar="both", chat="Anna", corrupt=False)
    make_fixture.make(tmp_path / "in", "whatsapp", sidecar="json", chat="Ben", corrupt=False)
    make_fixture.make(tmp_path / "in", "ios", sidecar="txt", chat="Cleo", corrupt=False)
    out = tmp_path / "out"
    assert main([str(tmp_path / "in"), "-o", str(out), "--no-ocr", "-q"]) == 0
    anna = (out / "Messages_Anna.html").read_text(encoding="utf-8")
    assert f"UDID {UDID}" in anna and "Test iPad" in anna and ".json + .txt" in anna
    cleo = (out / "Messages_Cleo.html").read_text(encoding="utf-8")
    assert "not recorded" in cleo
    index = (out / "index.html").read_text(encoding="utf-8")
    first, second = index.split("<h2>")[1:3]
    assert UDID in first and "Anna" in first and "Ben" in first and "Cleo" not in first
    assert "Device not recorded" in second and "Cleo" in second
    assert not list((tmp_path / "in").rglob("*.html"))  # nothing written next to the evidence


def test_mixed_devices_set_the_exit_code(tmp_path):
    folder = make_fixture.make(tmp_path / "in", "imessage", sidecar="json", corrupt=False)
    first = sorted(folder.glob("*.json"))[0]
    data = json.loads(first.read_text())
    data["device"]["udid"] = "ANOTHER-DEVICE"
    first.write_text(json.dumps(data))
    assert main([str(tmp_path / "in"), "-o", str(tmp_path / "out"), "--no-ocr", "-q"]) == 2
