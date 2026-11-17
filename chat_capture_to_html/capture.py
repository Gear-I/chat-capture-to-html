"""Read a UFADE Chat capture folder and verify every screenshot against its hash file.

UFADE writes each chat to ``screenshots/<app>/<chat>/`` as pairs of files::

    <chat>_MM_DD_YYYY_HH_MM_SS[_n].png   raw PNG bytes from the device
    <chat>_MM_DD_YYYY_HH_MM_SS[_n].txt   SHA-256 of those bytes, lowercase hex

The ``_n`` suffix is added when several shots fall in the same second.

UFADE (and ALEX, its Android counterpart) also write a ``<name>.case.json``
beside each PNG: a CASE/UCO record (JSON-LD, ``@graph``) of the acquisition. It
holds the file's name, size and SHA-256, the start of the capture and the time
the record was written (UTC), the tool and version, the chat capture settings
(app name, chat name, direction) and the device (model, serial number, an ID
the tool derives from the UDID or serial, operating system). ALEX writes only
the ``.case.json``. When both exist, the two recorded hashes must agree.
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
    id: str | None = None             # the device's @id in the CASE record, derived by the tool from UDID or serial
    serial: str | None = None
    model: str | None = None
    device_type: str | None = None
    manufacturer: str | None = None
    os_name: str | None = None
    os_version: str | None = None

    def label(self) -> str:
        name = " ".join(x for x in (self.manufacturer, self.model) if x) or "unknown model"
        os = " ".join(x for x in (self.os_name, self.os_version) if x)
        return ", ".join(x for x in (name, os, f"serial {self.serial}" if self.serial else "") if x)


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
    case_path: Path | None = None     # the <name>.case.json, when present
    device: Device | None = None
    captured_utc: datetime | None = None
    capture: dict = field(default_factory=dict)  # tool, version, method, app, chat, direction from the .case.json
    caption: str | None = None        # UFADE's accessibility caption of the element focused for this shot
    caption_note: str = ""            # why a caption in the .case.json could not be used

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
        """Devices named by the shots' .case.json files, by device ID."""
        out: dict[str, Device] = {}
        for s in self.shots:
            if s.device and s.device.id:
                out.setdefault(s.device.id, s.device)
        return out

    def device_warnings(self) -> list[str]:
        named = [s for s in self.shots if s.device and s.device.id]
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


def _types(obj: dict) -> list[str]:
    ty = obj.get("@type", [])
    return ty if isinstance(ty, list) else [ty]


def _facets(obj: dict) -> list[dict]:
    f = obj.get("uco-core:hasFacet", [])
    return [x for x in (f if isinstance(f, list) else [f]) if isinstance(x, dict)]


def _value(v) -> str | None:
    if isinstance(v, dict):
        v = v.get("@value")
    return None if v in (None, "") else str(v)


CaseInfo = tuple[str | None, str, "Device | None", "datetime | None", dict, tuple[str | None, str]]


