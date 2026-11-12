"""Offline OCR backends. Nothing here touches the network.

* ``tesseract``: the Tesseract command-line program (5.x recommended). On
  Windows the UB Mannheim build installs to ``C:\\Program Files\\Tesseract-OCR``;
  tick the German language data during setup for German chats.
* ``rapidocr``: ``pip install rapidocr-onnxruntime``; its models ship inside
  the wheel. Good on Latin text, but it drops accents and umlauts.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

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
            raise OcrError("Tesseract not found (install it or pass --tesseract PATH)")
        try:
            out = subprocess.run([self.exe, "--list-langs"], capture_output=True, text=True, timeout=30)
            version = subprocess.run([self.exe, "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            raise OcrError(f"cannot run Tesseract: {e}") from e
        langs = {l.strip() for l in (out.stdout + out.stderr).splitlines()[1:] if l.strip()}
        if lang is None:
            lang = "+".join(l for l in ("deu", "eng") if l in langs) or "eng"
        missing = [l for l in lang.split("+") if langs and l not in langs]
        if missing:
            raise OcrError(f"Tesseract language data missing: {', '.join(missing)}")
        self.lang = lang
        first = (version.stdout or version.stderr).splitlines()
        self.version = first[0].strip() if first else "tesseract"

    def describe(self) -> str:
        return f"{self.version} (languages: {self.lang})"

    def read(self, gray: np.ndarray, block: bool = True) -> list[Line]:
        ok, png = cv2.imencode(".png", gray)
        if not ok:
            raise OcrError("cannot encode image for OCR")
        psm = "11"  # sparse text: copes with mixed sizes such as a small time stamp in a bubble
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
    if name in ("auto", "tesseract"):
        try:
            return TesseractOcr(tesseract, lang)
        except OcrError:
            if name == "tesseract":
                raise
    if name in ("auto", "rapidocr"):
        return RapidOcr()
    raise OcrError(f"unknown OCR engine {name!r}")
