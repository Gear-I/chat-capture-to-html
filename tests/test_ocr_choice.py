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
    err = capsys.readouterr().err
    assert err.count("! OCR: RapidOCR reads Chinese and English characters only") == 1  # even with -q; once
    html = out.read_text(encoding="utf-8")
    assert "OCR: RapidOCR reads Chinese and English characters only" in html.split("<h2>Warnings</h2>")[1]


def test_cli_stops_on_languages_rapidocr_cannot_read(tmp_path, capsys, no_tesseract, fake_rapidocr):
    make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    assert cli.main([str(tmp_path / "in"), "-o", str(tmp_path / "out"), "--lang", "deu"]) == 3
    assert "RapidOCR cannot read the languages asked for (deu)" in capsys.readouterr().err
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.html"))


def test_rapidocr_setup_without_a_package_folder(no_tesseract, fake_rapidocr):
    rows = dict(ocr.make_engine("rapidocr").setup())
    assert rows["Model files"] == "package folder unknown, so not hashed"


def _fake_tessdata(tmp_path, monkeypatch, langs=("deu", "eng"), present=("deu", "eng")):
    data = tmp_path / "tessdata"
    data.mkdir()
    for code in present:
        (data / f"{code}.traineddata").write_bytes(f"model {code}".encode())

    def run(cmd, **kw):
        if "--list-langs" in cmd:
            out = f'List of available languages in "{data}/" ({len(langs)}):\n' + "\n".join(langs) + "\n"
        else:
            out = "tesseract 5.3.4\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(ocr, "_find_tesseract", lambda explicit: "tesseract")
    monkeypatch.setattr(ocr.subprocess, "run", run)
    return data


def test_tesseract_setup_hashes_each_language_file(tmp_path, monkeypatch):
    import hashlib
    _fake_tessdata(tmp_path, monkeypatch)
    rows = dict(ocr.make_engine("tesseract", lang="eng+deu").setup())
    assert rows["Program"].startswith("tesseract 5.3.4")
    assert rows["Languages"].startswith("eng+deu (in this order")
    assert "--psm 11" in rows["Settings"]
    for code in ("eng", "deu"):
        assert f"SHA-256 {hashlib.sha256(f'model {code}'.encode()).hexdigest()}" in rows[f"Language file {code}"]
    assert list(rows)[3:] == ["Language file eng", "Language file deu"]  # in the order they are used


def test_tesseract_setup_says_when_a_file_is_not_found(tmp_path, monkeypatch):
    _fake_tessdata(tmp_path, monkeypatch, present=("eng",))
    rows = dict(ocr.make_engine("tesseract", lang="deu+eng").setup())
    assert rows["Language file deu"].startswith("file not found, so not hashed")


def test_report_lists_the_ocr_setup(tmp_path, monkeypatch):
    _fake_tessdata(tmp_path, monkeypatch)
    engine = ocr.make_engine("tesseract")
    folder = make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    from chat_capture_to_html.capture import load_capture
    from chat_capture_to_html.pipeline import build_chat
    from chat_capture_to_html.report import render
    engine.read = lambda gray, block=True: []           # the stand-in Tesseract reads nothing
    res = build_chat(load_capture(folder), engine)
    page = render(res, tmp_path / "r.html", "Me", "Them")
    assert "<dt>OCR setup</dt>" in page and "Language file deu" in page and page.count("SHA-256 ") >= 2
    assert "<dt>OCR setup</dt>" not in render(build_chat(load_capture(folder), None), tmp_path / "n.html", "Me", "Them")


def test_installed_languages_lists_names_and_leaves_out_osd(tmp_path, monkeypatch):
    _fake_tessdata(tmp_path, monkeypatch, langs=("osd", "eng", "deu", "spa", "xyz"))
    langs = ocr.installed_languages()
    assert langs == ["eng", "deu", "spa", "xyz"]                 # by name: English, German, Spanish, then xyz
    assert [ocr.language_label(c) for c in langs] == ["English (eng)", "German (deu)", "Spanish (spa)", "xyz"]
    assert ocr.default_languages(langs) == "deu+eng" and ocr.default_languages(["spa"]) == "eng"


def test_installed_languages_without_tesseract(no_tesseract):
    assert ocr.installed_languages() is None
