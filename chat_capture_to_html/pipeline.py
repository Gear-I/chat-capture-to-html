"""Turn a verified capture into an ordered list of messages."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .bubbles import Bubble, background_color, find_bubbles, luminance
from .capture import Capture
from .layout import Placement, column_change, content_band, find_chat_pane, place_shots, to_gray
from .ocr import Line

TIME_TAIL_RE = re.compile(r"^(?P<body>.*?)[\s,]*(?P<time>\d{1,2}[:.\-]\d{2}(?:\s?[AaPp]\.?\s?[Mm]\.?)?)[\s\W_vVwW]{0,4}$")
DATEISH_RE = re.compile(r"\d{1,2}[:.]\d{2}|\d{1,2}\.\s?\d{1,2}\.|\b(today|yesterday|heute|gestern|mon|tue|wed|thu|fri|sat|sun|"
                        r"montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
                        r"mär|mai|okt|dez)", re.I)


@dataclass
class Source:
    shot: int                    # index into Capture.shots
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in full-screenshot pixels
    complete: bool


@dataclass
class Message:
    kind: str                    # "bubble" or "meta"
    segment: int
    gy0: int
    gy1: int
    x0: int
    x1: int
    side: str = "center"
    color: tuple[int, int, int] | None = None
    color_group: int | None = None
    lines: list[str] = field(default_factory=list)
    conf: float = 0.0
    time: str | None = None
    sender_name: str | None = None
    sources: list[Source] = field(default_factory=list)
    text_from: list[int] = field(default_factory=list)
    is_name_label: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class ShotInfo:
    width: int = 0
    height: int = 0
    used: bool = False
    skip_reason: str = ""
    placement: Placement | None = None
    bubbles: int = 0


@dataclass
class ChatResult:
    capture: Capture
    shots: list[ShotInfo]
    messages: list[Message]          # in chat order, segments already arranged
    segment_order: list[int]
    content_top: int
    content_bottom: int
    header_text: str
    color_groups: list[dict]
    ocr_engine: str
    bubble_times_stripped: bool
    warnings: list[str]
    chat_left: int = 0               # columns of the chat pane (iPad: beside the conversation list)
    chat_right: int = 0
    middle_match: dict[int, Placement] = field(default_factory=dict)  # shot index -> first try on the middle rows
    column_change: list[float] = field(default_factory=list)  # per 1/40 of the width, for --diagnose


# ---------------------------------------------------------------- helpers

def _prep(crop: np.ndarray, fill: tuple[int, int, int], scale: int, pad: int = 12,
          outside: np.ndarray | None = None) -> np.ndarray:
    if outside is not None:
        crop = crop.copy()
        crop[outside] = fill
    gray = to_gray(crop.astype(np.float32))
    if luminance(fill) < 128:
        gray = 255 - gray
    gray = np.clip(gray, 0, 255).astype(np.uint8)
    if scale > 1:
        gray = np.kron(gray, np.ones((scale, scale), np.uint8))
    bgval = int(np.median(np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])))
    return np.pad(gray, pad, constant_values=bgval)


def _good(line: Line, min_conf: float) -> bool:
    return line.conf >= min_conf and any(ch.isalnum() for ch in line.text)


def _join_rows(lines: list[Line]) -> list[Line]:
    """Sparse OCR returns words in separate blocks; glue words that share a row."""
    lines = sorted(lines, key=lambda l: (l.y0 + l.y1) / 2)
    rows: list[list[Line]] = []
    for l in lines:
        mid = (l.y0 + l.y1) / 2
        for row in rows:
            r = row[0]
            if r.y0 <= mid <= r.y1:
                row.append(l)
                break
        else:
            rows.append([l])
    out = []
    for row in rows:
        row.sort(key=lambda l: l.x0)
        cur = row[0]
        for l in row[1:]:
            hgt = max(cur.y1 - cur.y0, l.y1 - l.y0)
            if l.x0 - cur.x1 < 2.5 * hgt:
                cur = Line(cur.text + " " + l.text, min(cur.conf, l.conf), cur.x0, min(cur.y0, l.y0), l.x1, max(cur.y1, l.y1))
            else:
                out.append(cur)
                cur = l
        out.append(cur)
    return sorted(out, key=lambda l: (l.y0, l.x0))


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


# ---------------------------------------------------------------- main

def build_chat(cap: Capture, engine, top: int | None = None, bottom: int | None = None,
               include_unverified: bool = True, min_conf: float = 45.0, progress=lambda s: None,
               left: int | None = None, right: int | None = None) -> ChatResult:
    warnings: list[str] = []
    infos = [ShotInfo() for _ in cap.shots]
    images: dict[int, np.ndarray] = {}
    for i, s in enumerate(cap.shots):
        if not s.verified and not include_unverified:
            infos[i].skip_reason = f"hash {s.status}; excluded (--verified-only)"
            continue
        try:
            with Image.open(s.path) as im:
                arr = np.asarray(im.convert("RGB"))
        except Exception as e:  # noqa: BLE001 - any decode failure just skips the shot
            infos[i].skip_reason = f"cannot decode PNG: {e}"
            continue
        infos[i].height, infos[i].width = arr.shape[:2]
        images[i] = arr

    if not images:
        return ChatResult(cap, infos, [], [], 0, 0, "", [], engine.describe(), False, ["no usable screenshots"])

    size = Counter(a.shape[:2] for a in images.values()).most_common(1)[0][0]
    for i in list(images):
        if images[i].shape[:2] != size:
            infos[i].skip_reason = f"size {images[i].shape[1]}x{images[i].shape[0]} differs from the other shots"
            warnings.append(f"{cap.shots[i].path.name}: skipped, different screen size")
            del images[i]
    idx = sorted(images)
    H, W = size
    grays = [to_gray(images[i].astype(np.float32)) for i in idx]
    # On an iPad the conversation list sits beside the chat and never scrolls:
    # work on the chat pane only, and shift boxes back to screenshot pixels.
    profile = column_change(grays) if len(grays) > 1 else np.zeros(W)
    if left is None and right is None:
        xo, x_end = find_chat_pane(grays)
    else:
        auto = find_chat_pane(grays)
        xo = auto[0] if left is None else max(0, min(left, W - 1))
        x_end = auto[1] if right is None else max(xo + 1, W - right)
    if (xo, x_end) != (0, W):
        images = {i: images[i][:, xo:x_end] for i in images}
        grays = [g[:, xo:x_end] for g in grays]
        W = x_end - xo
    middle: list[Placement] = []
    if top is None or bottom is None:
        t_auto, b_auto, middle = content_band(grays)
    else:
        t_auto, b_auto = top, bottom
    top = t_auto if top is None else top
    bottom = b_auto if bottom is None else (H - bottom if bottom >= 0 else H)
    bands = [images[i][top:bottom] for i in idx]
    bh = bottom - top

    progress(f"lining up {len(idx)} screenshots")
    places = place_shots([g[top:bottom] for g in grays])
    for k, i in enumerate(idx):
        infos[i].used = True
        pl = places[k]
        infos[i].placement = Placement(pl.segment, pl.y, idx[pl.matched_with] if pl.matched_with is not None else None,
                                       pl.score, pl.note)

    # ------------------------------------------------ bubbles on the shared axis
    inst = []  # (k, bubble, segment, gy0, gy1)
    bgs = []
    for k, band in enumerate(bands):
        bg = background_color(band)
        bgs.append(bg)
        bubbles = find_bubbles(band, bg)
        infos[idx[k]].bubbles = len(bubbles)
        pl = places[k]
        for b in bubbles:
            inst.append((k, b, pl.segment, pl.y + b.y0, pl.y + b.y1))

    uf = _UF(len(inst))
    for a in range(len(inst)):
        ka, ba, sa, a0, a1 = inst[a]
        for b in range(a + 1, len(inst)):
            kb, bb, sb, b0, b1 = inst[b]
            if ka == kb or sa != sb or ba.side != bb.side:
                continue
            inter = min(a1, b1) - max(a0, b0)
            xin = min(ba.x1, bb.x1) - max(ba.x0, bb.x0)
            if inter > 0.5 * min(a1 - a0, b1 - b0) and xin > 0.5 * min(ba.x1 - ba.x0, bb.x1 - bb.x0):
                uf.union(a, b)
    clusters: dict[int, list[int]] = {}
    for a in range(len(inst)):
        clusters.setdefault(uf.find(a), []).append(a)

    scale = 2 if W < 900 else 1
    messages: list[Message] = []
    progress(f"reading {len(clusters)} message bubbles")
    for members in clusters.values():
        members.sort(key=lambda a: inst[a][0])
        seg = inst[members[0]][2]
        gy0 = min(inst[a][3] for a in members)
        gy1 = max(inst[a][4] for a in members)
        sources = []
        for a in members:
            k, b, _, _, _ = inst[a]
            sources.append(Source(idx[k], (b.x0 + xo, b.y0 + top, b.x1 + xo, b.y1 + top), b.complete))
        complete = [a for a in members if inst[a][1].complete]
        if complete:
            # prefer hash-verified shots, then the copy furthest from either band edge
            chosen = [max(complete, key=lambda a: (cap.shots[idx[inst[a][0]]].verified,
                                                   min(inst[a][1].y0, bh - inst[a][1].y1)))]
        else:
            chosen = members
        got: list[tuple[float, Line, float, float]] = []  # (gy mid, line, gy0, gy1)
        for a in chosen:
            k, b, _, g0, _ = inst[a]
            crop = bands[k][b.y0:b.y1, b.x0:b.x1]
            lines = _join_rows(engine.read(_prep(crop, b.color, scale, outside=b.outside), block=True))
            for l in lines:
                if not _good(l, min_conf * 0.6):
                    continue
                ly0 = (l.y0 - 12) / scale
                ly1 = (l.y1 - 12) / scale
                if (b.cut_top and ly0 < 3) or (b.cut_bottom and ly1 > (b.y1 - b.y0) - 3):
                    continue  # glyphs sliced by the band edge
                got.append(((g0 + ly0 + g0 + ly1) / 2, l, g0 + ly0, g0 + ly1))
        got.sort(key=lambda t: t[0])
        kept: list[tuple[float, Line, float, float]] = []
        for g in got:
            if kept and abs(g[0] - kept[-1][0]) < 0.5 * (g[3] - g[2]):
                if g[1].conf > kept[-1][1].conf:
                    kept[-1] = g
                continue
            kept.append(g)
        b0 = inst[members[0]][1]
        colors = np.array([inst[a][1].color for a in members])
        msg = Message("bubble", seg, gy0, gy1, b0.x0, b0.x1, side=b0.side,
                      color=tuple(int(v) for v in np.median(colors, axis=0)),
                      lines=[g[1].text for g in kept],
                      conf=float(np.mean([g[1].conf for g in kept])) if kept else 0.0,
                      sources=sources, text_from=sorted({idx[inst[a][0]] for a in chosen}))
        messages.append(msg)

    # ------------------------------------------------ text outside bubbles
    progress("reading text between bubbles")
    metas: list[Message] = []
    for k, band in enumerate(bands):
        bg = bgs[k]
        masked = band.copy()
        for (kk, b, _, _, _) in inst:
            if kk == k:
                masked[max(0, b.y0 - 4):b.y1 + 4, max(0, b.x0 - 4):b.x1 + 4] = bg
        lines = _join_rows(engine.read(_prep(masked, bg, scale, pad=0), block=False))
        pl = places[k]
        for l in lines:
            if not _good(l, min_conf):
                continue
            y0, y1 = l.y0 / scale, l.y1 / scale
            x0, x1 = int(l.x0 / scale), int(l.x1 / scale)
            if y0 < 3 or y1 > bh - 3:
                continue
            g0, g1 = int(pl.y + y0), int(pl.y + y1)
            src = Source(idx[k], (x0 + xo, int(y0) + top, x1 + xo, int(y1) + top), True)
            dup = None
            for m in metas:
                if m.segment == pl.segment and abs((m.gy0 + m.gy1) / 2 - (g0 + g1) / 2) < 0.6 * (g1 - g0) \
                        and min(m.x1, x1) - max(m.x0, x0) > 0:
                    dup = m
                    break
            if dup:
                dup.sources.append(src)
                if l.conf > dup.conf:
                    dup.lines, dup.conf, dup.text_from = [l.text], l.conf, [idx[k]]
                continue
            metas.append(Message("meta", pl.segment, g0, g1, x0, x1, side="center", lines=[l.text], conf=l.conf,
                                 sources=[src], text_from=[idx[k]]))

    # ------------------------------------------------ order, segments, extras
    deltas = [p.y - places[p.matched_with].y for p in places if p.matched_with is not None]
    scrolled_up = sum(1 for d in deltas if d < 0) > sum(1 for d in deltas if d > 0)
    segs = sorted({p.segment for p in places}, reverse=scrolled_up)
    if len(segs) > 1:
        warnings.append(f"{len(segs) - 1} place(s) where consecutive screenshots did not overlap; "
                        "messages may be missing or repeated there")

    allm = messages + metas
    allm.sort(key=lambda m: (segs.index(m.segment), m.gy0, m.x0))

    for m in allm:
        if m.kind == "bubble" and m.side == "center":
            m.kind = "meta"
    _attach_names(allm, W)
    stripped = _strip_bubble_times(allm)
    groups = _color_groups(allm)
    header = _read_header(images[idx[0]][:top], engine) if top > H * 0.04 else ""
    for m in messages:
        if not m.lines:
            m.lines = ["[no text recognised — open the screenshot]"]
    return ChatResult(cap, infos, [m for m in allm if not m.is_name_label], segs, top, bottom, header, groups,
                      engine.describe(), stripped, warnings, xo, xo + W,
                      {idx[k]: Placement(p.segment, p.y, idx[p.matched_with] if p.matched_with is not None else None,
                                         p.score, p.note) for k, p in enumerate(middle)},
                      [float(c.mean()) for c in np.array_split(profile, 40)])


def _attach_names(allm: list[Message], width: int) -> None:
    """Small text just above a left-hand bubble is the sender's name (group chats)."""
    current = None
    for i, m in enumerate(allm):
        if m.kind == "bubble":
            if m.side == "left":
                m.sender_name = current
            else:
                current = None
            continue
        nxt = allm[i + 1] if i + 1 < len(allm) else None
        h = m.gy1 - m.gy0
        if (nxt is not None and nxt.kind == "bubble" and nxt.side == "left" and nxt.segment == m.segment
                and 0 <= nxt.gy0 - m.gy1 < 1.5 * h and abs(m.x0 - nxt.x0) < 0.1 * width
                and not DATEISH_RE.search(m.text)):
            m.is_name_label = True
            current = m.text.strip()
        else:
            current = None


