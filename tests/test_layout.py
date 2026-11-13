"""Alignment of overlapping screenshots, checked against known scroll offsets."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture as mf  # noqa: E402

from chat_capture_to_html.layout import content_band, place_shots, to_gray  # noqa: E402

CH = mf.H - mf.HEADER - mf.FOOTER


def _shots(style, tops):
    tall, _, bg = mf.render_chat("imessage" if style == "ios" else style, rounds=3)
    imgs = [np.asarray(mf.screen(tall, t, bg, "9:41" if i % 2 else "9:42", "Test", style)) for i, t in enumerate(tops)]
    return [to_gray(i.astype(np.float32)) for i in imgs], tall


@pytest.mark.parametrize("style", ["imessage", "ios", "whatsapp"])
def test_offsets_match_true_scroll(style):
    tall, _, _ = mf.render_chat("imessage" if style == "ios" else style, rounds=3)
    bottom = tall.height - CH
    tops = [bottom, bottom - 701, bottom - 701 - 913, bottom - 701 - 913 - 1003]
    grays, _ = _shots(style, tops)
    top, bot, _ = content_band(grays)
    assert mf.HEADER - 5 <= top <= mf.HEADER + 30
    assert mf.H - mf.FOOTER - 30 <= bot <= mf.H - mf.FOOTER + 5
    places = place_shots([g[top:bot] for g in grays])
    assert {p.segment for p in places} == {0}
    for p, t in zip(places, tops):
        assert p.y == t - tops[0]  # the shared axis is the chat's own scroll position


@pytest.mark.parametrize("style", ["imessage", "ios"])
def test_shots_that_do_not_overlap_leave_a_gap(style):
    tall, _, _ = mf.render_chat("imessage" if style == "ios" else style, rounds=3)
    bottom = tall.height - CH
    tops = [bottom, bottom - 600, bottom - 600 - CH - 200]  # third shot skips part of the chat
    grays, _ = _shots(style, tops)
    top, bot, _ = content_band(grays)
    places = place_shots([g[top:bot] for g in grays])
    assert places[1].segment == 0 and places[1].y == -600
    assert places[2].segment == 1 and places[2].matched_with is None
    assert places[2].note  # the reason is recorded for the report


def test_chat_pane_beside_a_fixed_list():
    from chat_capture_to_html.layout import find_chat_pane
    tall, _, bg = mf.render_chat("imessage", rounds=2)
    bottom = tall.height - (mf.IPAD_H - mf.HEADER - mf.FOOTER)
    tops = (bottom, bottom - 500, bottom - 1100)
    grays = [to_gray(np.asarray(mf.screen(tall, t, bg, "9:41", "Test", "ipad", sub)).astype(np.float32))
             for t, sub in zip(tops, (0.0, 0.5, 0.25))]
    x0, x1 = find_chat_pane(grays)
    assert mf.LIST_W - 2 <= x0 <= mf.LIST_W + 10 and x1 == mf.IPAD_W
    # scrolling stopped between pixels: still lined up, to the nearest pixel
    top, bot, _ = content_band([g[:, x0:x1] for g in grays])
    places = place_shots([g[top:bot, x0:x1] for g in grays])
    assert {p.segment for p in places} == {0}
    assert all(abs(p.y - want) <= 1 for p, want in zip(places, (0, -500, -1100)))


@pytest.mark.parametrize("style", ["imessage", "ios", "whatsapp"])
def test_full_width_chat_keeps_full_width(style):
    from chat_capture_to_html.layout import find_chat_pane
    grays, _ = _shots(style, [3000, 2400, 1800])
    assert find_chat_pane(grays) == (0, mf.W)
