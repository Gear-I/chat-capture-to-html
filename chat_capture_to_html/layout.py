"""Find the fixed screen chrome and line up overlapping screenshots vertically.

Every screenshot is cut into a *content band* (between the status/navigation bar
at the top and the input bar at the bottom). Consecutive bands are matched row
by row to find how far the chat scrolled between them, which puts every shot on
one shared vertical axis. Positions on that axis grow downwards, i.e. towards
newer messages, whichever direction UFADE scrolled in.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

SIG_BINS = 64


@dataclass
class Placement:
    segment: int          # shots in different segments could not be lined up
    y: int                # top of the content band on the segment's axis
    matched_with: int | None
    score: float | None   # normalised difference of the match (0 = identical, 1 = unrelated)
    note: str = ""        # why the shot before could not be matched, in numbers only
    moved: int = 0        # largest distance (px) a part had to move on its own to match (bubbles still settling)
    with_pictures: bool = False  # matched only with pictures compared too (a still photo filling the overlap)


def to_gray(img: np.ndarray) -> np.ndarray:
    return (img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114).astype(np.float32)


def detect_chrome(grays: list[np.ndarray], tol: float = 10.0) -> tuple[int, int]:
    """Return (top, bottom) rows of the content band.

    Screen chrome is either the same in every screenshot (status bar, title,
    input field) or translucent and blurred, as on iOS, where the chat shows
    through as a soft smear that has no sharp detail in any shot. Short runs of
    other rows (a changing clock) are tolerated as long as a chrome row follows
    within 2% of the height.
    """
    h = grays[0].shape[0]
    if len(grays) < 2:
        return int(h * 0.11), int(h * 0.91)
    stack = np.stack(grays)
    n, _, w = stack.shape
    w2 = w - w % SIG_BINS
    strips = stack[:, :, :w2].reshape(n, h, SIG_BINS, w2 // SIG_BINS).mean(axis=3)
    # per row: the largest change in any column strip, in the worst shot, so a
    # short line of text on an otherwise empty row still counts as a change
    diff = np.abs(strips - strips[0]).max(axis=2).max(axis=0)
    detail = np.abs(np.diff(stack, axis=2)).mean(axis=2).max(axis=0)  # per row, sharpest shot
    static = diff <= tol
    blurred = detail <= 0.6
    gap = max(2, int(h * 0.02))

    def scan(rows):
        # Unchanged rows may bridge a short run of changing ones (a clock).
        # Blurred rows only extend the bar directly, never across a gap, so
        # flat rows inside bubbles further in cannot pull the edge inwards.
        edge, run = None, 0
        for r in rows:
            if static[r] or (blurred[r] and run == 0):
                edge, run = r, 0
            else:
                run += 1
                if run > gap:
                    break
        return edge

    t = scan(range(0, int(h * 0.25)))
    b = scan(range(h - 1, int(h * 0.75), -1))
    top = 0 if t is None else t + 1
    bottom = h if b is None else b
    if bottom - top < h * 0.3:  # nearly identical shots: fall back to a safe default
        return int(h * 0.11), int(h * 0.91)
    return top, bottom


def picture_boxes(img: np.ndarray, bg: tuple[int, int, int], block: int = 8) -> list[tuple[int, int, int, int]]:
    """Boxes (x0, y0, x1, y1) of pictures in a screenshot: photos, GIFs, videos.

    A message bubble is a smooth fill with text on it: in real screenshots 85%
    or more of its pixels equal their neighbours, and most of it is one colour
    (iOS shading changes it only slowly). A photo or GIF has grain or detail on
    a good part of it (real GIFs measured 63-73% smooth) and many colours.
    An animated GIF shows a different frame in every screenshot, so pictures
    must not be compared when lining shots up."""
    h, w, _ = img.shape
    hb, wb = h // block, w // block
    if hb < 4 or wb < 4:
        return []
    im = img[:hb * block, :wb * block].astype(np.int16)
    blocks = im.astype(np.float32).reshape(hb, block, wb, block, 3)
    mean = blocks.mean(axis=(1, 3))
    std = blocks.std(axis=(1, 3)).mean(axis=2)
    off_bg = (np.abs(mean - np.array(bg, np.float32)).sum(axis=2) > 30) | (std > 20)
    # per block: pixels off the background, and how many of them are smooth
    smooth = np.zeros(im.shape[:2], bool)
    smooth[:-1, :-1] = ((np.abs(im[:-1, 1:] - im[:-1, :-1]).max(axis=2) <= 4)
                        & (np.abs(im[1:, :-1] - im[:-1, :-1]).max(axis=2) <= 4))
    ink = np.abs(im - np.array(bg, np.int16)).sum(axis=2) > 30
    n_ink = ink.reshape(hb, block, wb, block).sum(axis=(1, 3))
    n_smooth = (ink & smooth).reshape(hb, block, wb, block).sum(axis=(1, 3))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(off_bg.astype(np.uint8), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < wb * 0.3 or bh < hb * 0.05 or area < 0.5 * bw * bh:
            continue
        sel = labels == i
        cols = mean[sel]
        same = (np.abs(cols - np.median(cols, axis=0)).max(axis=1) <= 24).mean()  # blocks in the main colour
        smooth_share = n_smooth[sel].sum() / max(1, n_ink[sel].sum())
        if smooth_share < 0.78 and same < 0.75:
            out.append((int(x * block), int(y * block), int(min(w, (x + bw) * block)), int(min(h, (y + bh) * block))))
    return out


def blank_pictures(gray: np.ndarray, boxes) -> np.ndarray:
    """A copy of ``gray`` with each picture box marked as not to be compared
    (NaN). Row signatures carry the mark, and wherever either of two shots is
    marked, neither is compared there (see _pair). Used for lining shots up
    only; the report always shows the original screenshots."""
    if not boxes:
        return gray
    g = gray.astype(np.float32, copy=True)
    for x0, y0, x1, y1 in boxes:
        g[y0:y1, x0:x1] = np.nan
    return g


def _pair(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``a`` and ``b`` with every cell that is a picture in either set to 0."""
    m = np.isnan(a) | np.isnan(b)
    if not m.any():
        return a, b
    return np.where(m, 0.0, a), np.where(m, 0.0, b)


