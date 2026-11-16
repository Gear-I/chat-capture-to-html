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
    assert not any(m.doubt for m in res.messages)  # every real message is in every shot that shows it
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


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_ios26_floating_title_and_blur_do_not_break_the_joins(tmp_path, seed):
    """iOS 26 draws the contact's name pill over the chat and blurs the chat as
    it slides under the title. Those rows are not chat that can be compared, so
    they stay out of the chat area. The capture also holds an animated picture
    that shows a different frame in every shot (pictures are not compared), and
    bubbles caught a few pixels from where they settle. Every overlapping pair
    of shots joins."""
    folder = make_fixture.make(tmp_path, "ios26-dark", seed=seed)
    res = build_chat(load_capture(folder), None)
    assert len(res.segment_order) == 1 and not res.warnings
    assert res.content_top >= make_fixture.PILL_BOTTOM
    assert any(info.pictures for info in res.shots)
    expected = json.loads((tmp_path / "expected_ios26-dark.json").read_text(encoding="utf-8"))
    bubbles = [m for m in res.messages if m.kind == "bubble"]
    assert [m.side for m in bubbles] == [e["side"] for e in expected if e["side"] != "center"]
    # the bubble right after the picture is not taken into it, nor lost, nor marked
    assert not any(m.doubt for m in res.messages)
    for m in res.messages:
        assert m.views and all(v.shot in m.text_from for v in m.views)


@pytest.mark.parametrize("style", ["ipad", "ipad-dark"])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_ipad_subpixel_redraws_still_join(tmp_path, style, seed):
    """The iPad redraws the chat between two pixels, so parts of a line land on
    different pixel rows. That alone must not make overlapping shots look like
    different screens."""
    folder = make_fixture.make(tmp_path, style, seed=seed)
    res = build_chat(load_capture(folder), None)
    assert len(res.segment_order) == 1 and not res.warnings


@pytest.mark.parametrize("style", ["imessage", "whatsapp", "ios-dark"])
@pytest.mark.parametrize("seed", [1, 2])
def test_something_in_one_shot_only_is_marked_not_hidden(tmp_path, style, seed):
    """A bubble-like patch that one screenshot shows and another screenshot of
    the same spot does not (a frame of an animated sticker, a typing
    indicator) stays in the report, marked as maybe not a message."""
    folder = make_fixture.make(tmp_path, style, seed=seed, ghost=True)
    expected = json.loads((tmp_path / f"expected_{style}.json").read_text(encoding="utf-8"))
    res = build_chat(load_capture(folder), None)
    flagged = [m for m in res.messages if m.doubt]
    assert len(flagged) == 1 and "seen in 1 of" in flagged[0].doubt
    assert [m.side for m in res.messages if not m.doubt] == [e["side"] for e in expected]
    out = tmp_path / "r.html"
    assert cli.main([str(folder), "-o", str(out), "--no-ocr", "-q"]) == 2  # one bad hash in the fixture
    html = out.read_text(encoding="utf-8")
    assert html.count('class="foot doubtnote"') == 1 and "may not be messages" in html


def test_a_marked_line_shows_its_note(tmp_path):
    """Something marked that ends up shown as a line between messages (a
    centred item) shows the same note as a marked message."""
    from chat_capture_to_html.report import render
    folder = make_fixture.make(tmp_path, "imessage")
    res = build_chat(load_capture(folder), None)
    line = next(m for m in res.messages if m.kind != "bubble")
    line.doubt = "seen in 1 of 2 screenshots that show this spot (not in #2); may not be a message"
    html = render(res, tmp_path / "r.html", "Me", "Them")
    assert html.count('class="foot doubtnote"') == 1 and 'class="meta doubt"' in html


def test_bubble_touching_a_picture_on_one_side_is_kept():
    """Measured on a real capture: a message right below a photo, as wide as
    the photo, with a flat strip of the photo above its corner. Its top side
    is all picture, its bottom side 72% and its right side 88% background;
    it is still a message. Flat parts of the photo are not."""
    import numpy as np
    from chat_capture_to_html.bubbles import Bubble
    from chat_capture_to_html.pipeline import _surrounded_by_background
    # positions and sizes as measured (shot 1320 px wide)
    rng = np.random.default_rng(0)
    img = np.zeros((1400, 1320, 3), np.uint8)                       # dark background
    img[100:771, 552:1264] = rng.integers(40, 220, (671, 712, 3))   # photo
    img[571:771, 1150:1259] = 90                                    # flat strip at its lower right corner
    img[771:983, 552:1259] = (10, 132, 255)                         # the message, right against the photo
    img[983:989, 552:750] = 120                                     # something just below: 28% of the bottom side
    img[771:796, 1259:1265] = 120                                   # and on the right: 12% of that side
    img[300:360, 700:830] = 200                                     # flat patch inside the photo

    def bubble(x0, y0, x1, y1):
        return Bubble(x0, y0, x1, y1, (0, 0, 0), "right", False, False)

    assert _surrounded_by_background(bubble(552, 771, 1259, 983), img, (0, 0, 0))
    assert not _surrounded_by_background(bubble(1150, 571, 1259, 771), img, (0, 0, 0))
    assert not _surrounded_by_background(bubble(700, 300, 830, 360), img, (0, 0, 0))


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
        ["Messages_Test Kontakt.html", "Messages_Test Kontakt_2.html", "index.html"]  # two reports + the overview


@pytest.mark.parametrize("strip", [10, 60])
def test_list_strip_inside_the_pane(tmp_path, strip):
    """Like the real dark-mode iPad capture: a sliver of the conversation list
    ends up inside the chat pane. It must neither spoil the matching (it stays
    put while the chat scrolls) nor swallow the date lines (its text has ink on
    almost every row)."""
    folder = make_fixture.make(tmp_path, "ipad-dark")
    expected = json.loads((tmp_path / "expected_ipad-dark.json").read_text(encoding="utf-8"))
    res = build_chat(load_capture(folder), None, left=make_fixture.LIST_W - strip)
    assert len(res.segment_order) == 1, res.warnings
    assert [m.side for m in res.messages] == [e["side"] for e in expected]
    assert sum(m.kind == "meta" for m in res.messages) == sum(e["side"] == "center" for e in expected)
