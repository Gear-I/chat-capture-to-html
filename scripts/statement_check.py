"""Show where UFADE writes "uco-core:statement" in a capture's .case.json files,
without printing any of the text.

    py scripts/statement_check.py FOLDER [--show]

FOLDER is a capture folder (or any folder above it). Files are only read.
For each .case.json it prints: whether a statement is there, which kind of
object holds it (@type) and under which key, its length in characters, into
how many comma-separated parts it splits, and the names of the holding
object's other fields (what they point to, never their text). --show also
prints the text itself, for your own eyes only: don't paste that output
anywhere.
"""

import json
import sys
from pathlib import Path


def find(obj, path=""):
    """Yield (path, holding @type, value, holding object) for every uco-core:statement."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}/{k}"
            if k == "uco-core:statement":
                yield here, obj.get("@type", "?"), v, obj
            yield from find(v, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from find(v, f"{path}[{i}]")


def describe(obj, data) -> str:
    """The holding object's other fields, by name and kind only: a reference
    to another object gives that object's @type, never a text value."""
    types = {o.get("@id"): o.get("@type", "?") for o in data.get("@graph", []) if isinstance(o, dict)}
    parts = []
    for k, v in obj.items():
        if k in ("uco-core:statement", "@type"):
            continue
        if k == "@id":
            parts.append("@id")
        elif isinstance(v, dict) and set(v) == {"@id"}:
            parts.append(f"{k} -> {types.get(v['@id'], 'not in this file')}")
        elif isinstance(v, list) and v and all(isinstance(x, dict) and set(x) == {"@id"} for x in v):
            parts.append(f"{k} -> [{', '.join(types.get(x['@id'], 'not in this file') for x in v)}]")
        elif isinstance(v, dict) and "@type" in v and "@value" in v:
            parts.append(f"{k} ({v['@type']})")
        else:
            parts.append(f"{k} ({type(v).__name__})")
    return "; ".join(parts)


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    show = "--show" in argv
    files = sorted(Path(argv[0]).rglob("*.case.json"))
    if not files:
        print("no .case.json files found")
        return 1
    for n, f in enumerate(files, 1):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            print(f"{n:3}  unreadable: {type(err).__name__}")
            continue
        hits = list(find(data))
        if not hits:
            print(f"{n:3}  no statement")
        for path, typ, value, holder in hits:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            line = (f"{n:3}  {typ}  at {path}  type {type(value).__name__}, {len(text)} characters, "
                    f"{len(text.split(','))} comma part(s)")
            print(line + f"\n       other fields: {describe(holder, data)}" + (f"\n       {text}" if show else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
