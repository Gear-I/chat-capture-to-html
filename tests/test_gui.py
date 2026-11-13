"""The window's logic, and the window itself where Tk and a display exist."""

import os
import queue
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html.gui import Settings, build_args, new_reports, run_tool, validate  # noqa: E402


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