def _read_case_json(path: Path, png_name: str) -> CaseInfo:
    """(recorded SHA-256, problem, device, capture time, capture settings,
    (accessibility caption, caption problem)) from a UFADE or ALEX
    ``<name>.case.json``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        return None, f"cannot read the .case.json file: {e}", None, None, {}, (None, "")
    except json.JSONDecodeError as e:
        return None, f"the .case.json file is not valid JSON (line {e.lineno})", None, None, {}, (None, "")
    graph = data.get("@graph") if isinstance(data, dict) else None
    if not isinstance(graph, list):
        return None, "the .case.json file holds no @graph", None, None, {}, (None, "")
    objs = [o for o in graph if isinstance(o, dict)]
    by_id = {o.get("@id"): o for o in objs if o.get("@id")}

    capture: dict = {}
    when = None
    for o in objs:
        if "case-investigation:InvestigativeAction" in _types(o):
            when = _parse_utc(_value(o.get("uco-action:endTime")))
            capture["started"] = _value(o.get("uco-action:startTime"))
        if "uco-tool:ConfiguredTool" in _types(o):
            capture["tool"] = _value(o.get("uco-core:name"))
            capture["version"] = _value(o.get("uco-tool:version"))
            conf = o.get("uco-configuration:usesConfiguration") or {}
            for entry in conf.get("uco-configuration:configurationEntry", []) if isinstance(conf, dict) else []:
                if isinstance(entry, dict) and entry.get("uco-configuration:itemName"):
                    capture[entry["uco-configuration:itemName"]] = _value(entry.get("uco-configuration:itemValue"))

    device = None
    for o in objs:
        if "uco-observable:Device" in _types(o):
            device = Device(id=str(o.get("@id", "")).removeprefix("kb:device-") or None)
            for f in _facets(o):
                if "uco-observable:DeviceFacet" in _types(f):
                    device.serial = _value(f.get("uco-observable:serialNumber"))
                    device.model = _value(f.get("uco-observable:model"))
                    device.device_type = _value(f.get("uco-observable:deviceType"))
                    maker = f.get("uco-observable:manufacturer")
                    if isinstance(maker, dict):
                        device.manufacturer = _value(by_id.get(maker.get("@id"), {}).get("uco-core:name"))
            break
    if device is not None:
        for o in objs:
            if "uco-observable:OperatingSystem" in _types(o):
                device.os_name = _value(o.get("uco-core:name"))
                for f in _facets(o):
                    device.os_version = device.os_version or _value(f.get("uco-observable:version"))

    files = []  # (file name, SHA-256 or None)
    file_ids = {}  # @id of each file record -> its file name
    for o in objs:
        if "uco-observable:File" not in _types(o):
            continue
        name = digest = None
        for f in _facets(o):
            if "uco-observable:FileFacet" in _types(f):
                name = _value(f.get("uco-observable:fileName"))
            if "uco-observable:ContentDataFacet" in _types(f):
                for h in f.get("uco-observable:hash", []):
                    if isinstance(h, dict) and str(h.get("uco-types:hashMethod", "")).upper() == "SHA256":
                        digest = _value(h.get("uco-types:hashValue"))
        files.append((name, digest))
        if o.get("@id"):
            file_ids[o["@id"]] = name
    caption = _read_caption(objs, file_ids, png_name)
    mine = [d for n, d in files if n == png_name]
    if not mine:
        others = [n for n, _ in files if n]
        if others:
            return None, f"the .case.json file describes {others[0]!r}, not this screenshot", device, when, capture, caption
        return None, "the .case.json file holds no file record", device, when, capture, caption
    digest = mine[0]
    if not digest or not HEX64_RE.fullmatch(digest.strip()):
        return None, "the .case.json file holds no SHA-256 value", device, when, capture, caption
    return digest.strip().lower(), "", device, when, capture, caption


def _read_caption(objs: list[dict], file_ids: dict, png_name: str) -> tuple[str | None, str]:
    """(caption, problem): the accessibility caption UFADE recorded for the
    element it focused before this screenshot, a ``uco-core:Annotation`` whose
    ``uco-core:statement`` is the caption and whose ``uco-core:object`` is this
    screenshot's file record. Kept exactly as written (it is in the device's
    language, and the message text in it can hold commas, so it is not split)."""
    found, problems = [], []
    for o in objs:
        if "uco-core:Annotation" not in _types(o) or "uco-core:statement" not in o:
            continue
        text = _value(o.get("uco-core:statement"))
        refs = o.get("uco-core:object", [])
        refs = [r.get("@id") for r in (refs if isinstance(refs, list) else [refs]) if isinstance(r, dict)]
        names = [file_ids.get(r) for r in refs]
        if png_name not in names:
            problems.append("an accessibility caption in the .case.json refers to "
                            + (repr(next(n for n in names if n)) if any(names) else "no file record of this screenshot"))
        elif text is not None:
            found.append(text)
    if len(found) > 1:
        problems.append(f"the .case.json holds {len(found)} accessibility captions for this screenshot; all are shown")
    return (" | ".join(found) if found else None), "; ".join(problems)


def load_capture(folder: Path) -> Capture:
    folder = Path(folder)
    pngs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".png")
    txts = {p.stem: p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt"}
    cases = {p.name[:-len(".case.json")]: p for p in folder.iterdir()
             if p.is_file() and p.name.lower().endswith(".case.json")}
    cap = Capture(folder=folder, app=folder.parent.name, chat=folder.name)

    for png in pngs:
        taken, seq = parse_stamp(png.stem)
        digest = sha256_file(png)
        txt = txts.pop(png.stem, None)
        case = cases.pop(png.stem, None)
        device = when = None
        capture: dict = {}
        caption: tuple[str | None, str] = (None, "")
        notes = []
        records: list[tuple[str, str | None]] = []  # (kind, recorded hash or None if unreadable)
        if case is not None:
            rec, problem, device, when, capture, caption = _read_case_json(case, png.name)
            records.append(("case.json", rec))
            if problem:
                notes.append(problem)
        if txt is not None:
            rec, problem = _read_recorded_hash(txt)
            records.append(("txt", rec))
            if problem:
                notes.append(problem)

        if not records:
            recorded, status = None, HASH_MISSING
            notes.append("no .case.json or .txt hash file next to this screenshot")
        elif any(rec is None for _, rec in records):
            recorded = next((rec for _, rec in records if rec), None)
            status = HASH_UNREADABLE
        else:
            values = {rec for _, rec in records}
            recorded = records[0][1]
            if len(values) > 1:
                status = HASH_MISMATCH
                notes.append("the .case.json and .txt files record different SHA-256 values")
            elif recorded == digest:
                status = HASH_OK
            else:
                status = HASH_MISMATCH
                notes.append("SHA-256 of the PNG does not match the value in the "
                             + " and ".join(f".{k}" for k, _ in records) + " file")
        cap.shots.append(Shot(png, txt, digest, recorded, status, taken, seq, "; ".join(notes),
                              case_path=case, device=device, captured_utc=when, capture=capture,
                              caption=caption[0], caption_note=caption[1]))

    cap.orphan_hash_files = sorted(list(txts.values()) + list(cases.values()))
    # Capture order: the .case.json time where every shot has one (UTC), else the
    # timestamp in the file name; then the _n suffix, then name.
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
