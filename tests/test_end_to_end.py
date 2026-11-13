"""Runs the whole pipeline on synthetic captures from make_fixture.py."""

import difflib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html.capture import HASH_MISMATCH, load_capture  # noqa: E402
from chat_capture_to_html.cli import main  # noqa: E402
from chat_capture_to_html.ocr import OcrError, TesseractOcr  # noqa: E402
from chat_capture_to_html.pipeline import build_chat  # noqa: E402

try:
    ENGINE = TesseractOcr()
except OcrError:
    ENGINE = None
needs_ocr = pytest.mark.skipif(ENGINE is None, reason="Tesseract not installed")


def norm(s):
    return re.sub(r"[^\w]+", " ", s.replace("|", "I")).strip().lower()


@needs_ocr
@pytest.mark.parametrize("style", ["imessage", "ios", "ipad", "whatsapp"])
def test_chat_matches_ground_truth(tmp_path, style):
    folder = make_fixture.make(tmp_path, style)
    expected = json.loads((tmp_path / f"expected_{style}.json").read_text(encoding="utf-8"))
    cap = load_capture(folder)
    assert sum(s.status == HASH_MISMATCH for s in cap.shots) == 1
    assert len(cap.shots) >= 3

    res = build_chat(cap, ENGINE)
    assert not res.warnings
    assert len(res.segment_order) == 1
    got = res.messages
    assert [m.side for m in got] == [e["side"] for e in expected]
    for m, e in zip(got, expected):
        ratio = difflib.SequenceMatcher(None, norm(" ".join(m.lines)), norm(e["text"])).ratio()
        assert ratio > 0.85, (m.lines, e["text"])
        assert m.sources and all(s.shot < len(cap.shots) for s in m.sources)
    if style == "ipad":  # only the chat pane is read; boxes still point into the full screenshot
        assert (res.chat_left, res.chat_right) == (make_fixture.LIST_W + 1, make_fixture.IPAD_W)
        assert all(s.box[0] >= make_fixture.LIST_W for m in got for s in m.sources)
    if style == "whatsapp":
        times = [m.time for m in got if m.kind == "bubble"]
        assert sum(t == e["time"] for t, e in zip(times, [e for e in expected if e["side"] != "center"])) >= len(times) - 1


@needs_ocr
def test_cli_writes_linked_report(tmp_path):
    make_fixture.make(tmp_path / "in", "imessage")
    out = tmp_path / "report.html"
    rc = main([str(tmp_path / "in"), "-o", str(out), "-q"])
    assert rc == 2  # a hash mismatch is reported through the exit code
    html = out.read_text(encoding="utf-8")
    assert "HASH MISMATCH" in html
    cap = load_capture(next((tmp_path / "in").rglob("*.png")).parent)
    for s in cap.shots:
        assert s.sha256 in html
    assert "../in/screenshots/Messages/Test%20Kontakt/" not in html  # report sits next to "in"
    assert "in/screenshots/Messages/Test%20Kontakt/" in html
