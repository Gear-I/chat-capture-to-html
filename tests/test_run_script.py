"""scripts/run.sh, the Linux/macOS runner."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="needs bash")


def run(*args):
    return subprocess.run(["bash", str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=600)


def test_runs_the_tool_and_passes_its_exit_code(tmp_path):
    make_fixture.make(tmp_path / "in", "imessage")
    out = tmp_path / "out"
    r = run("--no-venv", "--python", sys.executable, tmp_path / "in", "-o", out, "--no-ocr", "-q")
    assert r.returncode == 2, r.stderr  # the fixture has one wrong hash
    assert (out / "Messages_Test Kontakt.html").exists()


def test_refuses_an_installed_tool_of_another_version(tmp_path):
    fake = tmp_path / "fake" / "chat_capture_to_html"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text('__version__ = "0.0.1"\n')
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "fake"))
    r = subprocess.run(["bash", str(SCRIPT), "--no-venv", "--python", sys.executable, str(tmp_path), "--no-ocr"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 70
    assert "version '0.0.1'" in r.stderr


def test_usage_without_folder():
    r = run()
    assert r.returncode == 64 and "usage:" in r.stderr
