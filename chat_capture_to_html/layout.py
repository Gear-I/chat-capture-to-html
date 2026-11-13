"""Find the fixed screen chrome and line up overlapping screenshots vertically.

Every screenshot is cut into a *content band* (between the status/navigation bar
at the top and the input bar at the bottom). Consecutive bands are matched row
by row to find how far the chat scrolled between them, which puts every shot on
one shared vertical axis. Positions on that axis grow downwards, i.e. towards
newer messages, whichever direction UFADE scrolled in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SIG_BINS = 64


@dataclass
class Placement:
    segment: int          # shots in different segments could not be lined up
    y: int                # top of the content band on the segment's axis
    matched_with: int | None
    score: float | None   # normalised difference of the match (0 = identical, 1 = unrelated)
    note: str = ""        # why the shot before could not be matched, in numbers only


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


def row_signature(band: np.ndarray) -> np.ndarray:
    """Per row, how much horizontal detail (text strokes, bubble edges) each of
    SIG_BINS column strips holds. Flat areas score zero whatever their colour,
    so bubble colours that shift with screen position (iOS gradients) and plain
    backgrounds do not influence matching."""
    h, w = band.shape
    dx = np.abs(np.diff(band, axis=1))
    w2 = (w - 1) - (w - 1) % SIG_BINS
    return dx[:, :w2].reshape(h, SIG_BINS, w2 // SIG_BINS).mean(axis=2)


def _block(sig: np.ndarray, step: int) -> np.ndarray:
    h = sig.shape[0] - sig.shape[0] % step
    return sig[:h].reshape(h // step, step, -1).mean(axis=1)


def _overlap(sa: np.ndarray, sb: np.ndarray, d: int) -> tuple[np.ndarray, np.ndarray]:
    h = sa.shape[0]
    a0, a1 = max(0, d), min(h, h + d)
    # row r of B shows the same content as row r + d of A
    return sa[a0:a1], sb[a0 - d:a1 - d]


def _ink_rows(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a.sum(axis=1) + b.sum(axis=1)) > 0.5 * a.shape[1]


def _scores(sa: np.ndarray, sb: np.ndarray, candidates, min_rows: int) -> list[tuple[float, int]]:
    """Normalised difference (0 = identical, 1 = unrelated) over the rows of the
    overlap that hold any detail in either shot."""
    out = []
    for d in candidates:
        a, b = _overlap(sa, sb, d)
        if a.shape[0] < min_rows:
            continue
        ink = _ink_rows(a, b)
        if ink.sum() < min_rows:  # too little text in the overlap to prove anything
            continue
        a, b = a[ink], b[ink]
        out.append((float(np.abs(a - b).sum() / max((a + b).sum(), 1e-6)), d))
    return out


def _disagreement(sa: np.ndarray, sb: np.ndarray, d: int) -> float:
    """Share of text rows in the overlap where some column strip clearly
    differs. A true match is near zero; screens that merely look alike (the
    same phrases, the same bubble pattern) differ on every line whose text
    differs, even when the average difference is small.

    iOS often stops scrolling between two pixels and redraws text slightly
    shifted, so a row only counts as disagreeing when it differs from the
    other shot's row and from both of its neighbours."""
    a, b = _overlap(sa, sb, d)
    ink = _ink_rows(a, b)
    if not ink.any():
        return 1.0
    strong = np.ones(a.shape[0], bool)
    for o in (-1, 0, 1):
        bb = np.roll(b, o, axis=0)
        tot = a + bb
        strong &= ((np.abs(a - bb) > 0.6 * tot) & (tot > 2.0)).any(axis=1)
    return float(strong[ink].mean())


def match_details(sa: np.ndarray, sb: np.ndarray, max_score: float = 0.3,
                  max_disagree: float = 0.05) -> tuple[int | None, float | None, str]:
    """Scroll offset d so that B[r] == A[r + d], its score, and, when there is
    no trustworthy match, the reason. Returns d = None when the bands do not
    overlap or the best match is not clearly better than the alternatives."""
    h = sa.shape[0]
    min_rows = max(20, int(h * 0.03))
    step = 4
    coarse = _scores(_block(sa, step), _block(sb, step), range(-(h // step) + 1, h // step), max(5, min_rows // step))
    if not coarse:
        return None, None, "no overlap holds enough text to compare"
    coarse.sort()
    fine: dict[int, float] = {}
    for _, dc in coarse[:12]:
        for sc, d in _scores(sa, sb, range(dc * step - step, dc * step + step + 1), min_rows):
            if d != 0:
                fine[d] = sc
    if not fine:
        return None, None, "no overlap holds enough text to compare"
    ranked = sorted((sc, d) for d, sc in fine.items())
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
                                  f"{_disagreement(sa, sb, best_d):.1%} of text rows disagree")
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


def place_shots(bands: list[np.ndarray], max_score: float = 0.3) -> list[Placement]:
    """Put every shot on a shared axis, matching each one against the previous
    shot first and then against every shot already placed."""
    return _place([row_signature(b) for b in bands], max_score)


def _place(sigs: list[np.ndarray], max_score: float) -> list[Placement]:
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
                found = (j, d, score)
                break
        if found is None:
            segment += 1
            placements.append(Placement(segment, 0, None, None, note))
        else:
            j, d, score = found
            pj = placements[j]
            placements.append(Placement(pj.segment, pj.y + d, j, score))
    return placements


def find_content_band(grays: list[np.ndarray], max_score: float = 0.3) -> tuple[tuple[int, int] | None, list[Placement]]:
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
    sigs = [row_signature(g) for g in grays]
    places = _place([s[c0:c1] for s in sigs], max_score)
    errs: list[list[float]] = [[] for _ in range(h)]
    for i, pl in enumerate(places):
        if pl.matched_with is None:
            continue
        d = pl.y - places[pl.matched_with].y
        # row r of B shows row r + d of A. Only compare against rows from the
        # middle of the other shot, which are known to be chat.
        for x, y, off in ((sigs[i], sigs[pl.matched_with], d), (sigs[pl.matched_with], sigs[i], -d)):
            for r in range(max(0, c0 - off), min(h, c1 - off)):
                a, b = x[r], y[r + off]
                tot = a.sum() + b.sum()
                if tot > 0.5 * a.shape[0]:
                    errs[r].append(float(np.abs(a - b).sum() / tot))
    if not any(errs):
        return None, places

    def state(r):
        if not errs[r]:
            return 0  # no detail: says nothing
        return 1 if float(np.median(errs[r])) < 0.25 else -1

    top = c0
    for r in range(c0, -1, -1):
        st = state(r)
        if st < 0:
            break
        if st > 0:
            top = r
    bottom = c1
    for r in range(c1, h):
        st = state(r)
        if st < 0:
            break
        if st > 0:
            bottom = r + 1
    return (top, bottom), places


def content_band(grays: list[np.ndarray]) -> tuple[int, int, list[Placement]]:
    """Rows of the chat area: from scrolling where shots overlap, otherwise from
    rows that stay the same in every shot. Also returns how the shots lined up
    on the middle of the screen."""
    band, middle = find_content_band(grays)
    return (*(band or detect_chrome(grays)), middle)


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
    change = np.abs(stack - stack[0]).max(axis=0).mean(axis=0)  # per column
    moving = change > 2.0
    gap = max(2, int(w * 0.01))
    runs, start, quiet = [], None, 0
    for x in range(w):
        if moving[x]:
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
