"""Evidence handling (CLAUDE.md 2.1-2.3): no report inside a capture folder,
every skipped screenshot in the warnings and the exit code, and only the
bytes that were hashed are analysed."""

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html import cli, pipeline  # noqa: E402
from chat_capture_to_html.capture import load_capture  # noqa: E402


def _files(folder):
    return sorted((p.name, p.read_bytes()) for p in folder.iterdir())


@pytest.mark.parametrize("where", ["report.html", "reports", "."])
def test_cli_refuses_to_write_into_a_capture_folder(tmp_path, capsys, where):
    folder = make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    before = _files(folder)
    with pytest.raises(SystemExit) as e:
        cli.main([str(tmp_path / "in"), "-o", str(folder / where), "--no-ocr", "-q"])
    assert e.value.code == 2 and "into the capture folder" in capsys.readouterr().err
    assert _files(folder) == before and not (folder / "reports").exists()


def test_cli_refuses_the_capture_folder_as_working_directory(tmp_path, monkeypatch):
    folder = make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    monkeypatch.chdir(folder)                      # no -o: the report would go to the current folder
    with pytest.raises(SystemExit):
        cli.main([str(folder), "--no-ocr", "-q"])
    assert not list(folder.glob("*.html"))


def test_reports_next_to_the_capture_are_fine(tmp_path):
    make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    assert cli.main([str(tmp_path / "in"), "-o", str(tmp_path), "--no-ocr", "-q"]) == 0  # the parent, not inside


def test_undecodable_screenshot_is_a_warning_and_sets_the_exit_code(tmp_path):
    folder = make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    bad = sorted(folder.glob("*.png"))[1]
    bad.write_bytes(b"not a png")
    (folder / f"{bad.stem}.txt").write_text(hashlib.sha256(b"not a png").hexdigest())  # its hash is right
    out = tmp_path / "report.html"
    assert cli.main([str(tmp_path / "in"), "-o", str(out), "--no-ocr", "-q"]) == 2
    warnings = out.read_text(encoding="utf-8").split("<h2>Warnings</h2>")[1].split("</div>")[0]
    assert f"{bad.name}: skipped, cannot be decoded as a PNG image" in warnings


def test_a_screenshot_changed_after_hashing_is_not_analysed(tmp_path, monkeypatch):
    folder = make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    cap = load_capture(folder)                     # hashes every screenshot
    victim = cap.shots[1].path
    real = Path.read_bytes

    def changed(self):                             # the file changes between hashing and decoding
        data = real(self)
        return data + b"tampered" if self == victim else data
    monkeypatch.setattr(Path, "read_bytes", changed)
    res = pipeline.build_chat(cap, None)
    info = res.shots[1]
    assert not info.used and "changed after it was hashed" in info.skip_reason
    assert any(f"{victim.name}: skipped, the file changed after it was hashed" in w for w in res.warnings)


def test_report_says_what_a_hash_match_shows(tmp_path):
    make_fixture.make(tmp_path / "in", "imessage", corrupt=False)
    out = tmp_path / "r.html"
    cli.main([str(tmp_path / "in"), "-o", str(out), "--no-ocr", "-q"])
    page = out.read_text(encoding="utf-8")
    assert "it does not by itself show that the screenshot is authentic" in page
    assert "not by who typed it" in page
    script = page.split("const SHOTS=")[1].split("\n")[0]
    assert "<" not in script                       # nothing in the shot list can close the script block
