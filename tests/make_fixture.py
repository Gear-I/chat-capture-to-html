"""Build a synthetic UFADE Chat capture folder (no real messages) for tests.

    python tests/make_fixture.py OUT_DIR [--style imessage|whatsapp|both]

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

from PIL import Image, ImageChops, ImageDraw, ImageFont

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


def render_chat(style):
    wa = style == "whatsapp"
    bg = (239, 234, 226) if wa else (255, 255, 255)
    col = {"left": (255, 255, 255) if wa else (233, 233, 235), "right": (217, 253, 211) if wa else (10, 132, 255)}
    fg = {"left": (17, 27, 33) if wa else (0, 0, 0), "right": (17, 27, 33) if wa else (255, 255, 255)}
    f, fs = font(51), font(36)
    tall = Image.new("RGB", (W, 6000), bg)
    d = ImageDraw.Draw(tall)
    y, prev, expected = 40, None, []
    minute = 9 * 60 + 2
    for kind, text in CONVO:
        if kind == "date":
            y += 40
            tw = d.textlength(text, font=fs)
            if wa:
                d.rounded_rectangle(((W - tw) / 2 - 24, y - 8, (W + tw) / 2 + 24, y + 56), 20, fill=(255, 255, 255))
            d.text(((W - tw) / 2, y), text, font=fs, fill=(102, 102, 110))
            expected.append({"side": "center", "text": text})
            y += 90
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
    return tall.crop((0, 0, W, y + 60)), expected, bg


def screen(tall, top, bg, clock, title, style):
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


def make(out: Path, style: str, seed: int = 1, corrupt: bool = True) -> Path:
    rnd = random.Random(seed)
    app = "WhatsApp" if style == "whatsapp" else "Messages"
    chat = "Test Kontakt"
    folder = out / "screenshots" / app / chat
    folder.mkdir(parents=True, exist_ok=True)
    tall, expected, bg = render_chat(style)
    ch = H - HEADER - FOOTER
    tops, t = [], tall.height - ch
    while True:  # UFADE "Up": start at the newest message, scroll towards older ones
        tops.append(max(t, 0))
        if t <= 0:
            break
        t -= rnd.randint(int(ch * 0.3), int(ch * 0.55))
    when = datetime(2026, 11, 12, 14, 30, 5)
    last = None
    for i, top in enumerate(tops):
        clock = "9:41" if i < len(tops) // 2 else "9:42"
        img = screen(tall, top, bg, clock, chat, style)
        stamp = when.strftime("%m_%d_%Y_%H_%M_%S")
        name = f"{chat}_{stamp}" if stamp != last else f"{chat}_{stamp}_1"
        last = stamp
        png = folder / f"{name}.png"
        img.save(png)
        digest = hashlib.sha256(png.read_bytes()).hexdigest()
        if corrupt and i == 1:
            digest = "0" * 64  # one deliberately wrong hash
        (folder / f"{name}.txt").write_text(digest)
        when += timedelta(seconds=rnd.choice([0, 1, 1, 2]))
    (out / f"expected_{style}.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--style", choices=["imessage", "whatsapp", "both"], default="both")
    a = ap.parse_args()
    for s in (["imessage", "whatsapp"] if a.style == "both" else [a.style]):
        print(make(a.out, s))
