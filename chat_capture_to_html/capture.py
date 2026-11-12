"""Read a UFADE Chat capture folder and verify every screenshot against its hash file.

UFADE writes each chat to ``screenshots/<app>/<chat>/`` as pairs of files::

    <chat>_MM_DD_YYYY_HH_MM_SS[_n].png   raw PNG bytes from the device
    <chat>_MM_DD_YYYY_HH_MM_SS[_n].txt   SHA-256 of those bytes, lowercase hex

The ``_n`` suffix is added when several shots fall in the same second.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

STAMP_RE = re.compile(
    r"^(?P<chat>.*?)_(?P<mo>\d{2})_(?P<d>\d{2})_(?P<y>\d{4})_(?P<h>\d{2})_(?P<mi>\d{2})_(?P<s>\d{2})(?:_(?P<n>\d+))?$"
)
HEX64_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")

HASH_OK = "ok"
HASH_MISMATCH = "mismatch"
HASH_MISSING = "missing"
HASH_UNREADABLE = "unreadable"


@dataclass
class Shot:
    path: Path
    txt_path: Path | None
    sha256: str
    recorded_sha256: str | None
    status: str
    taken: datetime | None
    seq: int
    note: str = ""

    @property
    def verified(self) -> bool:
        return self.status == HASH_OK


@dataclass
class Capture:
    folder: Path
    app: str
    chat: str
    shots: list[Shot] = field(default_factory=list)
    orphan_hash_files: list[Path] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_stamp(stem: str) -> tuple[datetime | None, int]:
    m = STAMP_RE.match(stem)
    if not m:
        return None, 0
    try:
        taken = datetime(int(m["y"]), int(m["mo"]), int(m["d"]), int(m["h"]), int(m["mi"]), int(m["s"]))
    except ValueError:
        return None, 0
    return taken, int(m["n"] or 0)


def _read_recorded_hash(txt: Path) -> tuple[str | None, str]:
    try:
        text = txt.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"cannot read hash file: {e}"
    m = HEX64_RE.search(text)
    if not m:
        return None, "hash file holds no SHA-256 value"
    return m.group(1).lower(), ""


def load_capture(folder: Path) -> Capture:
    folder = Path(folder)
    pngs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".png")
    txts = {p.stem: p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt"}
    cap = Capture(folder=folder, app=folder.parent.name, chat=folder.name)

    for png in pngs:
        taken, seq = parse_stamp(png.stem)
        digest = sha256_file(png)
        txt = txts.pop(png.stem, None)
        note = ""
        if txt is None:
            recorded, status, note = None, HASH_MISSING, "no .txt hash file next to this screenshot"
        else:
            recorded, note = _read_recorded_hash(txt)
            if recorded is None:
                status = HASH_UNREADABLE
            elif recorded == digest:
                status = HASH_OK
            else:
                status = HASH_MISMATCH
                note = "SHA-256 of the PNG does not match the value in the .txt file"
        cap.shots.append(Shot(png, txt, digest, recorded, status, taken, seq, note))

    cap.orphan_hash_files = sorted(txts.values())
    # Capture order: timestamp in the file name, then the _n suffix, then name.
    cap.shots.sort(key=lambda s: (s.taken or datetime.min, s.seq, s.path.name))
    return cap


def find_capture_folders(root: Path) -> list[Path]:
    """Every folder under ``root`` (including itself) that directly holds PNG files."""
    root = Path(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if any(f.lower().endswith(".png") for f in filenames):
            found.append(Path(dirpath))
    return found
