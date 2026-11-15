"""Build a synthetic UFADE Chat capture folder (no real messages) for tests.

    python tests/make_fixture.py OUT_DIR [--style imessage|ios|ios26-dark|ipad|whatsapp|both]

``ios`` is iMessage drawn the way iOS really composites it: the chat scrolls
under translucent blurred bars and blue bubbles are shaded by screen position.
``ipad`` is the same chat on an iPad, beside the conversation list.
``ios-dark`` and ``ipad-dark`` are the same in dark mode.
``ios26-dark`` is iOS 26 in dark mode: no solid bars, the chat blurs as it slides
under the title, and the contact's name floats over it in a pill.

Writes ``OUT_DIR/screenshots/<App>/<chat>/`` with overlapping screenshots taken
while "scrolling up" from the newest message, a ``.txt`` SHA-256 next to each PNG
the way UFADE writes them, and ``expected.json`` with the true conversation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 1170, 2532
HEADER, FOOTER = 300, 250

CONVO = [
    ("date", "Montag, 3. November"),
    ("left", "Hi! Bist du morgen im Büro?"),
    ("right", "Ja, ab 9 Uhr. Warum?"),
    ("left", "Ich bringe die Unterlagen für die Prüfung mit."),
    ("left", "Und den Schlüssel für das Lager."),
    ("right", "Super, danke dir."),
    ("right", "Can you also send the invoice from October? The total was 1.284,50 EUR."),
    ("left", "Sure, I will email it tonight."),
    ("date", "Dienstag, 4. November"),
    ("left", "Bin unterwegs, Stau auf der A3. Ungefähr 20 Minuten später."),
    ("right", "Kein Problem."),
    ("left", "Danke! Grüße an alle."),
    ("right", "Meeting moved to room 4.12, see you there."),
    ("left", "Okay 👍 on my way"),
    ("right", "Last message in this test chat."),
]


def font(size):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def wrap(draw, text, f, maxw):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if draw.textlength(t, font=f) <= maxw:
            cur = t
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def render_chat(style, rounds=1, pictures=False):
    """Draw the whole chat as one tall image. With rounds > 1 the conversation
    repeats, each round numbered so no two messages look the same. With
    ``pictures`` an animated picture (a GIF) follows the fourth message of each
    round; its box is in ``tall.info["pictures"]`` (see paint_frames)."""
    convo = [(k, t if r == 0 else f"{t} [{r + 1}]") for r in range(rounds) for k, t in CONVO]
    if pictures:
        convo = [x for r in range(rounds) for x in
                 convo[r * len(CONVO):r * len(CONVO) + 4] + [("picture", "")] + convo[r * len(CONVO) + 4:(r + 1) * len(CONVO)]]
    boxes = []
    wa = style == "whatsapp"
    dark = style.endswith("-dark")
    bg = (239, 234, 226) if wa else (0, 0, 0) if dark else (255, 255, 255)
    col = {"left": (255, 255, 255) if wa else (38, 38, 41) if dark else (233, 233, 235),
           "right": (217, 253, 211) if wa else (10, 132, 255)}
    fg = {"left": (17, 27, 33) if wa else (255, 255, 255) if dark else (0, 0, 0),
          "right": (17, 27, 33) if wa else (255, 255, 255)}
    meta_fg = (142, 142, 147) if dark else (102, 102, 110)
    f, fs = font(51), font(36)
    tall = Image.new("RGB", (W, 6000 * rounds), bg)
    d = ImageDraw.Draw(tall)
    y, prev, expected = 40, None, []
    minute = 9 * 60 + 2
    for kind, text in convo:
        if kind == "date":
            y += 40
            tw = d.textlength(text, font=fs)
            if wa:
                d.rounded_rectangle(((W - tw) / 2 - 24, y - 8, (W + tw) / 2 + 24, y + 56), 20, fill=(255, 255, 255))
            d.text(((W - tw) / 2, y), text, font=fs, fill=meta_fg)
            expected.append({"side": "center", "text": text})
            y += 90
            prev = None
            continue
        if kind == "picture":  # drawn per screenshot by paint_frames; not a message the tool lists
            y += 34
            boxes.append((40, y, 40 + int(W * 0.6), y + 560))
            y += 560
            prev = None
            continue
        y += 8 if prev == kind else 34
        lines = wrap(d, text, f, W * 0.62)
        tw = max(d.textlength(l, font=f) for l in lines)
        stamp = f"{minute // 60:02d}:{minute % 60:02d}"
        minute += 3
        bw = tw + 72 + (150 if wa else 0)
        bh = len(lines) * 62 + 52
        x0 = 40 if kind == "left" else W - 40 - bw
        d.rounded_rectangle((x0, y, x0 + bw, y + bh), 48, fill=col[kind])
        for i, l in enumerate(lines):
            d.text((x0 + 36, y + 22 + i * 62), l, font=f, fill=fg[kind])
        if wa:
            d.text((x0 + bw - 130, y + bh - 56), stamp, font=fs, fill=(102, 119, 129))
        expected.append({"side": kind, "text": " ".join(lines), "time": stamp if wa else None})
        y += bh
        prev = kind
    tall = tall.crop((0, 0, W, y + 60))
    tall.info["pictures"] = boxes
    paint_frames(tall, 0)
    return tall, expected, bg


GIF_COLOURS = [(220, 60, 60), (60, 200, 80), (70, 90, 230), (230, 200, 60), (200, 80, 200), (60, 200, 200)]


def paint_frames(tall, frame):
    """Draw frame ``frame`` of each animated picture: soft colour patches with
    grain, different in every frame, the way a GIF looks different from one
    screenshot to the next."""
    import numpy as np
    for n, (x0, y0, x1, y1) in enumerate(tall.info.get("pictures", [])):
        rng = np.random.default_rng(1000 * n + frame)
        h, w = y1 - y0, x1 - x0
        yy, xx = np.mgrid[0:h, 0:w] / max(h, w)
        a = np.zeros((h, w, 3))
        order = rng.permutation(len(GIF_COLOURS))  # at least four different colours in every frame
        for k in range(6):
            cy, cx, r = rng.random(3) * [1, 1, 0.4] + [0, 0, 0.1]
            a += np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / r ** 2)[..., None] * np.array(GIF_COLOURS[order[k % 4]])
        a = a / a.max() * 190 + 20
        grain = rng.random((h, w, 1)) < 0.35  # real GIF frames: grain or detail on about a third of pixels
        a += grain * rng.normal(0, 14, (h, w, 1))
        pic = Image.fromarray(a.clip(0, 255).astype("uint8"))
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), 40, fill=255)
        tall.paste(pic, (x0, y0), m)


def _ios_screen(tall, top, bg, clock, title, gradient, dark=False):
    """Like real iOS Messages: the chat runs under translucent, blurred bars,
    and blue bubbles are shaded by their position on the screen."""
    img = Image.new("RGB", (W, H), bg)
    src_top = top - HEADER
    win = tall.crop((0, max(src_top, 0), W, min(src_top + H, tall.height)))
    img.paste(win, (0, max(-src_top, 0)))
    if gradient:
        import numpy as np
        a = np.asarray(img).astype(np.int16)
        blue = (np.abs(a - np.array([10, 132, 255])).sum(axis=2) < 40)
        ys = np.arange(H)[:, None].repeat(W, axis=1) / H
        grad = np.stack([10 + 110 * ys, 132 - 70 * ys, 255 - 20 * ys], axis=2)
        a[blue] = grad[blue]
        img = Image.fromarray(a.clip(0, 255).astype("uint8"))
    for y0, y1 in ((0, HEADER), (H - FOOTER, H)):
        region = img.crop((0, y0, W, y1)).filter(ImageFilter.GaussianBlur(30))
        bar = (28, 28, 30) if dark else (247, 247, 247)
        img.paste(Image.blend(region, Image.new("RGB", region.size, bar), 0.8), (0, y0))
    d = ImageDraw.Draw(img)
    ink = (255, 255, 255) if dark else (0, 0, 0)
    d.text((80, 50), clock, font=font(48), fill=ink)
    tw = d.textlength(title, font=font(54))
    d.text(((W - tw) / 2, 190), title, font=font(54), fill=ink)
    d.rounded_rectangle((120, H - FOOTER + 40, W - 120, H - FOOTER + 140), 50, width=3,
                        outline=(60, 60, 64) if dark else (200, 200, 200), fill=(28, 28, 30) if dark else (255, 255, 255))
    return img


def _spring(img, bg, amp, phase):
    """Bubbles caught while the chat is still settling after a scroll: each
    block of content (between empty rows) sits up to ``amp`` pixels away from
    its resting place, by an amount that changes slowly down the screen."""
    import numpy as np
    a = np.asarray(img)
    blank = (a == np.array(bg, np.uint8)).all(axis=2).all(axis=1)
    out = Image.new("RGB", img.size, bg)
    y, h = 0, a.shape[0]
    while y < h:
        if blank[y]:
            y += 1
            continue
        y1 = y
        while y1 < h and not blank[y1]:
            y1 += 1
        dy = int(round(amp * np.sin(2 * np.pi * (y + y1) / 2 / 1400 + phase)))
        out.paste(img.crop((0, y, img.width, y1)), (0, y + dy))
        y = y1
    return out


def _ios26_screen(tall, top, bg, clock, title, dark=True, spring=None):
    """iOS 26 Messages: no solid bars. The chat runs to both edges of the
    screen; near the top and bottom it fades into a soft blur, and floating
    controls (back button, contact picture and name pill, input field) are
    drawn over it in fixed places, partly over chat that is still readable.
    ``spring`` = (amp, phase) shows bubbles not yet settled after a scroll."""
    import numpy as np
    img = Image.new("RGB", (W, H), bg)
    src_top = top - HEADER
    win = tall.crop((0, max(src_top, 0), W, min(src_top + H, tall.height)))
    img.paste(win, (0, max(-src_top, 0)))
    if spring:
        img = _spring(img, bg, *spring)
    sharp = np.asarray(img).astype(np.float32)
    soft = np.asarray(img.filter(ImageFilter.GaussianBlur(14))).astype(np.float32) * 0.55
    ramp = np.zeros(H, np.float32)  # 1 = fully blurred, 0 = untouched
    ramp[:TOP_FADE] = np.linspace(1, 0, TOP_FADE) ** 1.5
    ramp[H - BOTTOM_FADE:] = np.linspace(0, 1, BOTTOM_FADE) ** 1.5
    a = sharp * (1 - ramp[:, None, None]) + soft * ramp[:, None, None]
    img = Image.fromarray(a.clip(0, 255).astype("uint8"))
    d = ImageDraw.Draw(img)
    ink = (255, 255, 255) if dark else (0, 0, 0)
    glass = (44, 44, 48) if dark else (238, 238, 240)
    d.text((110, 45), clock, font=font(48), fill=ink)
    d.ellipse((40, 115, 140, 215), fill=glass)  # back
    d.ellipse((W - 140, 115, W - 40, 215), fill=glass)  # video call
    d.ellipse((W / 2 - 60, 115, W / 2 + 60, 235), fill=(70, 50, 120))  # contact picture
    tw = d.textlength(title, font=font(44))
    d.rounded_rectangle((W / 2 - tw / 2 - 40, PILL_TOP, W / 2 + tw / 2 + 40, PILL_BOTTOM), 40,
                        fill=glass, outline=(70, 70, 76) if dark else (215, 215, 220), width=2)
    d.text((W / 2 - tw / 2, PILL_TOP + 12), title, font=font(44), fill=ink)
    d.ellipse((50, H - 170, 150, H - 70), fill=glass)  # plus
    d.rounded_rectangle((180, H - 170, W - 60, H - 70), 50, fill=glass)  # input field
    return img


# iOS 26 layout (see _ios26_screen): where the blur fades out, and the name pill,
# whose lower edge reaches just past the end of the blur
TOP_FADE, BOTTOM_FADE = 420, 300
PILL_TOP, PILL_BOTTOM = 250, 345

IPAD_W, IPAD_H, LIST_W = 1640, 2360, 470


def _ipad_screen(tall, top, bg, clock, title, subpixel=0.0, list_tint=0, dark=False):
    """iPad Messages in portrait: the conversation list stays on the left while
    the chat pane on the right scrolls. The status bar spans the full width.
    ``subpixel`` redraws the chat as if scrolling stopped between two pixels."""
    chat = _ios_screen(tall, top, bg, "", title, gradient=True, dark=dark)
    if subpixel:
        body = chat.crop((0, HEADER, W, H - FOOTER))
        body = body.transform(body.size, Image.AFFINE, (1, 0, 0, 0, 1, subpixel), resample=Image.BILINEAR)
        chat.paste(body, (0, HEADER))
    pane = Image.new("RGB", (W, IPAD_H), bg)
    pane.paste(chat.crop((0, 0, W, IPAD_H - FOOTER)), (0, 0))
    pane.paste(chat.crop((0, H - FOOTER, W, H)), (0, IPAD_H - FOOTER))
    # the list is not perfectly still either: its material shifts shade a little from shot to shot
    img = Image.new("RGB", (IPAD_W, IPAD_H), (28 + list_tint,) * 3 if dark else (255 - list_tint,) * 3)
    img.paste(pane, (LIST_W, 0))
    d = ImageDraw.Draw(img)
    ink = (255, 255, 255) if dark else (0, 0, 0)
    d.text((40, 30), clock, font=font(40), fill=ink)
    d.text((40, 190), "Messages", font=font(64), fill=ink)
    for i, (name, preview) in enumerate([(title, "Last message in this test chat."), ("Alex", "See you tomorrow"),
                                         ("Sam", "Thanks!"), ("Pat", "Call me back"), ("Jo", "On my way")]):
        y = 320 + i * 170
        if i == 0:  # the open conversation is highlighted
            d.rounded_rectangle((16, y - 10, LIST_W - 16, y + 150), 24, fill=(10, 132, 255))
        c = (255, 255, 255) if i == 0 else ink
        d.text((48, y + 10), name, font=font(44), fill=c)
        d.text((48, y + 75), preview[:18], font=font(34), fill=c if i == 0 else (142, 142, 147) if dark else (120, 120, 128))
    line = (56 + list_tint,) * 3 if dark else (210 - list_tint, 210 - list_tint, 214 - list_tint)
    d.line((LIST_W - 1, 0, LIST_W - 1, IPAD_H), fill=line, width=2)
    return img


def screen(tall, top, bg, clock, title, style, subpixel=0.0, list_tint=0, spring=None):
    dark = style.endswith("-dark")
    if style.startswith("ipad"):
        return _ipad_screen(tall, top, bg, clock, title, subpixel, list_tint, dark)
    if style.startswith("ios26"):
        return _ios26_screen(tall, top, bg, clock, title, dark, spring)
    if style.startswith("ios"):
        return _ios_screen(tall, top, bg, clock, title, gradient=True, dark=dark)
    img = Image.new("RGB", (W, H), bg)
    if style == "whatsapp":  # wallpaper fixed to the screen, not the chat
        dd = ImageDraw.Draw(img)
        for yy in range(HEADER, H - FOOTER, 90):
            for xx in range(30, W, 120):
                dd.ellipse((xx, yy, xx + 6, yy + 6), fill=(226, 219, 207))
    ch = H - HEADER - FOOTER
    window = tall.crop((0, top, W, top + ch))
    if style == "whatsapp":  # paste only non-background pixels so the wallpaper shows through
        plain = Image.new("RGB", window.size, bg)
        mask = ImageChops.difference(window, plain).convert("L").point(lambda v: 255 if v > 0 else 0)
        img.paste(window, (0, HEADER), mask)
    else:
        img.paste(window, (0, HEADER))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, HEADER), fill=(247, 247, 247))
    d.text((80, 50), clock, font=font(48), fill=(0, 0, 0))
    tw = d.textlength(title, font=font(54))
    d.text(((W - tw) / 2, 190), title, font=font(54), fill=(0, 0, 0))
    d.line((0, HEADER - 1, W, HEADER - 1), fill=(200, 200, 200), width=2)
    d.rectangle((0, H - FOOTER, W, H), fill=(247, 247, 247))
    d.rounded_rectangle((120, H - FOOTER + 40, W - 120, H - FOOTER + 140), 50, outline=(200, 200, 200), width=3, fill=(255, 255, 255))
    return img


DEVICE = {"udid": "00008101-000A1B2C3D4E5F6A", "product_type": "iPad13,18", "os_version": "26.1",
          "name": "Test iPad"}


def make(out: Path, style: str, seed: int = 1, corrupt: bool = True, sidecar: str = "txt",
         device: dict | None = None, chat: str = "Test Kontakt") -> Path:
    """Write a synthetic capture. ``sidecar`` is "txt" (as UFADE does today),
    "json" (the proposed per-screenshot JSON naming the device) or "both"."""
    rnd = random.Random(seed)
    app = "WhatsApp" if style == "whatsapp" else "Messages"
    render_style = ("imessage-dark" if style.endswith("-dark")
                    else "imessage" if style in ("ios", "ipad") else style)
    folder = out / "screenshots" / app / chat
    folder.mkdir(parents=True, exist_ok=True)
    ios26 = style.startswith("ios26")
    tall, expected, bg = render_chat(render_style, rounds=2 if ios26 else 1, pictures=ios26)
    ch = (IPAD_H if style.startswith("ipad") else H) - HEADER - FOOTER
    tops, t = [], tall.height - ch
    while True:  # UFADE "Up": start at the newest message, scroll towards older ones
        tops.append(max(t, 0))
        if t <= 0:
            break
        t -= rnd.randint(int(ch * 0.3), int(ch * 0.55))
    when = datetime(2026, 11, 12, 14, 30, 5)
    last, n = None, 0
    for i, top in enumerate(tops):
        clock = "9:41" if i < len(tops) // 2 else "9:42"
        sub = rnd.choice([0.25, 0.5, 0.75]) if style.startswith("ipad") and i else 0.0
        if tall.info.get("pictures"):
            paint_frames(tall, i)  # the GIF has moved on by the time of each shot
        spring = (rnd.uniform(2, 4.5), rnd.uniform(0, 6.28)) if ios26 and i else None  # up to 9 px apart; 8 seen on a real capture
        img = screen(tall, top, bg, clock, chat, style, sub, list_tint=(i % 3) * 2, spring=spring)
        stamp = when.strftime("%m_%d_%Y_%H_%M_%S")
        n = n + 1 if stamp == last else 0  # UFADE adds _1, _2, ... within the same second
        name = f"{chat}_{stamp}" + (f"_{n}" if n else "")
        last = stamp
        png = folder / f"{name}.png"
        img.save(png)
        digest = hashlib.sha256(png.read_bytes()).hexdigest()
        if corrupt and i == 1:
            digest = "0" * 64  # one deliberately wrong hash
        if sidecar in ("txt", "both"):
            (folder / f"{name}.txt").write_text(digest)
        if sidecar in ("json", "both"):
            record = {"schema": "ufade-shot/1", "file": png.name, "sha256": digest,
                      "captured_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "device": device or DEVICE,
                      "app": app, "chat": chat, "tool": {"name": "UFADE", "version": "test"}}
            (folder / f"{name}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        when += timedelta(seconds=rnd.choice([0, 1, 1, 2]))
    (out / f"expected_{style}.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--style", choices=["imessage", "ios", "ios-dark", "ios26-dark", "ipad", "ipad-dark", "whatsapp", "both"],
                    default="both")
    a = ap.parse_args()
    for s in (["imessage", "whatsapp"] if a.style == "both" else [a.style]):
        print(make(a.out, s))
