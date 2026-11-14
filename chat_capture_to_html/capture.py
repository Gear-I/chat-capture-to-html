"""Read a UFADE Chat capture folder and verify every screenshot against its hash file.

UFADE writes each chat to ``screenshots/<app>/<chat>/`` as pairs of files::

    <chat>_MM_DD_YYYY_HH_MM_SS[_n].png   raw PNG bytes from the device
    <chat>_MM_DD_YYYY_HH_MM_SS[_n].txt   SHA-256 of those bytes, lowercase hex

The ``_n`` suffix is added when several shots fall in the same second.

A capture may instead (or as well) carry a ``<name>.json`` beside each PNG,
which also names the device. The draft format, agreed with UFADE's author
as a proposal and not yet final::

    {"schema": "ufade-shot/1", "file": "<name>.png", "sha256": "<64 hex>",
     "captured_utc": "2026-11-12T14:30:05Z",
     "device": {"udid": "...", "product_type": "iPad13,18", "os_version": "26.1", "name": "..."},
     "app": "Messages", "chat": "Anna", "tool": {"name": "UFADE", "version": "..."}}

Only ``sha256`` is required. The JSON is preferred; a ``.txt`` next to it is
checked too, and the two must agree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
class Device:
    udid: str | None = None
    product_type: str | None = None
    os_version: str | None = None
    name: str | None = None

    def label(self) -> str:
        bits = [b for b in (self.product_type, f"iOS {self.os_version}" if self.os_version else None) if b]
        return (self.name + " · " if self.name else "") + (", ".join(bits) or "unknown model")


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
    json_path: Path | None = None
    device: Device | None = None
    captured_utc: datetime | None = None

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

    @property
    def devices(self) -> dict[str, Device]:
        """Devices named by the shots' JSON files, by UDID."""
        out: dict[str, Device] = {}
        for s in self.shots:
            if s.device and s.device.udid:
                out.setdefault(s.device.udid, s.device)
        return out

    def device_warnings(self) -> list[str]:
        named = [s for s in self.shots if s.device and s.device.udid]
        if not named:
            return []
        warns = []
        if len(self.devices) > 1:
            warns.append(f"Screenshots in this chat folder name {len(self.devices)} different devices "
                         f"({', '.join(sorted(self.devices))}).")
        if len(named) < len(self.shots):
            warns.append(f"{len(self.shots) - len(named)} of {len(self.shots)} screenshots name no device; "
                         "they cannot be tied to the device of the others.")
        return warns


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


def _parse_utc(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _read_json_sidecar(path: Path, png_name: str) -> tuple[str | None, str, Device | None, datetime | None]:
    """(recorded SHA-256, problem, device, capture time) from a <name>.json."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        return None, f"cannot read the .json file: {e}", None, None
    except json.JSONDecodeError as e:
        return None, f"the .json file is not valid JSON (line {e.lineno})", None, None
    if not isinstance(data, dict):
        return None, "the .json file does not hold an object", None, None
    dev = data.get("device")
    device = None
    if isinstance(dev, dict):
        device = Device(*(str(dev[k]) if dev.get(k) not in (None, "") else None
                          for k in ("udid", "product_type", "os_version", "name")))
    when = _parse_utc(data.get("captured_utc"))
    named = data.get("file")
    if isinstance(named, str) and named and named != png_name:
        return None, f"the .json file describes {named!r}, not this screenshot", device, when
    digest = data.get("sha256")
    if not isinstance(digest, str) or not HEX64_RE.fullmatch(digest.strip()):
        return None, "the .json file holds no SHA-256 value", device, when
    return digest.strip().lower(), "", device, when


def load_capture(folder: Path) -> Capture:
    folder = Path(folder)
    pngs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".png")
    txts = {p.stem: p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt"}
    jsons = {p.stem: p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".json"}
    cap = Capture(folder=folder, app=folder.parent.name, chat=folder.name)

    for png in pngs:
        taken, seq = parse_stamp(png.stem)
        digest = sha256_file(png)
        txt = txts.pop(png.stem, None)
        js = jsons.pop(png.stem, None)
        device = when = None
        notes = []
        records: list[tuple[str, str | None]] = []  # (kind, recorded hash or None if unreadable)
        if js is not None:
            rec, problem, device, when = _read_json_sidecar(js, png.name)
            records.append(("json", rec))
            if problem:
                notes.append(problem)
        if txt is not None:
            rec, problem = _read_recorded_hash(txt)
            records.append(("txt", rec))
            if problem:
                notes.append(problem)

        if not records:
            recorded, status = None, HASH_MISSING
            notes.append("no .json or .txt hash file next to this screenshot")
        elif any(rec is None for _, rec in records):
            recorded = next((rec for _, rec in records if rec), None)
            status = HASH_UNREADABLE
        else:
            values = {rec for _, rec in records}
            recorded = records[0][1]
            if len(values) > 1:
                status = HASH_MISMATCH
                notes.append("the .json and .txt files record different SHA-256 values")
            elif recorded == digest:
                status = HASH_OK
            else:
                status = HASH_MISMATCH
                notes.append("SHA-256 of the PNG does not match the value in the "
                             + " and ".join(f".{k}" for k, _ in records) + " file")
        cap.shots.append(Shot(png, txt, digest, recorded, status, taken, seq, "; ".join(notes),
                              json_path=js, device=device, captured_utc=when))

    cap.orphan_hash_files = sorted(list(txts.values()) + list(jsons.values()))
    # Capture order: the JSON's capture time where every shot has one (it carries a
    # time zone), else the timestamp in the file name; then the _n suffix, then name.
    if cap.shots and all(s.captured_utc for s in cap.shots):
        cap.shots.sort(key=lambda s: (s.captured_utc, s.seq, s.path.name))
    else:
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
