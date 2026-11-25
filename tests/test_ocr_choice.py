"""Which OCR engine runs, and that its limits are never silent (issue #44)."""

import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html import cli, ocr  # noqa: E402


@pytest.fixture
def no_tesseract(monkeypatch):
    monkeypatch.setattr(ocr, "_find_tesseract", lambda explicit: None)


@pytest.fixture
def fake_rapidocr(monkeypatch):
    """A stand-in for rapidocr-onnxruntime that reads nothing (the real one is an optional extra)."""
    mod = types.ModuleType("rapidocr_onnxruntime")

    class RapidOCR:
        def __call__(self, img):
            return None, None
    mod.RapidOCR = RapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", mod)


def test_fallback_says_what_it_cannot_read(no_tesseract, fake_rapidocr):
    engine = ocr.make_engine("auto")
    assert engine.name == "rapidocr"
    assert any("Tesseract not found" in w and "fallback" in w for w in engine.warnings)
    assert any("Chinese and English characters only" in w and "ä" in w for w in engine.warnings)
    assert ocr.make_engine("rapidocr").warnings == [ocr.RAPID_NOTE]   # chosen on purpose: still told
    assert ocr.make_engine("auto", lang="eng").name == "rapidocr"      # English it can read


@pytest.mark.parametrize("name", ["auto", "rapidocr"])
def test_languages_rapidocr_cannot_read_stop_the_run(no_tesseract, fake_rapidocr, name):
    with pytest.raises(ocr.OcrError, match=r"RapidOCR cannot read the languages asked for \(deu\+eng\)"):
        ocr.make_engine(name, lang="deu+eng")


def test_missing_language_data_is_an_error_not_a_switch(monkeypatch, fake_rapidocr):
    def run(cmd, **kw):
        out = "List of available languages (2):\neng\nosd\n" if "--list-langs" in cmd else "tesseract 5.3.4\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(ocr, "_find_tesseract", lambda explicit: "tesseract")
    monkeypatch.setattr(ocr.subprocess, "run", run)
    with pytest.raises(ocr.OcrError, match="language data missing: deu"):
        ocr.make_engine("auto", lang="deu")
    engine = ocr.make_engine("auto")                     # no German installed: English only, and said
    assert engine.name == "tesseract" and engine.lang == "eng"
    assert any("no German language data" in w for w in engine.warnings)


def test_console_and_report_carry_the_warning(tmp_path, capsys, no_tesseract, fake_rapidocr):
    make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    out = tmp_path / "report.html"
    assert cli.main([str(tmp_path / "in"), "-o", str(out), "-q"]) == 0
    assert "! OCR: RapidOCR reads Chinese and English characters only" in capsys.readouterr().err  # even with -q
    html = out.read_text(encoding="utf-8")
    assert "OCR: RapidOCR reads Chinese and English characters only" in html.split("<h2>Warnings</h2>")[1]


def test_cli_stops_on_languages_rapidocr_cannot_read(tmp_path, capsys, no_tesseract, fake_rapidocr):
    make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    assert cli.main([str(tmp_path / "in"), "-o", str(tmp_path / "out"), "--lang", "deu"]) == 3
    assert "RapidOCR cannot read the languages asked for (deu)" in capsys.readouterr().err
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.html"))
