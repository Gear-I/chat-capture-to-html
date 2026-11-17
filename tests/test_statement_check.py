"""scripts/statement_check.py: where a .case.json holds "uco-core:statement",
in numbers only unless --show is given."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import statement_check  # noqa: E402

SECRET = "Erfundener Text, Textnachricht"


def test_text_is_printed_only_with_show(tmp_path, capsys):
    graph = {"@graph": [{"@type": "uco-action:InvestigativeAction", "uco-core:statement": SECRET}]}
    (tmp_path / "a.case.json").write_text(json.dumps(graph), encoding="utf-8")
    (tmp_path / "b.case.json").write_text(json.dumps({"@graph": []}), encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    assert statement_check.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out and "Erfundener" not in out
    assert "uco-action:InvestigativeAction" in out and f"{len(SECRET)} characters, 2 comma part(s)" in out
    assert "no statement" in out
    assert statement_check.main([str(tmp_path), "--show"]) == 0
    assert SECRET in capsys.readouterr().out
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before  # only read
