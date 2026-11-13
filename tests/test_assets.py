from pathlib import Path

from chat_capture_to_html.report import FAVICON

ROOT = Path(__file__).resolve().parent.parent


def test_packaged_logo_matches_assets():
    assert (ROOT / "chat_capture_to_html" / "logo.svg").read_bytes() == (ROOT / "assets" / "logo.svg").read_bytes()


def test_report_favicon_is_the_logo():
    assert FAVICON.startswith("data:image/svg+xml;base64,")


def test_readme_header():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'src="assets/logo.svg"' in text
    assert text.rstrip("\n").splitlines()[-1] == "Author: Dielle De Noon (@Gear-I), with Claude"
    assert "## License\n\nGNU General Public License v3.0, see [LICENSE](LICENSE)." in text


def test_version_is_the_same_everywhere():
    import re
    from chat_capture_to_html import __version__
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', toml, re.M).group(1) == __version__
