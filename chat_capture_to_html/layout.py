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
    score: float | None   # mean row difference of the match (0 = identical)


def to_gray(img: np.ndarray) -> np.ndarray:
    return (img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114).astype(np.float32)


def detect_chrome(grays: list[np.ndarray], tol: float = 10.0) -> tuple[int, int]:
    """Return (top, bottom) rows of the content band.

    Rows that look the same in every screenshot are screen chrome. Short runs of
    changing rows inside the chrome (a clock, a translucent blurred bar) are
    tolerated as long as a static row follows within 2% of the height.
    """
    h = grays[0].shape[0]
    if len(grays) < 2:
        return int(h * 0.11), int(h * 0.91)
    stack = np.stack(grays)
    diff = np.abs(stack - stack[0]).mean(axis=2).max(axis=0)  # per row, worst shot
    static = diff <= tol
    gap = max(2, int(h * 0.02))

    def scan(rows):
        edge, run = None, 0
        for r in rows:
            if static[r]:
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
    h, w = band.shape
    w2 = w - w % SIG_BINS
    return band[:, :w2].reshape(h, SIG_BINS, w2 // SIG_BINS).mean(axis=2)


def _scores(sa: np.ndarray, sb: np.ndarray, candidates, min_overlap: int) -> list[tuple[float, int]]:
    h = sa.shape[0]
    out = []
    for d in candidates:
        # row r of B shows the same content as row r + d of A
        a0, a1 = max(0, d), min(h, h + d)
        if a1 - a0 < min_overlap:
            continue
        a = sa[a0:a1]
        b = sb[a0 - d:a1 - d]
        if a.std() < 1.5:  # featureless overlap proves nothing
            continue
        out.append((float(np.abs(a - b).mean()), d))
    return out


def match_offset(sa: np.ndarray, sb: np.ndarray, max_score: float) -> tuple[int, float] | None:
    """Scroll offset d so that B[r] == A[r + d], or None when the bands do not overlap."""
    h = sa.shape[0]
    min_overlap = max(20, int(h * 0.08))
    step = 4
    coarse = _scores(sa[::step], sb[::step], range(-(h // step) + 1, h // step), max(5, min_overlap // step))
    if not coarse:
        return None
    coarse.sort()
    best = None
    for _, dc in coarse[:5]:
        for score, d in _scores(sa, sb, range(dc * step - step, dc * step + step + 1), min_overlap):
            if d != 0 and (best is None or score < best[0]):
                best = (score, d)
    if best is None or best[0] > max_score:
        return None
    return best[1], best[0]


def place_shots(bands: list[np.ndarray], max_score: float = 6.0) -> list[Placement]:
    """Put every shot on a shared axis, matching each one against the previous
    shot first and then against every shot already placed."""
    sigs = [row_signature(b) for b in bands]
    placements: list[Placement] = []
    segment = 0
    for i, sig in enumerate(sigs):
        if i == 0:
            placements.append(Placement(0, 0, None, None))
            continue
        order = [i - 1] + [j for j in range(i - 2, -1, -1)]
        found = None
        for j in order:
            m = match_offset(sigs[j], sig, max_score)
            if m is not None:
                found = (j, *m)
                break
        if found is None:
            segment += 1
            placements.append(Placement(segment, 0, None, None))
        else:
            j, d, score = found
            pj = placements[j]
            placements.append(Placement(pj.segment, pj.y + d, j, score))
    return placements
