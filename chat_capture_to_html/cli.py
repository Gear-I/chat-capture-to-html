from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__
from .capture import HASH_OK, find_capture_folders, load_capture
from .ocr import OcrError, make_engine
from .pipeline import build_chat
from .report import render


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .") or "chat"


def _diagnose(res) -> None:
    sizes = {(i.width, i.height) for i in res.shots if i.used}
    print(f"  screen size: {', '.join(f'{w}x{h}' for w, h in sorted(sizes))}")
    print(f"  content band: rows {res.content_top}-{res.content_bottom}, columns {res.chat_left}-{res.chat_right}")
    if res.column_change:
        w = max(i.width for i in res.shots if i.used)
        step = w / len(res.column_change)
        print("  change between shots across the width (middle rows; 0 = never changes):")
        for k in range(0, len(res.column_change), 10):
            print("    " + "  ".join(f"{int(round((k + j) * step)):>4}:{v:5.1f}"
                                     for j, v in enumerate(res.column_change[k:k + 10])))
    if res.middle_match:
        print("  lining up on the middle of the screen (used to find the chat area):")
        for i, mp in sorted(res.middle_match.items()):
            if mp.matched_with is not None:
                d = mp.y - res.middle_match[mp.matched_with].y
                print(f"    #{i + 1}: matched #{mp.matched_with + 1}, scrolled {d:+d} px, diff {mp.score:.3f}")
            elif mp.note:
                print(f"    #{i + 1}: no match; previous shot: {mp.note}")
        print("  lining up on the chat area:")
    for n, (shot, info) in enumerate(zip(res.capture.shots, res.shots), 1):
        pl = info.placement
        if pl is None:
            print(f"  #{n}: not used ({info.skip_reason})")
        elif pl.matched_with is None:
            print(f"  #{n}: segment {pl.segment + 1} start" + (f"; previous shot: {pl.note}" if pl.note else "")
                  + f"; {info.bubbles} bubbles")
        else:
            print(f"  #{n}: matched #{pl.matched_with + 1}, scrolled {pl.y - res.shots[pl.matched_with].placement.y:+d} px, "
                  f"diff {pl.score:.3f}; {info.bubbles} bubbles")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="chat-capture-to-html",
        description="Turn a UFADE Chat capture folder into an HTML chat. Every PNG is checked against its "
                    ".txt SHA-256, OCR runs offline, and each message links to its screenshot and hash.")
    p.add_argument("folder", type=Path, help="a chat folder (screenshots/<app>/<chat>) or any folder above it")
    p.add_argument("-o", "--output", type=Path,
                   help="HTML file to write (one chat) or folder for the reports (several chats). Default: current folder")
    p.add_argument("--me", default="Device owner", help="label for right-hand bubbles (default: %(default)s)")
    p.add_argument("--them", help="label for left-hand bubbles (default: the chat folder name)")
    p.add_argument("--ocr", choices=["auto", "tesseract", "rapidocr"], default="auto",
                   help="OCR engine; auto prefers Tesseract, then RapidOCR")
    p.add_argument("--no-ocr", action="store_true",
                   help="read no text: show each message as it appears in its screenshot (no Tesseract needed)")
    p.add_argument("--lang", help="Tesseract languages, e.g. deu+eng (default: deu+eng where installed)")
    p.add_argument("--tesseract", help="path to tesseract executable")
    p.add_argument("--top", type=int, help="pixels of fixed header to ignore (default: detected)")
    p.add_argument("--bottom", type=int, help="pixels of fixed footer to ignore (default: detected)")
    p.add_argument("--left", type=int, help="pixels on the left that are not the chat, e.g. the iPad conversation list "
                                            "(default: detected)")
    p.add_argument("--right", type=int, help="pixels on the right that are not the chat (default: detected)")
    p.add_argument("--verified-only", action="store_true", help="leave out screenshots whose hash does not verify")
    p.add_argument("--diagnose", action="store_true",
                   help="print screen size, header/footer rows and how each screenshot was lined up "
                        "(numbers only, no message text), for sharing when something looks wrong")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args(argv)

    say = (lambda s: None) if a.quiet else (lambda s: print(s, file=sys.stderr))
    if not a.folder.is_dir():
        p.error(f"not a folder: {a.folder}")
    folders = find_capture_folders(a.folder)
    if not folders:
        p.error(f"no PNG screenshots under {a.folder}")
    say(f"chat-capture-to-html {__version__}")
    if a.no_ocr:
        engine = None
        say("OCR: off (--no-ocr)")
    else:
        try:
            engine = make_engine(a.ocr, a.tesseract, a.lang)
        except OcrError as e:
            print(f"error: {e}", file=sys.stderr)
            print("Use --no-ocr to make the report without reading any text.", file=sys.stderr)
            return 3
        say(f"OCR: {engine.describe()}")

    single = len(folders) == 1
    if a.output is None:
        out_dir, out_file = Path.cwd(), None
    elif single and a.output.suffix.lower() in (".html", ".htm"):
        out_dir, out_file = a.output.parent, a.output
    else:
        out_dir, out_file = a.output, None
    out_dir.mkdir(parents=True, exist_ok=True)

    worst = 0
    written = []
    for folder in folders:
        cap = load_capture(folder)
        bad = [s for s in cap.shots if s.status != HASH_OK]
        say(f"\n{folder}: {len(cap.shots)} screenshots, {len(cap.shots) - len(bad)} hash-verified")
        for s in bad:
            say(f"  ! {s.path.name}: {s.status} — {s.note}")
        if bad or cap.orphan_hash_files:
            worst = 2
        target = out_file or out_dir / f"{_safe(cap.app)}_{_safe(cap.chat)}.html"
        n = 2
        while target in written:  # two capture folders with the same app and chat name
            target = out_dir / f"{_safe(cap.app)}_{_safe(cap.chat)}_{n}.html"
            n += 1
        if folder.resolve() in target.resolve().parents:
            say("  ! writing the report inside the evidence folder")
        res = build_chat(cap, engine, a.top, a.bottom, include_unverified=not a.verified_only, progress=lambda s: say("  " + s),
                         left=a.left, right=a.right)
        target.write_text(render(res, target, a.me, a.them or cap.chat), encoding="utf-8")
        say(f"  {sum(1 for m in res.messages if m.kind == 'bubble')} messages -> {target}")
        for w in res.warnings:
            say(f"  ! {w}")
        if a.diagnose:
            _diagnose(res)
        elif len(res.segment_order) > 1:
            say("    run again with --diagnose to see why (prints numbers only, no message text)")
        written.append(target)
    return worst
