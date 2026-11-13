"""``chatwalk``: document an iPhone or iPad app live, over USB.

Only ``probe`` exists so far. It finds out what the device's accessibility
service reports, as shapes without content, so the walker can be built on what
devices really do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from .. import __version__
from .probe import NoFocus, format_summary, run_on_device

SAFETY = """\
Before you run this:
  - Use it only on a device you may lawfully examine. It does not unlock anything;
    unlock the device yourself and open the app and chat you want to look at.
  - It moves the accessibility focus through the app, which can scroll it, as UFADE's
    chat capture does. It presses and types nothing.
  - Put the device in airplane mode unless you mean otherwise.
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chatwalk", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe", help="report what the device's accessibility service gives, without any content")
    pr.add_argument("--udid", help="device to use when more than one is connected")
    pr.add_argument("--limit", type=int, default=60, help="elements to walk (default: %(default)s)")
    pr.add_argument("--attribute", action="append", default=[], metavar="NAME",
                    help="also try this attribute name (repeatable)")
    pr.add_argument("--show-identifiers", action="store_true",
                    help="keep developer-set values (Identifier, class names) verbatim; they are set by the app, "
                         "not by people, but check before sharing")
    pr.add_argument("--pair", action="store_true",
                    help="pair with the device if it is not paired yet (the device asks to trust this computer)")
    pr.add_argument("-o", "--output", type=Path, help="where to write the JSON report (default: probe_<time>.json)")
    a = p.parse_args(argv)

    print(f"chatwalk {__version__}", file=sys.stderr)
    print(SAFETY, file=sys.stderr)
    try:
        import pymobiledevice3  # noqa: F401
    except ImportError:
        print("error: pymobiledevice3 is not installed; run: py -m pip install .[live]", file=sys.stderr)
        return 3
    try:
        report = asyncio.run(_probe(a))
    except NoFocus:
        print("error: no element took the accessibility focus. The device may be locked, or no app is in front.\n"
              "Unlock it yourself, open the chat, and run the probe again.", file=sys.stderr)
        return 4
    except Exception as e:  # noqa: BLE001 - connection problems arrive as many types
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        print("Is the device connected by USB, unlocked, trusted by this computer, and is Developer Mode on? "
              "Try --pair if it was never paired.", file=sys.stderr)
        return 5
    out = a.output or Path(f"probe_{datetime.now():%Y%m%d_%H%M%S}.json")
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(format_summary(report))
    print(f"\nfull report (shapes only, no message text): {out}", file=sys.stderr)
    return 0


async def _probe(a):
    from pymobiledevice3.lockdown import create_using_usbmux  # noqa: F401 - fail early with a clear import error
    report = await run_on_device(a.udid, limit=a.limit, attributes=a.attribute, show_identifiers=a.show_identifiers,
                                 pair=a.pair)
    return report


if __name__ == "__main__":
    raise SystemExit(main())
