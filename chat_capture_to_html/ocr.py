"""Offline OCR backends. Nothing here touches the network.

* ``tesseract``: the Tesseract command-line program (5.x recommended). On
  Windows the UB Mannheim build installs to ``C:\\Program Files\\Tesseract-OCR``;
  tick the German language data during setup for German chats.
* ``rapidocr``: ``pip install rapidocr-onnxruntime``; its models ship inside
  the wheel. They know Chinese and English characters only: accents, umlauts
  and other letters are lost, so it says so whenever it is used.

Each engine has ``warnings``: what the examiner must know about how it reads
(shown on the console and in the report), and ``setup()``: the program, the
settings and every model or language file it reads with, each with its
SHA-256, so the report says exactly what produced the text.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Line:
    text: str
    conf: float  # 0..100
    x0: int
    y0: int
    x1: int
    y1: int


class OcrError(RuntimeError):
    pass


def _file_line(label: str, path: Path | None) -> tuple[str, str]:
    """("label", "file name · size · SHA-256") for a model or language file."""
    if path is None or not path.is_file():
        return label, "file not found, so not hashed" + (f" ({path})" if path else "")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return label, f"{path} · {path.stat().st_size} bytes · SHA-256 {h.hexdigest()}"


class TesseractMissing(OcrError):
    """Tesseract is not installed (the only case where ``auto`` falls back)."""


RAPID_CHARSET = {"eng", "chi_sim"}  # what RapidOCR's bundled model can read
RAPID_NOTE = ("RapidOCR reads Chinese and English characters only: letters such as ä, ö, ü, ß, é or ñ are "
              "lost or misread. For other languages install Tesseract with their language data.")


def _find_tesseract(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            cand = os.path.join(base, "Tesseract-OCR", "tesseract.exe")
            if os.path.isfile(cand):
                return cand
    return None


class TesseractOcr:
    name = "tesseract"

    def __init__(self, exe: str | None = None, lang: str | None = None):
        self.exe = _find_tesseract(exe)
        if not self.exe:
            raise TesseractMissing("Tesseract not found (install it or pass --tesseract PATH)")
        try:
            out = subprocess.run([self.exe, "--list-langs"], capture_output=True, text=True, timeout=30)
            version = subprocess.run([self.exe, "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            raise OcrError(f"cannot run Tesseract: {e}") from e
        listing = (out.stdout + out.stderr).splitlines()
        langs = {l.strip() for l in listing[1:] if l.strip()}
        # 'List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (3):'
        where = re.search(r'"([^"]+)"', listing[0]) if listing else None
        self.tessdata = (Path(where.group(1)) if where else
                         Path(os.environ["TESSDATA_PREFIX"]) if os.environ.get("TESSDATA_PREFIX") else None)
        self.warnings: list[str] = []
        if lang is None:
            lang = "+".join(l for l in ("deu", "eng") if l in langs) or "eng"
            if langs and "deu" not in langs:
                self.warnings.append("Tesseract has no German language data (deu), so only English is read: "
                                     "umlauts and ß are lost. Install the German data, or choose languages "
                                     "with --lang.")
        missing = [l for l in lang.split("+") if langs and l not in langs]
        if missing:
            raise OcrError(f"Tesseract language data missing: {', '.join(missing)}")
        self.lang = lang
        first = (version.stdout or version.stderr).splitlines()
        self.version = first[0].strip() if first else "tesseract"

    def describe(self) -> str:
        return f"{self.version} (languages: {self.lang})"

    PSM = "11"  # sparse text: copes with mixed sizes such as a small time stamp in a bubble

    def setup(self) -> list[tuple[str, str]]:
        """Program, languages, settings, and each language file with its SHA-256
        (hashed once per run, from the bytes on disk)."""
        if getattr(self, "_setup", None) is not None:
            return self._setup
        rows = [("Program", f"{self.version} · {self.exe}"),
                ("Languages", f"{self.lang} (in this order; the first is preferred where they disagree)"),
                ("Settings", f"--psm {self.PSM} (sparse text), -c preserve_interword_spaces=1, tsv output")]
        for code in self.lang.split("+"):
            rows.append(_file_line(f"Language file {code}",
                                   self.tessdata / f"{code}.traineddata" if self.tessdata else None))
        self._setup = rows
        return rows

    def read(self, gray: np.ndarray, block: bool = True) -> list[Line]:
        ok, png = cv2.imencode(".png", gray)
        if not ok:
            raise OcrError("cannot encode image for OCR")
        psm = self.PSM
        try:
            proc = subprocess.run(
                [self.exe, "stdin", "stdout", "-l", self.lang, "--psm", psm, "-c", "preserve_interword_spaces=1", "tsv"],
                input=png.tobytes(), capture_output=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise OcrError(f"Tesseract failed: {e}") from e
        if proc.returncode != 0:
            raise OcrError(f"Tesseract failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
        rows = csv.DictReader(io.StringIO(proc.stdout.decode("utf-8", "replace")), delimiter="\t", quoting=csv.QUOTE_NONE)
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            if r.get("level") != "5" or not (r.get("text") or "").strip():
                continue
            groups.setdefault((r["page_num"], r["block_num"], r["par_num"], r["line_num"]), []).append(r)
        lines = []
        for words in groups.values():
            x0 = min(int(w["left"]) for w in words)
            y0 = min(int(w["top"]) for w in words)
            x1 = max(int(w["left"]) + int(w["width"]) for w in words)
            y1 = max(int(w["top"]) + int(w["height"]) for w in words)
            confs = [float(w["conf"]) for w in words if float(w["conf"]) >= 0]
            text = " ".join(w["text"].strip() for w in words)
            lines.append(Line(text, sum(confs) / len(confs) if confs else 0.0, x0, y0, x1, y1))
        lines.sort(key=lambda l: (l.y0, l.x0))
        return lines


class RapidOcr:
    name = "rapidocr"

    def __init__(self):
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except ImportError as e:
            raise OcrError("rapidocr-onnxruntime is not installed") from e
        self.engine = RapidOCR()
        try:
            from importlib.metadata import version
            self.version = f"RapidOCR {version('rapidocr-onnxruntime')}"
        except Exception:
            self.version = "RapidOCR"
        self.warnings = [RAPID_NOTE]
        import rapidocr_onnxruntime as pkg  # type: ignore
        self.folder = Path(pkg.__file__).parent if getattr(pkg, "__file__", None) else None

    def setup(self) -> list[tuple[str, str]]:
        """Program, settings file and each bundled model with its SHA-256
        (hashed once per run, from the bytes on disk)."""
        if getattr(self, "_setup", None) is not None:
            return self._setup
        rows = [("Program", f"{self.version} · {self.folder or 'package folder unknown'}"),
                ("Languages", "Chinese and English characters (bundled model)")]
        if self.folder is None:
            rows.append(("Model files", "package folder unknown, so not hashed"))
        else:
            rows.append(_file_line("Settings file", self.folder / "config.yaml"))
            models = sorted((self.folder / "models").glob("*.onnx"))
            rows += [_file_line(f"Model {m.stem}", m) for m in models] or [("Model files", "none found, so not hashed")]
        self._setup = rows
        return rows

    def describe(self) -> str:
        return f"{self.version} (bundled PP-OCR models)"

    def read(self, gray: np.ndarray, block: bool = True) -> list[Line]:
        result, _ = self.engine(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        lines = []
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(Line(text.strip(), float(score) * 100, int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))))
        lines.sort(key=lambda l: (l.y0, l.x0))
        return lines


def make_engine(name: str = "auto", tesseract: str | None = None, lang: str | None = None):
    """The OCR engine to use. ``auto`` takes Tesseract, and RapidOCR only when
    Tesseract is not installed; any other Tesseract problem (missing language
    data, a program that will not run) is an error, never a silent switch."""
    if name not in ("auto", "tesseract", "rapidocr"):
        raise OcrError(f"unknown OCR engine {name!r}")
    fallback = None
    if name in ("auto", "tesseract"):
        try:
            return TesseractOcr(tesseract, lang)
        except TesseractMissing as e:
            if name == "tesseract":
                raise
            fallback = str(e)
    if lang and not set(lang.split("+")) <= RAPID_CHARSET:
        raise OcrError((f"{fallback}; " if fallback else "")
                       + f"RapidOCR cannot read the languages asked for ({lang}): its model knows Chinese and "
                       "English characters only. Install Tesseract with that language data, or leave out --lang.")
    engine = RapidOcr()
    if fallback:
        engine.warnings.insert(0, f"{fallback}, so the fallback engine RapidOCR is used.")
    return engine
