"""--no-ocr: the report shows each message as it appears in its screenshot.
These tests need no OCR engine at all."""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html import cli  # noqa: E402
from chat_capture_to_html.capture import load_capture  # noqa: E402
from chat_capture_to_html.pipeline import build_chat  # noqa: E402


@pytest.mark.parametrize("style", ["imessage", "ios", "ios-dark", "ipad", "ipad-dark", "whatsapp"])
def test_order_sides_and_views_without_ocr(tmp_path, style):
    folder = make_fixture.make(tmp_path, style)
    expected = json.loads((tmp_path / f"expected_{style}.json").read_text(encoding="utf-8"))
    cap = load_capture(folder)
    res = build_chat(cap, None)
    assert not res.text_read and not res.warnings
    assert [m.side for m in res.messages] == [e["side"] for e in expected]
    for m in res.messages:
        assert not m.lines and m.views
        for v in m.views:
            x0, y0, x1, y1 = v.box
            info = res.shots[v.shot]
            assert 0 <= x0 < x1 <= info.width and 0 <= y0 < y1 <= info.height
            assert v.shot in m.text_from
    if style.startswith("ipad"):  # views stay inside the chat pane, never in the conversation list
        assert all(v.box[0] >= make_fixture.LIST_W for m in res.messages for v in m.views)
    # the newest message is shown whole, not cut at the input bar
    assert res.messages[-1].views[0].complete


def test_cli_no_ocr_needs_no_engine_and_links_every_view(tmp_path, monkeypatch):
    def no_engine(*a, **k):
        raise AssertionError("--no-ocr must not load an OCR engine")
    monkeypatch.setattr(cli, "make_engine", no_engine)
    make_fixture.make(tmp_path / "in", "imessage")
    out = tmp_path / "report.html"
    assert cli.main([str(tmp_path / "in"), "-o", str(out), "--no-ocr", "-q"]) == 2  # one bad hash in the fixture
    html = out.read_text(encoding="utf-8")
    assert "off (--no-ocr)" in html and "OCR confidence" not in html
    imgs = re.findall(r'<img src="([^"]+)" alt="(?:Message|Line) as shown in screenshot #\d+"', html)
    assert len(imgs) == 15
    pngs = {p.name for p in (tmp_path / "in").rglob("*.png")}
    assert all(Path(src.replace("%20", " ")).name in pngs for src in imgs)
    assert not list((tmp_path / "in").rglob("*.html"))  # nothing written next to the evidence


def test_same_app_and_chat_name_do_not_overwrite(tmp_path):
    make_fixture.make(tmp_path / "a", "imessage")
    make_fixture.make(tmp_path / "b", "ios")
    cli.main([str(tmp_path), "-o", str(tmp_path / "out"), "--no-ocr", "-q"])
    assert sorted(p.name for p in (tmp_path / "out").glob("*.html")) == \
        ["Messages_Test Kontakt.html", "Messages_Test Kontakt_2.html"]
