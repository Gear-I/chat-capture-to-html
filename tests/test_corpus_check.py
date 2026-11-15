"""scripts/corpus_check.py: regression runs over a corpus of captures."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "corpus_check.py"
spec = importlib.util.spec_from_file_location("corpus_check", SCRIPT)
corpus_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus_check)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("corpus")
    make_fixture.make(root / "phone", "imessage")
    make_fixture.make(root / "tablet", "ipad", chat="Other Chat")
    return root


def listing(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*"))


def test_save_then_compare_unchanged(corpus, tmp_path, capsys):
    before = listing(corpus)
    base = tmp_path / "baseline.json"
    assert corpus_check.main([str(corpus), "--save", str(base)]) == 0
    saved = json.loads(base.read_text(encoding="utf-8"))
    assert len(saved["captures"]) == 2 and all(c["runs"] == 1 for c in saved["captures"])
    assert corpus_check.main([str(corpus), "--baseline", str(base)]) == 0
    out = capsys.readouterr().out
    assert "2 of 2 unchanged" in out
    # numbers only: no chat names or file names unless asked for
    assert "Test Kontakt" not in out and "Other Chat" not in out and ".png" not in out
    assert listing(corpus) == before  # nothing written into the corpus


def test_changes_and_expected_values_are_reported(corpus, tmp_path, capsys):
    base = tmp_path / "baseline.json"
    corpus_check.main([str(corpus), "--save", str(base)])
    data = json.loads(base.read_text(encoding="utf-8"))
    first = data["captures"][0]
    first["runs"], first["messages"] = 2, first["messages"] + 1
    first["placements"][-1] = [9, 9]
    base.write_text(json.dumps(data), encoding="utf-8")
    exp = tmp_path / "expected.json"
    exp.write_text(json.dumps({first["id"]: {"runs": 1, "messages": 999}}), encoding="utf-8")
    capsys.readouterr()
    assert corpus_check.main([str(corpus), "--baseline", str(base), "--expected", str(exp)]) == 1
    out = capsys.readouterr().out
    assert f"{first['id']}: runs 2 -> 1" in out and "1 of" in out and "placed differently" in out
    assert "messages" in out and "(expected 999)" in out


def test_capture_id_does_not_depend_on_where_it_is_stored(tmp_path):
    a = make_fixture.make(tmp_path / "a", "imessage", chat="Name One")
    b = make_fixture.make(tmp_path / "b", "imessage", chat="Name One")
    from chat_capture_to_html.capture import load_capture
    assert corpus_check.capture_id(load_capture(a)) == corpus_check.capture_id(load_capture(b))


def test_refuses_to_save_into_a_capture_folder(corpus):
    folder = next(p.parent for p in corpus.rglob("*.png"))
    with pytest.raises(SystemExit):
        corpus_check.main([str(corpus), "--save", str(folder / "results.json")])
    assert not (folder / "results.json").exists()
