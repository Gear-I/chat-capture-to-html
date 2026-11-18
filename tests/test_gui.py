"""The window's logic, and the window itself where Tk and a display exist."""

import os
import queue
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html.gui import (Settings, build_args, capture_summary, new_reports, run_tool,  # noqa: E402
                                      validate)


def test_validate_refuses_reports_inside_the_evidence(tmp_path):
    cap = tmp_path / "screenshots"
    cap.mkdir()
    assert "capture folder" in validate(Settings("", str(tmp_path / "out")))
    assert "does not exist" in validate(Settings(str(tmp_path / "nope"), str(tmp_path / "out")))
    assert "outside the capture folder" in validate(Settings(str(cap), str(cap)))
    assert "outside the capture folder" in validate(Settings(str(cap), str(cap / "reports")))
    assert validate(Settings(str(cap), str(tmp_path / "out"))) is None


def test_build_args():
    s = Settings("/c", "/o", no_ocr=True, diagnose=True, verified_only=True, me="Owner", them="Anna")
    assert build_args(s) == ["/c", "-o", "/o", "--no-ocr", "--diagnose", "--verified-only", "--me", "Owner",
                             "--them", "Anna"]
    assert build_args(Settings("/c", "/o")) == ["/c", "-o", "/o"]
    assert build_args(Settings("/c", "/o", lang=" deu+eng ")) == ["/c", "-o", "/o", "--lang", "deu+eng"]
    assert build_args(Settings("/c", "/o", no_ocr=True, lang="deu")) == ["/c", "-o", "/o", "--no-ocr"]  # no OCR: no language


def test_validate_checks_the_ocr_languages(tmp_path):
    cap = tmp_path / "screenshots"
    cap.mkdir()
    out = str(tmp_path / "out")
    assert validate(Settings(str(cap), out, lang="deu+eng")) is None
    assert validate(Settings(str(cap), out, lang="chi_sim")) is None
    assert "language codes" in validate(Settings(str(cap), out, lang="deu, eng"))
    assert "language codes" in validate(Settings(str(cap), out, lang="deu+"))
    assert validate(Settings(str(cap), out, no_ocr=True, lang="deu, eng")) is None  # not used without OCR


def test_run_tool_streams_lines_and_exit_code(tmp_path):
    make_fixture.make(tmp_path / "in", "imessage")
    q = queue.Queue()
    start = time.time()
    run_tool(build_args(Settings(str(tmp_path / "in"), str(tmp_path / "out"), no_ocr=True)), q)
    items = []
    while not q.empty():
        items.append(q.get())
    assert items[-1] == ("done", 2)  # one wrong hash in the fixture
    lines = [v for k, v in items if k == "line"]
    assert any("chat-capture-to-html" in l for l in lines) and any("messages ->" in l for l in lines)
    assert [p.name for p in new_reports(tmp_path / "out", start)] == ["Messages_Test Kontakt.html"]


def test_window_runs_and_reports(tmp_path):
    tk = pytest.importorskip("tkinter")
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    from chat_capture_to_html.gui import build_window
    make_fixture.make(tmp_path / "in", "imessage")
    root = tk.Tk()
    try:
        w = build_window(root)
        w["capture"].set(str(tmp_path / "in"))
        w["output"].set(str(tmp_path / "out"))
        w["no_ocr"].set(True)
        w["run"]()
        deadline = time.time() + 120
        while str(w["run_button"].cget("state")) == "disabled" and time.time() < deadline:
            root.update()
            time.sleep(0.05)
        assert "hash problems" in w["status"].get()
        assert "1 report(s) written" in w["status"].get()
        assert str(w["open_button"].cget("state")) == "normal"
        assert "messages ->" in w["log"].get("1.0", "end")
    finally:
        root.destroy()


def test_capture_summary_for_the_side_panel(tmp_path):
    folder = make_fixture.make(tmp_path / "in", "imessage", sidecar="both", captions=True)
    before = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir())
    text = capture_summary(tmp_path / "in")
    n = len(list(folder.glob("*.png")))
    assert f"{n} screenshots" in text and f"{n - 1} hash-verified" in text and "! 1 with hash problems" in text
    assert make_fixture.DEVICE["serial"] in text and "iPadOS 26.1" in text
    assert f"{n - 1} of {n} screenshots" in text                     # UFADE captions
    assert sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()) == before  # only read
    (tmp_path / "empty").mkdir()
    assert "No screenshots" in capture_summary(tmp_path / "empty")
    assert capture_summary(tmp_path / "missing") == "No capture chosen."


def test_ufade_style_window_runs_and_reports(tmp_path):
    pytest.importorskip("tkinter")
    pytest.importorskip("customtkinter")
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    from chat_capture_to_html import gui_ctk
    make_fixture.make(tmp_path / "in", "imessage")
    app = gui_ctk.App()
    try:
        app.s.capture, app.s.output = str(tmp_path / "in"), str(tmp_path / "out")
        app.show_folders()
        app._summarise()
        app.show_options()
        app.s.no_ocr = True
        app.show_run()
        deadline = time.time() + 120
        while not app.reports and time.time() < deadline:
            app.update()
            time.sleep(0.05)
        assert [p.name for p in app.reports] == ["Messages_Test Kontakt.html"]
        while "Capture" not in app.info.get("1.0", "end") and time.time() < deadline:
            app.update()
            time.sleep(0.05)
        assert "hash-verified" in app.info.get("1.0", "end")
    finally:
        app.destroy()


def test_logo_files_ship_with_the_package():
    import chat_capture_to_html
    here = Path(chat_capture_to_html.__file__).parent
    shipped = next(line for line in (here.parent / "pyproject.toml").read_text(encoding="utf-8").splitlines()
                   if line.startswith("chat_capture_to_html = ["))  # tomllib needs Python 3.11
    for name in ("logo.ico", "logo_64.png", "logo_256.png"):
        assert (here / name).is_file() and name in shipped
    assert (here / "logo.ico").read_bytes() == (here.parent / "assets" / "logo.ico").read_bytes()
