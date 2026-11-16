"""The <name>.case.json UFADE and ALEX write next to each screenshot (CASE/UCO,
JSON-LD): hash, capture time and settings, device. UFADE writes it next to the
.txt, ALEX instead of it."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html.capture import (HASH_MISMATCH, HASH_MISSING, HASH_OK, HASH_UNREADABLE,  # noqa: E402
                                          load_capture)
from chat_capture_to_html.cli import main  # noqa: E402

DEV = make_fixture.DEVICE
START, END = "2026-11-12T14:30:05Z", "2026-11-12T14:30:09Z"


def _record(name, data, digest=None, device=DEV, file_name=None):
    return make_fixture.case_record(file_name or f"{name}.png", len(data), digest or hashlib.sha256(data).hexdigest(),
                                    START, END, device, "iMessage", "Test Chat")


def _shot(folder, stem, data=b"png", case=None, txt=None):
    (folder / f"{stem}.png").write_bytes(data)
    if case is not None:
        text = case if isinstance(case, str) else json.dumps(case)
        (folder / f"{stem}.case.json").write_text(text, encoding="utf-8")
    if txt is not None:
        (folder / f"{stem}.txt").write_text(txt)


def test_case_json_verifies_and_names_the_device(tmp_path):
    folder = make_fixture.make(tmp_path, "imessage", sidecar="case")  # as ALEX writes: no .txt
    cap = load_capture(folder)
    assert [s.status for s in cap.shots].count(HASH_MISMATCH) == 1  # the fixture's deliberately wrong one
    assert all(s.case_path and not s.txt_path for s in cap.shots)
    assert list(cap.devices) == [DEV["id"]] and cap.device_warnings() == []
    d = cap.devices[DEV["id"]]
    assert (d.serial, d.model, d.manufacturer, d.os_name, d.os_version) == (
        DEV["serial"], DEV["model"], "Apple", "iPadOS", "26.1")
    assert all(s.captured_utc and s.captured_utc.tzinfo for s in cap.shots)
    c = cap.shots[0].capture
    assert c["tool"] == "UFADE" and c["CaptureDirection"] == "up" and c["ExtractionMethod"] == "Chat Capture"
    assert not cap.orphan_hash_files  # x.case.json belongs to x.png


def test_case_json_and_txt_must_agree(tmp_path):
    good = hashlib.sha256(b"a").hexdigest()
    _shot(tmp_path, "x_01_02_2026_03_04_05", b"a", case=_record("x_01_02_2026_03_04_05", b"a"), txt=good)
    _shot(tmp_path, "x_01_02_2026_03_04_06", b"b", case=_record("x_01_02_2026_03_04_06", b"b"), txt="1" * 64)
    a, b = load_capture(tmp_path).shots
    assert a.status == HASH_OK and a.case_path and a.txt_path
    assert b.status == HASH_MISMATCH and "different SHA-256" in b.note


def test_bad_case_json_is_reported_not_ignored(tmp_path):
    _shot(tmp_path, "x_01_02_2026_03_04_05", b"a", case="{not json")
    _shot(tmp_path, "x_01_02_2026_03_04_06", b"b", case={"@context": {}})                   # no @graph
    _shot(tmp_path, "x_01_02_2026_03_04_07", b"c", case=_record("x", b"c", file_name="other.png"))
    no_hash = _record("x_01_02_2026_03_04_08", b"d")
    no_hash["@graph"][2]["uco-core:hasFacet"].pop()                                          # drop the hash facet
    _shot(tmp_path, "x_01_02_2026_03_04_08", b"d", case=no_hash)
    _shot(tmp_path, "x_01_02_2026_03_04_09", b"e")
    (tmp_path / "x_01_02_2026_03_04_10.case.json").write_text("{}")                          # no PNG
    cap = load_capture(tmp_path)
    assert [s.status for s in cap.shots] == [HASH_UNREADABLE] * 4 + [HASH_MISSING]
    assert "not valid JSON" in cap.shots[0].note and "no @graph" in cap.shots[1].note
    assert "describes 'other.png'" in cap.shots[2].note and "no SHA-256" in cap.shots[3].note
    assert [p.name for p in cap.orphan_hash_files] == ["x_01_02_2026_03_04_10.case.json"]


def test_mixed_devices_are_flagged(tmp_path):
    other = dict(DEV, id="0f0f0f0f-0000-5000-8000-000000000000", serial="OTHERSERIAL")
    _shot(tmp_path, "x_01_02_2026_03_04_05", b"a", case=_record("x_01_02_2026_03_04_05", b"a"))
    _shot(tmp_path, "x_01_02_2026_03_04_06", b"b", case=_record("x_01_02_2026_03_04_06", b"b", device=other))
    _shot(tmp_path, "x_01_02_2026_03_04_07", b"c", txt=hashlib.sha256(b"c").hexdigest())
    warns = load_capture(tmp_path).device_warnings()
    assert any("2 different devices" in w for w in warns)
    assert any("1 of 3 screenshots name no device" in w for w in warns)


def test_report_and_overview_group_chats_by_device(tmp_path):
    make_fixture.make(tmp_path / "in", "imessage", sidecar="both", chat="Anna", corrupt=False)
    make_fixture.make(tmp_path / "in", "whatsapp", sidecar="case", chat="Ben", corrupt=False)
    make_fixture.make(tmp_path / "in", "ios", sidecar="txt", chat="Cleo", corrupt=False)
    out = tmp_path / "out"
    assert main([str(tmp_path / "in"), "-o", str(out), "--no-ocr", "-q"]) == 0
    anna = (out / "Messages_Anna.html").read_text(encoding="utf-8")
    assert f"device ID {DEV['id']}" in anna and DEV["serial"] in anna and ".case.json + .txt" in anna
    assert "direction up" in anna and "Chat Capture" in anna
    cleo = (out / "Messages_Cleo.html").read_text(encoding="utf-8")
    assert "not recorded" in cleo
    index = (out / "index.html").read_text(encoding="utf-8")
    first, second = index.split("<h2>")[1:3]
    assert DEV["id"] in first and "Anna" in first and "Ben" in first and "Cleo" not in first
    assert "Device not recorded" in second and "Cleo" in second
    assert not list((tmp_path / "in").rglob("*.html"))  # nothing written next to the evidence


def test_mixed_devices_set_the_exit_code(tmp_path):
    folder = make_fixture.make(tmp_path / "in", "imessage", sidecar="case", corrupt=False)
    first = sorted(folder.glob("*.case.json"))[0]
    data = json.loads(first.read_text())
    for obj in data["@graph"]:
        if obj["@type"] == "uco-observable:Device":
            obj["@id"] = "kb:device-another"
    first.write_text(json.dumps(data))
    assert main([str(tmp_path / "in"), "-o", str(tmp_path / "out"), "--no-ocr", "-q"]) == 2
