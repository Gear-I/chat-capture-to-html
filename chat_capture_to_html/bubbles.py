"""Find message bubbles in a content band.

A bubble is a large, smooth area whose colour differs from the chat background.
Text inside it leaves holes but does not split it, and the thin gap between two
bubbles (background colour) keeps neighbours apart.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Bubble:
    x0: int
    y0: int
    x1: int
    y1: int
    color: tuple[int, int, int]
    side: str            # "left", "right" or "center"
    cut_top: bool        # runs into the top edge of the band
    cut_bottom: bool
    outside: np.ndarray | None = None  # True where the bounding box is not bubble (rounded corners, neighbours)

    @property
    def complete(self) -> bool:
        return not (self.cut_top or self.cut_bottom)


def background_color(band: np.ndarray) -> tuple[int, int, int]:
    """Most common colour in the outermost 2% of columns on each side."""
    h, w, _ = band.shape
    m = max(2, int(w * 0.02))
    edge = np.concatenate([band[:, :m].reshape(-1, 3), band[:, -m:].reshape(-1, 3)])
    q = (edge // 4).astype(np.int32)
    keys = q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    k = vals[counts.argmax()]
    sel = edge[keys == k]
    return tuple(int(v) for v in np.median(sel, axis=0))


def side_of(x0: int, x1: int, width: int) -> str:
    left_gap, right_gap = x0, width - x1
    if abs(left_gap - right_gap) < width * 0.06:
        return "center"
    return "left" if left_gap < right_gap else "right"


def find_bubbles(band: np.ndarray, bg: tuple[int, int, int] | None = None) -> list[Bubble]:
    h, w, _ = band.shape
    bg = bg or background_color(band)
    img = band.astype(np.int16)
    not_bg = np.abs(img - np.array(bg, dtype=np.int16)).sum(axis=2) > 30
    dx = np.zeros((h, w), bool)
    dy = np.zeros((h, w), bool)
    dx[:, :-1] = np.abs(img[:, 1:] - img[:, :-1]).max(axis=2) <= 4
    dy[:-1] = np.abs(img[1:] - img[:-1]).max(axis=2) <= 4
    mask = (not_bg & dx & dy).astype(np.uint8)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    min_w = max(40, int(w * 0.08))
    min_h = max(20, int(w * 0.04))
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < min_w or bh < min_h or bw > w * 0.97:
            continue
        if area / float(bw * bh) < 0.45:
            continue
        boxes.append((i, x, y, x + bw, y + bh, area))

    bubbles = []
    for i, x0, y0, x1, y1, area in boxes:
        # drop anything sitting inside a bigger bubble (e.g. a quoted reply box)
        if any(o is not None and o[0] != i and o[1] <= x0 and o[2] <= y0 and o[3] >= x1 and o[4] >= y1 and o[5] > area
               for o in boxes):
            continue
        comp = labels[y0:y1, x0:x1] == i
        color = tuple(int(v) for v in np.median(band[y0:y1, x0:x1][comp], axis=0))
        bubbles.append(Bubble(int(x0), int(y0), int(x1), int(y1), color, side_of(x0, x1, w),
                              cut_top=y0 <= 1, cut_bottom=y1 >= h - 1, outside=_outside(comp)))
    bubbles.sort(key=lambda b: (b.y0, b.x0))
    return bubbles


def _outside(comp: np.ndarray) -> np.ndarray:
    """Pixels of the box reachable from its border without crossing the bubble.
    Text holes inside the bubble are enclosed, so they stay False."""
    h, w = comp.shape
    free = np.pad((~comp).astype(np.uint8), 1, constant_values=1)
    ff = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(free, ff, (0, 0), 2, flags=4)
    return free[1:-1, 1:-1] == 2


def luminance(c: tuple[int, int, int]) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
