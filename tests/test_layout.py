"""Alignment of overlapping screenshots, checked against known scroll offsets."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture as mf  # noqa: E402

from chat_capture_to_html.layout import content_band, picture_boxes, place_shots, to_gray  # noqa: E402

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


def test_chat_pane_beside_a_list_that_changes_a_little():
    """Like a real dark-mode iPad capture: the list drifts by up to 10 grey levels
    between shots (its text stays put), the chat scrolls, and nothing between
    the two stays perfectly still."""
    from chat_capture_to_html.layout import find_chat_pane
    rng = np.random.default_rng(7)
    h, w, edge = 600, 820, 345
    tall = rng.uniform(0, 255, (h + 900, w - edge)).astype(np.float32)  # the chat, scrolled below
    lst = rng.uniform(0, 255, (h, edge)).astype(np.float32)             # the list's text
    drift = np.linspace(0.1, 10, edge)
    grays = []
    for k, top in enumerate((900, 610, 330, 40)):
        g = np.empty((h, w), np.float32)
        g[:, :edge] = lst + drift * (k % 2)
        g[:, edge:] = tall[top:top + h]
        grays.append(g)
    x0, x1 = find_chat_pane(grays)
    assert edge - 3 <= x0 <= edge + 3, (x0, x1)
    assert x1 == w


def test_tiny_overlap_is_not_joined():
    """Shots that share only a sliver (about one bubble) stay apart."""
    from chat_capture_to_html.layout import match_offset, row_signature
    rng = np.random.default_rng(3)
    tall = rng.uniform(0, 255, (4000, 300)).astype(np.float32)
    a, h = row_signature(tall[2000:3000]), 1000
    assert match_offset(a, row_signature(tall[1100:2100])) == (-900, 0.0)  # 100 rows shared
    assert match_offset(a, row_signature(tall[1050:2050])) is None          # 50 rows shared: refused
    assert match_offset(a, row_signature(tall[1150:2150])) == (-850, 0.0)


def test_lookalike_reason_says_where_it_disagrees():
    from chat_capture_to_html.layout import match_details, row_signature
    tall, _, bg = mf.render_chat("imessage", rounds=3)
    bottom = tall.height - CH
    shots = [row_signature(to_gray(np.asarray(mf.screen(tall, t, bg, "9:41", "Test", "imessage")).astype(np.float32))
                           [mf.HEADER:mf.H - mf.FOOTER]) for t in (bottom - 600, bottom - 600 - CH - 200)]
    d, _, why = match_details(*shots)
    assert d is None and "only looks alike" in why
    assert "top to bottom:" in why and "left to right:" in why
    parts = why.split("top to bottom: ")[1].split("%")[0].split()
    assert len(parts) == 8 and all(p == "-" or p.isdigit() for p in parts)


@pytest.mark.parametrize("style", ["imessage", "imessage-dark", "whatsapp"])
def test_picture_boxes_find_pictures_and_never_bubbles(style):
    """Bubbles (one colour with text) are never taken for pictures; a GIF frame is."""
    tall, _, bg = mf.render_chat(style, rounds=2)
    assert picture_boxes(np.asarray(tall), bg) == []
    tall, _, bg = mf.render_chat(style, rounds=2, pictures=True)
    found = picture_boxes(np.asarray(tall), bg)
    truth = tall.info["pictures"]
    assert len(found) == len(truth)
    for (x0, y0, x1, y1), (tx0, ty0, tx1, ty1) in zip(sorted(found, key=lambda b: b[1]), truth):
        assert abs(x0 - tx0) <= 16 and abs(y0 - ty0) <= 16 and abs(x1 - tx1) <= 16 and abs(y1 - ty1) <= 16


def _with_pill(tall, bg, tops, pill):
    from PIL import ImageDraw
    out = []
    for t in tops:
        im = mf.screen(tall, t, bg, "9:41", "Test", "ios")
        if pill:
            d = ImageDraw.Draw(im)
            d.rounded_rectangle((mf.W / 2 - 170, PILL[0], mf.W / 2 + 170, PILL[1]), 30, fill=(238, 238, 240),
                                outline=(200, 200, 205), width=2)
            d.text((mf.W / 2 - 120, PILL[0] + 8), "2 People >", font=mf.font(44), fill=(0, 0, 0))
        out.append(to_gray(np.asarray(im).astype(np.float32)))
    return out


PILL = (mf.HEADER + 10, mf.HEADER + 70)


@pytest.mark.parametrize("start", [mf.HEADER, PILL[0], PILL[0] + 20])
def test_trim_unsteady_edges_cuts_a_name_pill_floating_over_the_chat(start):
    """iOS 26 floats the contact's name over the top of the chat. When the chat
    area was found to start inside it, rows that disagree in most pairs of
    shots are cut, so the chat area starts below the pill."""
    from chat_capture_to_html.layout import trim_unsteady_edges
    tall, _, bg = mf.render_chat("imessage", rounds=3)
    bottom = tall.height - CH
    tops = [bottom, bottom - 701, bottom - 701 - 913, bottom - 701 - 913 - 1003]
    grays = _with_pill(tall, bg, tops, pill=True)
    _, bot, _ = content_band(grays)
    top, new_bot = trim_unsteady_edges(grays, start, bot)
    assert PILL[1] - 4 <= top <= PILL[1] + 10 and new_bot == bot
    # the same shots without the pill: nothing to cut
    plain = _with_pill(tall, bg, tops, pill=False)
    t2, b2, _ = content_band(plain)
    assert trim_unsteady_edges(plain, t2, b2) == (t2, b2)


@pytest.mark.parametrize("spring", [3, 6, 9])
def test_a_bubble_that_sprang_a_few_pixels_still_matches(spring):
    """A screenshot can catch a bubble before it settles after a scroll. A
    block moved a few pixels on its own still matches, and the match says how
    far it had to move."""
    from chat_capture_to_html.layout import _place, row_signature
    tall, _, bg = mf.render_chat("imessage", rounds=3)
    bottom = tall.height - CH
    g0, g1 = (to_gray(np.asarray(mf.screen(tall, t, bg, "9:41", "Test", "imessage")).astype(np.float32))
              for t in (bottom, bottom - 900))
    g1 = g1.copy()
    band = slice(mf.HEADER, mf.H - mf.FOOTER)
    # move one block of rows (a bubble's worth) in the second shot down by `spring` px
    y0, y1 = mf.HEADER + 1200, mf.HEADER + 1400
    g1[y0 + spring:y1 + spring] = g1[y0:y1].copy()
    places = _place([row_signature(g[band]) for g in (g0, g1)], 0.3)
    assert places[1].matched_with == 0 and places[1].y == -900
    assert 0 < places[1].moved <= spring + 1