def row_signature(band: np.ndarray, keep: np.ndarray | None = None) -> np.ndarray:
    """Per row, how much horizontal detail (text strokes, bubble edges) each of
    SIG_BINS column strips holds. Flat areas score zero whatever their colour,
    so bubble colours that shift with screen position (iOS gradients) and plain
    backgrounds do not influence matching. ``keep`` limits it to the strips
    that move with the chat (see moving_strips)."""
    h, w = band.shape
    dx = np.abs(np.diff(band, axis=1))
    w2 = (w - 1) - (w - 1) % SIG_BINS
    sig = dx[:, :w2].reshape(h, SIG_BINS, w2 // SIG_BINS).mean(axis=2)
    return sig if keep is None else sig[:, keep]


def moving_strips(grays: list[np.ndarray]) -> np.ndarray | None:
    """The column strips (as in row_signature) whose content changes between
    shots about as much as the chat does. Strips that barely change while the
    chat scrolls, such as a sliver of the iPad conversation list left inside
    the pane, would otherwise favour "the shots did not move" and spoil every
    match. None when there is too little to tell or nothing to leave out."""
    if len(grays) < 2:
        return None
    w = grays[0].shape[1]
    w2 = (w - 1) - (w - 1) % SIG_BINS
    change = column_change(grays)[:w2].reshape(SIG_BINS, w2 // SIG_BINS).mean(axis=1)
    keep = change > 0.1 * float(np.percentile(change, 90))
    if keep.all() or keep.sum() < SIG_BINS // 4:
        return None
    return keep


def _block(sig: np.ndarray, step: int) -> np.ndarray:
    h = sig.shape[0] - sig.shape[0] % step
    return sig[:h].reshape(h // step, step, -1).mean(axis=1)


def _overlap(sa: np.ndarray, sb: np.ndarray, d: int) -> tuple[np.ndarray, np.ndarray]:
    h = sa.shape[0]
    a0, a1 = max(0, d), min(h, h + d)
    # row r of B shows the same content as row r + d of A
    return _pair(sa[a0:a1], sb[a0 - d:a1 - d])


def _ink_rows(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a.sum(axis=1) + b.sum(axis=1)) > 0.5 * a.shape[1]


def _scores(sa: np.ndarray, sb: np.ndarray, candidates, min_rows: int, min_overlap: int = 0) -> list[tuple[float, int]]:
    """Normalised difference (0 = identical, 1 = unrelated) over the rows of the
    overlap that hold any detail in either shot."""
    out = []
    for d in candidates:
        a, b = _overlap(sa, sb, d)
        if a.shape[0] < max(min_rows, min_overlap):  # too small a shared part proves nothing
            continue
        ink = _ink_rows(a, b)
        if ink.sum() < min_rows:  # too little text in the overlap to prove anything
            continue
        a, b = a[ink], b[ink]
        out.append((float(np.abs(a - b).sum() / max((a + b).sum(), 1e-6)), d))
    return out


# A part of the screen (a bubble, a line between bubbles) may sit a few pixels
# away from where the scroll puts it: iOS Messages lets bubbles spring into
# place, and a screenshot can catch them before they settle.
MAX_LOCAL_SHIFT = 10


def _runs(mask: np.ndarray, join: int = 6) -> list[tuple[int, int]]:
    """Runs of True, joining runs less than ``join`` rows apart."""
    out: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if out and start - out[-1][1] <= join:
                out[-1] = (out[-1][0], i)
            else:
                out.append((start, i))
            start = None
    return out


def _local_fit(sa: np.ndarray, sb: np.ndarray, d: int,
               max_shift: int = MAX_LOCAL_SHIFT) -> tuple[np.ndarray, np.ndarray, int]:
    """For B placed at offset d: the overlap rows that still disagree once each
    block of disagreeing rows may move up to ``max_shift`` pixels on its own,
    the overlap rows holding text, and the largest move that helped.

    Text that really differs differs at every small shift, so screens that
    only look alike still disagree; a bubble that sprang a few pixels does not."""
    a0 = max(0, d)
    a, b = _overlap(sa, sb, d)
    ink = _ink_rows(a, b)
    bad = _strong_cells(a, b).any(axis=1) & ink
    moved = 0
    if max_shift <= 0 or not bad.any():
        return bad, ink, moved
    out = bad.copy()
    for r0, r1 in _runs(bad):
        best = bad[r0:r1]
        best_n, best_s = int(best.sum()), 0
        for s in range(-max_shift, max_shift + 1):
            rb0, rb1 = r0 + a0 - d + s, r1 + a0 - d + s
            if s == 0 or rb0 < 0 or rb1 > sb.shape[0]:
                continue
            x, y = _pair(sa[a0 + r0:a0 + r1], sb[rb0:rb1])
            m = _strong_cells(x, y).any(axis=1) & ink[r0:r1]
            if int(m.sum()) < best_n:
                best, best_n, best_s = m, int(m.sum()), s
        out[r0:r1] = best
        if best_s:
            moved = max(moved, abs(best_s))
    return out, ink, moved


def _disagreement(sa: np.ndarray, sb: np.ndarray, d: int) -> float:
    """Share of text rows in the overlap where some column strip clearly
    differs. A true match is near zero; screens that merely look alike (the
    same phrases, the same bubble pattern) differ on every line whose text
    differs, even when the average difference is small.

    iOS often stops scrolling between two pixels and redraws text slightly
    shifted, and each part of a line may land on a different pixel row. So a
    column strip only counts as differing when it differs from the other
    shot's row and from both of its neighbours, and a row disagrees when at
    least one strip does. A block of rows that matches once moved a few pixels
    on its own (a bubble still settling) does not disagree; see _local_fit."""
    bad, ink, _ = _local_fit(sa, sb, d)
    if not ink.any():
        return 1.0
    return float(bad[ink].mean())


def _strong_cells(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cells (row, column strip) that clearly differ between ``a`` and ``b``
    at the same row and at the rows just above and below in ``b``."""
    strong = np.ones(a.shape, bool)
    for o in (-1, 0, 1):
        bb = np.roll(b, o, axis=0)
        tot = a + bb
        strong &= (np.abs(a - bb) > 0.6 * tot) & (tot > 2.0)
    return strong


def _where_disagreeing(sa: np.ndarray, sb: np.ndarray, d: int) -> str:
    """Where the disagreeing text sits, for --diagnose: the share of text rows
    that disagree in each eighth of the later shot's chat area from top to
    bottom, and the share of detailed column strips that disagree in each
    eighth of its width from left to right. Numbers only."""
    a, b = _overlap(sa, sb, d)
    h = sb.shape[0]
    strong_rows, ink, _ = _local_fit(sa, sb, d)  # rows that still differ after local moves
    strong = _strong_cells(a, b) & strong_rows[:, None]
    rows = np.arange(max(0, -d), max(0, -d) + a.shape[0])  # these rows of the later shot
    by_row = []
    for k in range(8):
        sel = ink & (rows * 8 // h == k)
        by_row.append("-" if not sel.any() else f"{100 * float(strong_rows[sel].mean()):.0f}")
    cells = (a + b) > 2.0
    nstrip = a.shape[1]
    by_col = []
    for k in range(8):
        cols = slice(k * nstrip // 8, (k + 1) * nstrip // 8)
        c = cells[:, cols]
        by_col.append("-" if not c.any() else f"{100 * float(strong[:, cols][c].mean()):.0f}")
    return f"disagreeing by eighth, top to bottom: {' '.join(by_row)}%; left to right: {' '.join(by_col)}%"


def _ranked_offsets(sa: np.ndarray, sb: np.ndarray) -> list[tuple[float, int]]:
    """Candidate scroll offsets d (B[r] == A[r + d]) with their scores, best first."""
    h = sa.shape[0]
    min_rows = max(20, int(h * 0.03))
    # A shared part shorter than this is about one bubble: too little to rule
    # out a coincidence, so such shots are left unjoined (a visible gap).
    min_overlap = max(40, int(h * 0.08))
    step = 4
    coarse = _scores(_block(sa, step), _block(sb, step), range(-(h // step) + 1, h // step), max(5, min_rows // step),
                     min_overlap // step)
    if not coarse:
        return []
    coarse.sort()
    fine: dict[int, float] = {}
    for _, dc in coarse[:12]:
        for sc, d in _scores(sa, sb, range(dc * step - step, dc * step + step + 1), min_rows, min_overlap):
            if d != 0:
                fine[d] = sc
    return sorted((sc, d) for d, sc in fine.items())


def trim_unsteady_edges(grays: list[np.ndarray], top: int, bottom: int, keep: np.ndarray | None = None,
                        max_score: float = 0.3, zone: float = 0.06) -> tuple[int, int]:
    """Narrow the chat area where its top or bottom edge does not hold steady
    chat: on iOS 26 the contact's name floats over the top of the chat and the
    chat blurs as it slides under it. Each pair of consecutive shots is lined
    up at its best offset; rows within ``zone`` of either edge that disagree
    in most of the pairs that see them are left out."""
    h = bottom - top
    sigs = [row_signature(g[top:bottom], keep) for g in grays]
    if len(sigs) < 2 or h < 100:
        return top, bottom
    bad = np.zeros(h)
    seen = np.zeros(h)
    pairs = 0
    for i in range(1, len(sigs)):
        ranked = _ranked_offsets(sigs[i - 1], sigs[i])
        if not ranked or ranked[0][0] > max_score:
            continue
        d = ranked[0][1]
        rows_bad, ink, _ = _local_fit(sigs[i - 1], sigs[i], d)
        ra = np.arange(max(0, d), max(0, d) + len(ink))
        for rows in (ra, ra - d):  # rows of the earlier shot, rows of the later one
            seen[rows[ink]] += 1
            bad[rows[rows_bad]] += 1
        pairs += 1
    if pairs < 2:
        return top, bottom
    # a row is unsteady when most of at least two pairs that hold text there disagree
    unsteady = (seen >= 2) & (bad > 0.5 * seen)
    z = max(5, int(h * zone))

    def cut(rows_from_edge: np.ndarray) -> int:
        """How many rows to cut from an edge: up to the furthest unsteady row
        such that most of the rows with text before it are unsteady too, so a
        stray row further in does not cut away good chat."""
        u = unsteady[rows_from_edge]
        s = seen[rows_from_edge] >= 2
        n = 0
        for k in np.where(u)[0]:
            if u[:k + 1].sum() > 0.5 * max(1, s[:k + 1].sum()):
                n = int(k) + 1
        return n

    return top + cut(np.arange(z)), bottom - cut(np.arange(h - 1, h - 1 - z, -1))


def match_details(sa: np.ndarray, sb: np.ndarray, max_score: float = 0.3,
                  max_disagree: float = 0.05) -> tuple[int | None, float | None, str]:
    """Scroll offset d so that B[r] == A[r + d], its score, and, when there is
    no trustworthy match, the reason. Returns d = None when the bands do not
    overlap or the best match is not clearly better than the alternatives."""
    step = 4
    ranked = _ranked_offsets(sa, sb)
    if not ranked:
        return None, None, "no overlap holds enough text to compare"
    best_score, best_d = ranked[0]
    if best_score > max_score:
        return None, best_score, f"best match {best_score:.3f} at {best_d:+d} px is above the limit {max_score:.2f}"
    good = []
    for sc, d in ranked:
        if sc <= max_score:
            dis = _disagreement(sa, sb, d)
            if dis <= max_disagree:
                good.append((sc, d, dis))
    if not good:
        return None, best_score, (f"best match {best_score:.3f} at {best_d:+d} px only looks alike: "
                                  f"{_disagreement(sa, sb, best_d):.1%} of text rows disagree "
                                  f"({_where_disagreeing(sa, sb, best_d)})")
    best_score, best_d, best_dis = good[0]
    # The same content matching about as cleanly in a second, separate place
    # (by average difference and by disagreeing rows) cannot be resolved: refuse.
    for sc, d, dis in good[1:]:
        if abs(d - best_d) > 3 * step and sc <= best_score * 2 + 0.01 and dis <= best_dis * 2 + 0.01:
            return None, best_score, (f"ambiguous: {best_score:.3f} at {best_d:+d} px, "
                                      f"but {sc:.3f} at {d:+d} px matches about as well")
    return best_d, best_score, ""


def match_offset(sa: np.ndarray, sb: np.ndarray, max_score: float = 0.3) -> tuple[int, float] | None:
    d, score, _ = match_details(sa, sb, max_score)
    return None if d is None else (d, score)


def place_shots(bands: list[np.ndarray], max_score: float = 0.3, keep: np.ndarray | None = None,
                raw_bands: list[np.ndarray] | None = None) -> list[Placement]:
    """Put every shot on a shared axis, matching each one against the previous
    shot first and then against every shot already placed.

    ``bands`` have pictures marked as not to be compared (blank_pictures). When
    two shots cannot be matched that way, they are tried once more as in
    ``raw_bands``, with pictures compared too: a still photo that fills most of
    the shared part is the same in both shots and lines them up, while an
    animated GIF still disagrees and is refused as before."""
    raw = [row_signature(b, keep) for b in raw_bands] if raw_bands is not None else None
    return _place([row_signature(b, keep) for b in bands], max_score, raw)


def _place(sigs: list[np.ndarray], max_score: float, raw_sigs: list[np.ndarray] | None = None) -> list[Placement]:
    placements: list[Placement] = []
    segment = 0
    for i, sig in enumerate(sigs):
        if i == 0:
            placements.append(Placement(0, 0, None, None))
            continue
        order = [i - 1] + [j for j in range(i - 2, -1, -1)]
        found = None
        note = ""
        for j in order:
            d, score, why = match_details(sigs[j], sig, max_score)
            if j == i - 1:
                note = why
            if d is not None:
                found = (j, d, score, False)
                break
        if found is None and raw_sigs is not None:
            for j in order:
                d, score, _ = match_details(raw_sigs[j], raw_sigs[i], max_score)
                if d is not None:
                    found = (j, d, score, True)
                    break
        if found is None:
            segment += 1
            placements.append(Placement(segment, 0, None, None, note))
        else:
            j, d, score, with_pics = found
            pj = placements[j]
            a, b = (raw_sigs[j], raw_sigs[i]) if with_pics else (sigs[j], sig)
            placements.append(Placement(pj.segment, pj.y + d, j, score, moved=_local_fit(a, b, d)[2],
                                        with_pictures=with_pics))
    return placements


def find_content_band(grays: list[np.ndarray], max_score: float = 0.3,
                      keep: np.ndarray | None = None) -> tuple[tuple[int, int] | None, list[Placement]]:
    """Find the rows that scroll with the chat.

    The middle of a phone screen is always chat, so shots are first lined up on
    that alone. Then each row is checked against the shot it was matched with:
    chat rows reappear shifted by the scroll distance, while the status bar,
    title, input bar and anything drawn over them stay put. The band grows
    outwards from the middle until it meets rows that do not move with the
    chat. Rows without detail (plain background, blurred bars) are neutral.
    Returns the band (None when no two shots could be lined up) and how the
    shots were lined up on the middle of the screen, for diagnosis.
    """
    h = grays[0].shape[0]
    c0, c1 = int(h * 0.15), int(h * 0.85)
    sigs = [row_signature(g, keep) for g in grays]
    places = _place([s[c0:c1] for s in sigs], max_score)
    errs: list[list[float]] = [[] for _ in range(h)]
    for i, pl in enumerate(places):
        if pl.matched_with is None:
            continue
        d = pl.y - places[pl.matched_with].y
        # row r of B shows row r + d of A. Only compare against rows from the
        # middle of the other shot, which are known to be chat.
        for x, y, off in ((sigs[i], sigs[pl.matched_with], d), (sigs[pl.matched_with], sigs[i], -d)):
            rows = np.arange(max(0, c0 - off), min(h, c1 - off))
            if not len(rows):
                continue
            a, b = _pair(x[rows], y[rows + off])
            tot = a.sum(axis=1) + b.sum(axis=1)
            err = np.abs(a - b).sum(axis=1) / np.maximum(tot, 1e-9)
            # A row also fails when any single column strip clearly differs (the
            # same test as _disagreement): a control floating over the chat, or
            # chat that iOS blurs as it slides under the title bar, can cover only
            # part of a row whose other strips do move with the chat.
            strong = np.ones(a.shape, bool)
            for o in (-1, 0, 1):
                ao, bo = _pair(a, y[np.clip(rows + off + o, 0, h - 1)])
                t = ao + bo
                strong &= (np.abs(ao - bo) > 0.6 * t) & (t > 2.0)
            strong = strong.any(axis=1)
            for r, e, s, tt in zip(rows, err, strong, tot):
                if tt > 0.5 * a.shape[1]:
                    errs[r].append(1.0 if s else float(e))
    if not any(errs):
        return None, places, []

    def state(r):
        if not errs[r]:
            return 0  # no detail: says nothing
        return 1 if float(np.median(errs[r])) < 0.25 else -1

    states = [state(r) for r in range(h)]
    tol = max(3, int(h * 0.005))  # a few odd rows (sub-pixel redraws) do not end the chat

    def grow(rows):
        edge, bad = None, 0
        for r in rows:
            if states[r] < 0:
                bad += 1
                if bad > tol:
                    break
            else:
                bad = 0
                if states[r] > 0:
                    edge = r
        return edge

    t = grow(range(c0, -1, -1))
    b = grow(range(c1, h))
    return (c0 if t is None else t, c1 if b is None else b + 1), places, states


def content_band(grays: list[np.ndarray], keep: np.ndarray | None = None) -> tuple[int, int, list[Placement]]:
    """Rows of the chat area: from scrolling where shots overlap, otherwise from
    rows that stay the same in every shot. Also returns how the shots lined up
    on the middle of the screen."""
    band, middle, states = find_content_band(grays, keep=keep)
    chrome = detect_chrome(grays)
    if band is None:
        return (*chrome, middle)
    top, bottom = band
    tol = max(3, int(len(states) * 0.005))

    def blocked(rows) -> bool:  # a real run of rows that stay put
        run = 0
        for r in rows:
            run = run + 1 if states[r] < 0 else 0
            if run > tol:
                return True
        return False

    # Rows next to the bars can only appear in the newest or oldest shot, so no
    # other shot confirms them. Take them in up to the bars found independently,
    # unless rows that stay put lie between.
    if chrome[0] < top and not blocked(range(top - 1, chrome[0] - 1, -1)):
        top = chrome[0]
    if chrome[1] > bottom and not blocked(range(bottom, chrome[1])):
        bottom = chrome[1]
    return top, bottom, middle


def column_change(grays: list[np.ndarray]) -> np.ndarray:
    """Per column, how much the middle rows (15-85% of the height) change from
    the first shot in the worst shot, as a mean grey-level difference."""
    h = grays[0].shape[0]
    stack = np.stack([g[int(h * 0.15):int(h * 0.85)] for g in grays])
    return np.abs(stack - stack[0]).max(axis=0).mean(axis=0)


def _column_scroll(grays: list[np.ndarray], c0: int, c1: int) -> np.ndarray | None:
    """Per column: +1 where the content moves with the chat's scroll, -1 where
    it stays put while the chat scrolls, 0 where it cannot tell (blank).

    Shots are lined up on columns c0..c1 (the chat's busy core) and the middle
    rows. For each matched pair, each column of the later shot is compared with
    the earlier shot twice: shifted by the scroll distance, and not shifted.
    Rows are averaged in blocks of 4 so sub-pixel redraws do not matter.
    Returns None when no two shots line up.
    """
    h, w = grays[0].shape
    m0, m1 = int(h * 0.15), int(h * 0.85)
    places = _place([row_signature(g[m0:m1, c0:c1]) for g in grays], 0.3)
    moved = np.zeros(w)
    stayed = np.zeros(w)
    pairs = 0
    for i, pl in enumerate(places):
        if pl.matched_with is None:
            continue
        d = pl.y - places[pl.matched_with].y
        a, b = grays[pl.matched_with], grays[i]
        r0, r1 = max(m0, m0 - d), min(m1, m1 - d)  # rows r of b whose r + d is a middle row of a
        if r1 - r0 < 40:
            continue
        n = (r1 - r0) // 4 * 4

        def blocks(x):
            return x[:n].reshape(n // 4, 4, w).mean(axis=1)
        bb = blocks(b[r0:r0 + n])
        moved += np.abs(blocks(a[r0 + d:r0 + d + n]) - bb).mean(axis=0)
        stayed += np.abs(blocks(a[r0:r0 + n]) - bb).mean(axis=0)
        pairs += 1
    if not pairs:
        return None
    moved, stayed = moved / pairs, stayed / pairs
    out = np.zeros(w, dtype=np.int8)
    out[(moved < 0.5 * stayed) & (stayed > 2.0)] = 1
    out[(stayed < 0.5 * moved) & (moved > 2.0)] = -1
    return out


def find_chat_pane(grays: list[np.ndarray]) -> tuple[int, int]:
    """Columns of the chat when it shares the screen with something that does
    not scroll, such as the conversation list on an iPad.

    Over the middle rows of the screen (away from the status bar and the input
    bar) the chat pane changes from shot to shot while a side list stays the
    same. The widest run of changing columns is the pane, widened over the
    plain margin beside it. The full width is returned when the chat fills
    nearly the whole screen or when it cannot be told apart.
    """
    h, w = grays[0].shape
    if len(grays) < 2:
        return 0, w
    mid = slice(int(h * 0.15), int(h * 0.85))
    stack = np.stack([g[mid] for g in grays])
    change = column_change(grays)
    # "Moving" is judged against the chat itself: a side list can shift a little
    # between shots (translucent material, a highlight), but far less than the
    # chat, whose columns change wherever messages scroll past.
    # The chat is judged against itself: a side list can drift a little between
    # shots (translucent material, a highlight) but far less than the chat.
    # A strict threshold finds the core of the chat; a lenient one then grows
    # it outwards, so quieter chat columns near its edge are kept while the
    # list, which changes by a small fraction of that, is left out.
    p90 = float(np.percentile(change, 90))
    strict, lenient = max(2.0, 0.2 * p90), max(2.0, 0.02 * p90)
    gap = max(2, int(w * 0.01))
    runs, start, quiet = [], None, 0
    for x in range(w):
        if change[x] > strict:
            if start is None:
                start = x
            quiet, last = 0, x
        elif start is not None:
            quiet += 1
            if quiet > gap:
                runs.append((start, last + 1))
                start, quiet = None, 0
    if start is not None:
        runs.append((start, last + 1))
    if not runs:
        return 0, w
    x0, x1 = max(runs, key=lambda r: r[1] - r[0])
    scrolls = _column_scroll(grays, x0, x1)

    def grow(x, step):
        """Widen the pane from its core until columns clearly stay put while the
        chat scrolls (the list). Blank columns, which say nothing either way,
        are only taken in up to a small margin beyond the outermost column that
        scrolls, so blank list space next to the chat stays out. Without any
        matched pair of shots, fall back to how much each column changes."""
        edge, outer, quiet = x, x, 0
        while 0 <= x + step < w:
            x += step
            if scrolls is not None:
                still, moves = scrolls[x] < 0, scrolls[x] > 0
            else:
                still = change[x] <= lenient
                moves = not still
            if not still:
                edge, quiet = x, 0
                if moves:
                    outer = x
            else:
                quiet += 1
                if quiet > gap:
                    break
        if scrolls is None:
            return edge
        margin = max(2, int(w * 0.02))
        return max(edge, outer - margin) if step < 0 else min(edge, outer + margin)

    x0, x1 = grow(x0, -1), grow(x1 - 1, 1) + 1
    if x1 - x0 < w * 0.3 or (x1 - x0) > w * 0.85:
        return 0, w

    # widen over the plain margin: columns that never change and carry no
    # detail, stopping at a divider line or the side list
    detail = np.abs(np.diff(stack, axis=1)).mean(axis=(0, 1))  # per column
    def flat(x):
        return change[x] <= 2.0 and detail[min(x, w - 1)] < 0.5 and \
            abs(float(stack[0][:, x].mean()) - float(stack[0][:, x0].mean())) < 8
    while x0 > 0 and flat(x0 - 1):
        x0 -= 1
    while x1 < w and flat(x1):
        x1 += 1
    return (0, w) if (x1 - x0) > w * 0.9 else (x0, x1)
