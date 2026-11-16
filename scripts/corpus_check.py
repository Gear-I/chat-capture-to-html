"""Run chat-capture-to-html over a corpus of captures and compare the results.

    py scripts/corpus_check.py CORPUS [--save FILE] [--baseline FILE] [--expected FILE] [--show-paths]

CORPUS is any folder holding capture folders (``screenshots/<app>/<chat>/``),
however deeply nested. Every capture is processed without OCR, with the
version of the tool that is installed, and summed up in numbers: shots, hash
problems, runs of joined shots, gaps, messages, pictures, the chat area found
and where each shot was placed.

Nothing here is written into a capture folder, and no message text, chat name
or file name is printed. Each capture is named by an ID made from the SHA-256
values of its screenshots, so the output can be shared. ``--show-paths``
also prints the folders, for your own use.

--save FILE       store the results (JSON) to compare against later
--baseline FILE   compare with results saved earlier, e.g. by another version
--expected FILE   check against what the examiner has confirmed by looking at
                  the screenshots, e.g. {"3f9a0c1d2e4b": {"runs": 1, "messages": 53}}

The script uses only the tool's basic functions, so it also runs with older
installed versions: save a baseline with one version, install another, and
compare. Exit code 1 when anything differs from the baseline or the expected
values, 0 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from chat_capture_to_html import __version__
from chat_capture_to_html.capture import HASH_OK, find_capture_folders, load_capture
from chat_capture_to_html.pipeline import build_chat

# Compared with the baseline; "placements" is compared but printed as a count.
FIELDS = ["shots", "verified", "hash_problems", "orphans", "skipped", "runs", "gaps", "messages", "doubtful",
          "cut_off", "lines", "pictures", "band", "pane"]


def capture_id(cap) -> str:
    """Stable ID from the screenshots' own SHA-256 values: the same capture gets
    the same ID wherever it is stored, and the ID reveals no names."""
    h = hashlib.sha256("".join(sorted(s.sha256 for s in cap.shots)).encode()).hexdigest()
    return h[:12]


def summarise(folder: Path, root: Path) -> dict:
    cap = load_capture(folder)
    t0 = time.perf_counter()
    res = build_chat(cap, None)  # no OCR: fast, needs no engine, same in every run
    shots = res.shots
    return {
        "id": capture_id(cap),
        "path": str(folder.relative_to(root)) if folder != root else ".",
        "shots": len(cap.shots),
        "verified": sum(1 for s in cap.shots if s.status == HASH_OK),
        "hash_problems": sum(1 for s in cap.shots if s.status != HASH_OK),
        "orphans": len(cap.orphan_hash_files),
        "skipped": sum(1 for i in shots if not i.used),
        "runs": len(res.segment_order),
        "gaps": max(0, len(res.segment_order) - 1),
        "messages": sum(1 for m in res.messages if m.kind == "bubble"),
        "doubtful": sum(1 for m in res.messages if getattr(m, "doubt", "")),  # marked from 0.5.6 on
        "cut_off": sum(1 for m in res.messages if m.kind == "bubble" and getattr(m, "cut", "")),  # from 0.6.1 on
        "lines": sum(1 for m in res.messages if m.kind != "bubble"),
        "pictures": sum(getattr(i, "pictures", 0) for i in shots),  # counted from 0.5.2 on
        "band": [res.content_top, res.content_bottom],
        "pane": [getattr(res, "chat_left", 0), getattr(res, "chat_right", 0)],
        "placements": [None if i.placement is None else [i.placement.segment, i.placement.y] for i in shots],
        "seconds": round(time.perf_counter() - t0, 1),
    }


def _inside_capture(path: Path, folders: list[Path]) -> bool:
    p = path.resolve()
    return any(p.parent == f or f in p.parents for f in (x.resolve() for x in folders))


def compare(old: dict, new: dict) -> list[str]:
    out = []
    for k in FIELDS:
        if k in old and old.get(k) != new.get(k):
            out.append(f"{k} {old.get(k)} -> {new.get(k)}")
    if "placements" in old and old["placements"] != new["placements"]:
        moved = sum(1 for a, b in zip(old["placements"], new["placements"]) if a != b)
        out.append(f"placements: {moved} of {len(new['placements'])} shots placed differently")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="corpus_check", description=__doc__.split("\n\n")[0])
    p.add_argument("corpus", type=Path)
    p.add_argument("--save", type=Path, help="write the results to this JSON file")
    p.add_argument("--baseline", type=Path, help="compare with results saved earlier")
    p.add_argument("--expected", type=Path, help="check against values the examiner confirmed")
    p.add_argument("--show-paths", action="store_true", help="also print each capture's folder (for your own use)")
    a = p.parse_args(argv)

    if not a.corpus.is_dir():
        p.error(f"not a folder: {a.corpus}")
    folders = find_capture_folders(a.corpus)
    if not folders:
        p.error(f"no PNG screenshots under {a.corpus}")
    if a.save and _inside_capture(a.save, folders):
        p.error("--save must not point into a capture folder; keep the evidence folders as UFADE wrote them")
    baseline = json.loads(a.baseline.read_text(encoding="utf-8")) if a.baseline else None
    expected = json.loads(a.expected.read_text(encoding="utf-8")) if a.expected else {}

    print(f"chat-capture-to-html {__version__}, {len(folders)} capture(s)"
          + (f", baseline from {baseline['version']}" if baseline else ""))
    print(f"{'id':12}  shots  hash!  runs  gaps  msgs  doubt  cut  lines  pics  band")
    results, failed = [], False
    for folder in folders:
        r = summarise(folder, a.corpus)
        results.append(r)
        print(f"{r['id']:12}  {r['shots']:5}  {r['hash_problems']:5}  {r['runs']:4}  {r['gaps']:4}  {r['messages']:4}"
              f"  {r['doubtful']:5}  {r['cut_off']:3}  {r['lines']:5}  {r['pictures']:4}  {r['band'][0]}-{r['band'][1]}"
              + (f"  {r['path']}" if a.show_paths else ""))

    if baseline is not None:
        before = {r["id"]: r for r in baseline["captures"]}
        now = {r["id"]: r for r in results}
        print(f"\ncompared with {baseline['version']}:")
        same = 0
        for r in results:
            if r["id"] not in before:
                print(f"  {r['id']}: new, not in the baseline")
                failed = True
                continue
            diff = compare(before[r["id"]], r)
            if diff:
                failed = True
                print(f"  {r['id']}: " + "; ".join(diff))
            else:
                same += 1
        for k in before:
            if k not in now:
                failed = True
                print(f"  {k}: in the baseline, missing now")
        print(f"  {same} of {len(results)} unchanged")

    if expected:
        print("\nchecked against expected values:")
        now = {r["id"]: r for r in results}
        for k, want in expected.items():
            if k not in now:
                failed = True
                print(f"  {k}: expected, but not in the corpus")
                continue
            bad = [f"{f} {now[k].get(f)} (expected {v})" for f, v in want.items()
                   if f in now[k] and now[k].get(f) != v]
            unknown = [f for f in want if f not in now[k]]
            if unknown:
                print(f"  {k}: unknown field(s) {', '.join(unknown)}")
            if bad:
                failed = True
            print(f"  {k}: " + ("; ".join(bad) if bad else "as expected"))

    if a.save:
        a.save.parent.mkdir(parents=True, exist_ok=True)
        a.save.write_text(json.dumps({"version": __version__, "captures": results}, indent=1), encoding="utf-8")
        print(f"\nresults saved to {a.save}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