def _strip_bubble_times(allm: list[Message]) -> bool:
    """Apps like WhatsApp print the time inside each bubble. Split it off, but only
    when most bubbles end in a time, so a message that merely ends in "18:30" in
    an app without in-bubble times is left alone."""
    bubbles = [m for m in allm if m.kind == "bubble" and m.lines]
    hits = [TIME_TAIL_RE.match(m.lines[-1]) for m in bubbles]
    if not bubbles or sum(1 for h in hits if h) < 0.5 * len(bubbles):
        return False
    for m, h in zip(bubbles, hits):
        if h:
            m.time = h["time"]
            body = h["body"].strip()
            if body:
                m.lines[-1] = body
            else:
                m.lines.pop()
    return True


def _color_groups(allm: list[Message]) -> list[dict]:
    groups: list[dict] = []
    for m in allm:
        if m.color is None:
            continue
        for gi, g in enumerate(groups):
            if sum(abs(a - b) for a, b in zip(g["color"], m.color)) < 45:
                m.color_group = gi
                g["sides"][m.side] += 1
                break
        else:
            m.color_group = len(groups)
            groups.append({"color": m.color, "sides": Counter({m.side: 1})})
    return groups


def _read_header(region: np.ndarray, engine) -> str:
    if region.size == 0:
        return ""
    h, w, _ = region.shape
    bg = background_color(region)
    try:
        lines = engine.read(_prep(region, bg, 1), block=False)
    except Exception:  # noqa: BLE001 - the header is a nice-to-have
        return ""
    lines = [l for l in _join_rows(lines) if _good(l, 50) and l.y0 > h * 0.25]
    if not lines:
        return ""
    best = min(lines, key=lambda l: abs((l.x0 + l.x1) / 2 - w / 2) - (l.y1 - l.y0))
    return best.text
