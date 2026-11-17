"""Show where UFADE writes "uco-core:statement" in a capture's .case.json files,
without printing any of the text.

    py scripts/statement_check.py FOLDER [--show]

FOLDER is a capture folder (or any folder above it). Files are only read.
For each .case.json it prints: whether a statement is there, which kind of
object holds it (@type) and under which key, its length in characters, and
into how many comma-separated parts it splits. --show also prints the text
itself, for your own eyes only: don't paste that output anywhere.
"""

import json
import sys
from pathlib import Path


def find(obj, path=""):
    """Yield (path, holding @type, value) for every uco-core:statement."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}/{k}"
            if k == "uco-core:statement":
                yield here, obj.get("@type", "?"), v
            yield from find(v, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from find(v, f"{path}[{i}]")


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
        for path, typ, value in hits:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            line = (f"{n:3}  {typ}  at {path}  type {type(value).__name__}, {len(text)} characters, "
                    f"{len(text.split(','))} comma part(s)")
            print(line + (f"\n       {text}" if show else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
