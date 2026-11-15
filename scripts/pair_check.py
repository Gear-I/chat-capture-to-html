"""Numbers-only look at why two screenshots of a capture were not joined.

    py scripts/pair_check.py CAPTURE_FOLDER A B SHIFT [A B SHIFT ...]

A and B are shot numbers as --diagnose prints them (#A is the earlier shot),
SHIFT the "best match ... at SHIFT px" it printed for #B. For each pair:

- the runs of rows in #B that clearly differ from #A, with the column strips
  they fall in, and whether each run matches when moved by a few pixels on its
  own (a bubble or line that sat a little higher or lower in one shot);
- the picture candidates in both shots, with the measures used to tell a
  photo or GIF from a bubble.

Only row and column numbers and measures are printed: no text, no names, no
image. The capture folder is only read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from chat_capture_to_html.bubbles import background_color
from chat_capture_to_html.capture import load_capture
from chat_capture_to_html.layout import content_band, row_signature, to_gray

STRIPS = 64


def strong_cells(a, b):
    s = np.ones(a.shape, bool)
    for o in (-1, 0, 1):
        bb = np.roll(b, o, axis=0)
        t = a + bb
        s &= (np.abs(a - bb) > 0.6 * t) & (t > 2.0)
    return s


def runs(mask):
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    return out


def picture_measures(img, block=8):
    bg = background_color(img)
    h, w, _ = img.shape
    hb, wb = h // block, w // block
    im = img[:hb * block, :wb * block].astype(np.int16)
    blocks = im.astype(np.float32).reshape(hb, block, wb, block, 3)
    mean = blocks.mean(axis=(1, 3))
    std = blocks.std(axis=(1, 3)).mean(axis=2)
    off = (np.abs(mean - np.array(bg, np.float32)).sum(axis=2) > 30) | (std > 20)
    smooth = np.zeros(im.shape[:2], bool)
    smooth[:-1, :-1] = ((np.abs(im[:-1, 1:] - im[:-1, :-1]).max(axis=2) <= 4)
                        & (np.abs(im[1:, :-1] - im[:-1, :-1]).max(axis=2) <= 4))
    ink = np.abs(im - np.array(bg, np.int16)).sum(axis=2) > 30
    n_ink = ink.reshape(hb, block, wb, block).sum(axis=(1, 3))
    n_sm = (ink & smooth).reshape(hb, block, wb, block).sum(axis=(1, 3))
    n, lab, st, _ = cv2.connectedComponentsWithStats(off.astype(np.uint8), connectivity=8)
    rows = []
    for i in range(1, n):
        x, y, bw, bh, area = st[i]
        if bw < wb * 0.3 or bh < hb * 0.05:
            continue
        sel = lab == i
        cols = mean[sel]
        same = (np.abs(cols - np.median(cols, axis=0)).max(axis=1) <= 24).mean()
        smooth_share = n_sm[sel].sum() / max(1, n_ink[sel].sum())
        sv = np.linalg.svd(cols - cols.mean(axis=0), compute_uv=False)
        rows.append((y * block, (y + bh) * block, x * block, (x + bw) * block, area / (bw * bh), same, smooth_share,
                     sv[2] / sv[0] if sv[0] else 0.0))
    return bg, rows


def main(argv):
    if len(argv) < 4 or (len(argv) - 1) % 3:
        print(__doc__)
        return 2
    cap = load_capture(Path(argv[0]))
    imgs = [np.asarray(Image.open(s.path).convert("RGB")) for s in cap.shots]
    grays = [to_gray(i.astype(np.float32)) for i in imgs]
    top, bottom, _ = content_band(grays)
    print(f"{len(imgs)} shots, chat area rows {top}-{bottom}")
    sigs = [row_signature(g[top:bottom]) for g in grays]
    h = bottom - top
    w = imgs[0].shape[1]
    sw = w / STRIPS
    for k in range(1, len(argv), 3):
        ia, ib, d = int(argv[k]) - 1, int(argv[k + 1]) - 1, int(argv[k + 2])
        sa, sb = sigs[ia], sigs[ib]
        a0, a1 = max(0, d), min(h, h + d)
        a, b = sa[a0:a1], sb[a0 - d:a1 - d]
        strong = strong_cells(a, b)
        ink = (a.sum(1) + b.sum(1)) > 0.5 * a.shape[1]
        bad = strong.any(1) & ink
        print(f"\n#{ia + 1} -> #{ib + 1} at {d:+d}: {bad.sum()} of {ink.sum()} text rows differ "
              f"({100 * bad.sum() / max(1, ink.sum()):.1f}%)")
        merged = []
        for r0, r1 in runs(bad):
            if merged and r0 - merged[-1][1] <= 6:
                merged[-1] = (merged[-1][0], r1)
            else:
                merged.append((r0, r1))
        for r0, r1 in merged:
            cols = np.where(strong[r0:r1].any(0))[0]
            c0, c1 = int(cols.min() * sw), int((cols.max() + 1) * sw)
            # does this run match when moved on its own?
            best = None
            for s in range(-12, 13):
                rb0, rb1 = r0 + (a0 - d) + s, r1 + (a0 - d) + s
                if rb0 < 0 or rb1 > h:
                    continue
                rem = strong_cells(a[r0:r1], sb[rb0:rb1]).any(1).mean()
                if best is None or rem < best[1]:
                    best = (s, rem)
            yb0 = top + r0 + (a0 - d)
            yb1 = top + r1 + (a0 - d)
            ya0 = top + r0 + a0
            print(f"  rows {yb0}-{yb1} of #{ib + 1} (rows {ya0}-{ya0 + r1 - r0} of #{ia + 1}), columns {c0}-{c1}: "
                  f"{100 * bad[r0:r1].mean():.0f}% differ; moved on its own by {best[0]:+d} px: "
                  f"{100 * best[1]:.0f}% differ")
        for i in (ia, ib):
            bg, pics = picture_measures(imgs[i])
            print(f"  shot #{i + 1} (background {bg}): large areas off the background")
            for y0, y1, x0, x1, fill, same, smooth, r3 in pics:
                print(f"    rows {y0}-{y1}, columns {x0}-{x1}: filled {fill:.2f}, main colour {same:.2f}, "
                      f"smooth {smooth:.2f}, colour spread {r3:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
