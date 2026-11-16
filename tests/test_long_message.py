"""A message taller than the screen whose top the capture skipped: UFADE's
focus-based scrolling can jump past the start of such a message. The pieces
are joined into one message, and the report says its top was not captured."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html import cli  # noqa: E402
from chat_capture_to_html.capture import load_capture  # noqa: E402
from chat_capture_to_html.pipeline import build_chat  # noqa: E402


@pytest.mark.parametrize("style", ["imessage", "ipad", "whatsapp"])
def test_long_message_is_one_message_marked_cut_at_the_top(tmp_path, style):
    folder = make_fixture.make(tmp_path, style, long=True, corrupt=False)
    res = build_chat(load_capture(folder), None)
    screen = make_fixture.H - make_fixture.HEADER - make_fixture.FOOTER
    long = [m for m in res.messages if m.kind == "bubble" and m.gy1 - m.gy0 > screen]
    assert len(long) == 1                             # its pieces from several shots are one message
    assert long[0].cut == "top"
    assert len(res.segment_order) == 2                # the skipped top leaves a gap, shown as such
    if style == "imessage":                           # every other message ends inside a screenshot
        assert [m for m in res.messages if m.cut] == long


def test_report_and_cli_say_the_top_was_not_captured(tmp_path, capsys):
    make_fixture.make(tmp_path / "in", "imessage", long=True, corrupt=False)
    out = tmp_path / "out"
    assert cli.main([str(tmp_path / "in"), "-o", str(out), "--no-ocr"]) == 0  # hashes all fine
    page = next(p for p in out.glob("*.html") if p.name != "index.html").read_text(encoding="utf-8")
    assert "The top of this message is in no screenshot" in page
    assert "1 message(s) reach past the edge of every screenshot" in page
    assert "reach past the edge of every screenshot that shows them; marked in the report" in capsys.readouterr().err
    assert not list((tmp_path / "in").rglob("*.html"))  # nothing written next to the evidence
