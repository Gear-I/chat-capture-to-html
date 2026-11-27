"""OCR per language (issue #48) and right-to-left text in the report (#47).

The language tests read invented chats in German, French, Spanish and
Russian with Tesseract and measure the character error rate. They show the
logic works on synthetic screenshots in a clean font (CLAUDE.md 5), not how
well real chats in these languages are read."""

import json
from collections import Counter
import re
import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

from chat_capture_to_html.capture import load_capture  # noqa: E402
from chat_capture_to_html.ocr import Line, OcrError, TesseractOcr, installed_languages  # noqa: E402
from chat_capture_to_html.pipeline import build_chat  # noqa: E402
from chat_capture_to_html.report import render  # noqa: E402

INSTALLED = installed_languages() or []
MAX_CER = 0.03  # measured 0.6-0.8 % on these chats with Tesseract 5.3.4; the rest is margin
EMOJI = re.compile("[\U0001F300-\U0001FAFF]")


def _distance(a: str, b: str) -> int:
    """Levenshtein distance: characters inserted, deleted or replaced."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _special(text: str) -> set[str]:
    """Letters and marks beyond plain ASCII that the language needs (no emoji)."""
    return {c for c in EMOJI.sub("", text) if ord(c) > 127 and unicodedata.category(c)[0] in "LP"}


@pytest.mark.parametrize("code", ["deu", "fra", "spa", "rus"])
def test_chat_in_each_language_is_read(tmp_path, code):
    if code not in INSTALLED:
        pytest.skip(f"Tesseract language data '{code}' not installed")
    folder = make_fixture.make(tmp_path, "imessage", corrupt=False, language=code)
    expected = json.loads((tmp_path / "expected_imessage.json").read_text(encoding="utf-8"))
    res = build_chat(load_capture(folder), TesseractOcr(lang=f"{code}+eng"))
    assert not res.warnings
    assert [m.side for m in res.messages] == [e["side"] for e in expected]   # every message, on its side
    want = [EMOJI.sub("", e["text"]) for e in expected]                       # emoji are not recognised (README)
    got = [" ".join(m.lines) for m in res.messages]
    errors = sum(_distance(EMOJI.sub("", g).replace("[]", ""), w) for g, w in zip(got, want))
    cer = errors / sum(len(w) for w in want)
    assert cer <= MAX_CER, f"{code}: character error rate {cer:.1%}"
    # every occurrence of a letter beyond ASCII (ä, é, ñ, ¿, ё, Cyrillic ...) counts, message by message
    needed = kept = 0
    for g, w in zip(got, want):
        need = Counter(c for c in w if c in _special(w))
        needed += sum(need.values())
        kept += sum((need & Counter(g)).values())
    assert kept >= 0.9 * needed, f"{code}: only {kept} of {needed} special letters kept"


class _FixedText:
    """A stand-in OCR engine that reads the same right-to-left text in every bubble."""
    name, warnings = "stand-in", []

    def __init__(self, text):
        self.text = text

    def describe(self):
        return "stand-in OCR"

    def setup(self):
        return []

    def read(self, gray, block=True):
        h, w = gray.shape[:2]
        return [Line(self.text, 95.0, 12, 12, max(13, w - 12), max(13, h - 12))]


@pytest.mark.parametrize("text", ["שלום, מה שלומך?", "مرحبا، كيف حالك؟"])
def test_right_to_left_text_keeps_its_side(tmp_path, text):
    folder = make_fixture.make(tmp_path, "imessage", corrupt=False)
    expected = json.loads((tmp_path / "expected_imessage.json").read_text(encoding="utf-8"))
    res = build_chat(load_capture(folder), _FixedText(text))
    assert [m.side for m in res.messages if m.kind == "bubble"] == \
        [e["side"] for e in expected if e["side"] != "center"]                  # sides come from the layout
    page = render(res, tmp_path / "r.html", "Me", "Them")
    bubbles = re.findall(r'<div class="msg (left|right)[^"]*" id="m\d+">.*?<div class="bubble"([^>]*)>([^<]*)', page)
    assert bubbles and all('dir="auto"' in attrs and text in body for _, attrs, body in bubbles)
    assert [side for side, _, _ in bubbles] == [m.side for m in res.messages if m.kind == "bubble"]
