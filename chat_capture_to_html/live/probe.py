"""Find out what an iPhone or iPad reports through its accessibility service.

The probe moves the accessibility focus through the app in front, as UFADE's
chat capture does, and records for each element the *shape* of its caption and
which attributes can be read (see redact.py). It reads, presses and types
nothing else. Moving the focus can scroll the app, as it does in UFADE.

The device side is reached through an ``audit`` object with the methods of
pymobiledevice3's ``AccessibilityAudit``, so the logic can be tested with a
stand-in and needs no device.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from .. import __version__
from .redact import shape_text, shape_value

# Attribute names to try. The daemon answers None for names it does not know;
# which ones work is exactly what the probe is for.
CANDIDATE_ATTRIBUTES = [
    "Label", "Value", "Identifier", "Hint", "Traits", "Frame", "FrameInScreen", "VisibleFrame",
    "Bounds", "Center", "ActivationPoint", "Path", "ElementType", "Role", "ClassName", "Type",
    "Language", "IsVisible", "Visible", "Enabled", "Selected", "ParentElement", "Parent", "Children",
    "ContainerType", "RowRange", "PlaceholderValue", "CustomActions", "URL", "ApplicationIdentifier",
    "BundleIdentifier", "Window", "ScreenFrame",
]
# Set by the app's developer, not by a person writing a message.
DEVELOPER_SET = {"Identifier", "ClassName", "ElementType", "Role", "Type", "ApplicationIdentifier", "BundleIdentifier"}


class NoFocus(RuntimeError):
    """No element took the focus: the device is locked, or no app is in front."""


async def probe(audit, next_direction, *, limit: int = 60, attribute_sample: int = 5,
                attributes: list[str] | None = None, show_identifiers: bool = False,
                timeout: float = 5.0, clock=time.monotonic) -> dict[str, Any]:
    """Walk up to ``limit`` elements and return a content-free report."""
    names = list(dict.fromkeys((attributes or []) + CANDIDATE_ATTRIBUTES))
    report: dict[str, Any] = {"tool": f"chat-capture-to-html {__version__}", "elements": []}
    try:
        await audit.set_show_visuals(False)  # no focus rectangle drawn on the screen
    except Exception as e:  # noqa: BLE001 - optional nicety; record and carry on
        report["show_visuals_error"] = type(e).__name__
    try:
        report["capabilities"] = list(await audit.capabilities())
    except Exception as e:  # noqa: BLE001
        report["capabilities_error"] = type(e).__name__

    seen: set[bytes] = set()
    working: list[str] = []  # attributes that answered on the sample
    errors: dict[str, str] = {}  # attribute name -> exception type, when reading it failed
    stopped = f"reached the limit of {limit} elements"
    for i in range(limit):
        t0 = clock()
        try:
            focus = await audit.move_focus_and_wait(next_direction, timeout=timeout)
        except TimeoutError:
            if i == 0:
                raise NoFocus("no element took the focus") from None
            stopped = "the focus stopped moving (end of what the app offers, or the app changed)"
            break
        ms = round((clock() - t0) * 1000)
        element = getattr(focus, "element", None)
        ident = getattr(element, "identifier", None)
        if ident is not None and ident in seen:
            stopped = "the focus came back to an element already visited"
            break
        if ident is not None:
            seen.add(ident)
        row: dict[str, Any] = {
            "n": i + 1,
            "ms": ms,
            "caption": shape_text(getattr(focus, "caption", None)),
            "spoken": shape_text(getattr(focus, "spoken_description", None)),
        }
        if ident is not None:
            to_read = names if i < attribute_sample else working
            values = {}
            for name in to_read:
                try:
                    v = await audit.element_attribute(ident, name)
                except Exception as e:  # noqa: BLE001 - an unknown name may raise instead of answering None
                    errors[name] = type(e).__name__
                    continue
                if v is not None:
                    values[name] = shape_value(v, keep_strings=show_identifiers and name in DEVELOPER_SET)
                    if i < attribute_sample and name not in working:
                        working.append(name)
            row["attributes"] = values
        report["elements"].append(row)

    rows = report["elements"]
    report["stopped"] = stopped
    report["attribute_errors"] = errors
    report["summary"] = {
        "elements": len(rows),
        "median_ms_per_move": sorted(r["ms"] for r in rows)[len(rows) // 2] if rows else None,
        "caption_shapes": Counter(r["caption"] for r in rows).most_common(25),
        "attributes_answered": {n: sum(1 for r in rows if n in r.get("attributes", {})) for n in working},
    }
    return report


def format_summary(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"{report['tool']}",
        f"device: {report.get('device', {})}",
        f"elements walked: {s['elements']} ({report['stopped']})",
        f"median time per focus move: {s['median_ms_per_move']} ms",
        "attributes that answered: " + (", ".join(f"{k} ({v})" for k, v in s["attributes_answered"].items()) or "none"),
        "attributes that raised errors: " + (", ".join(f"{k} ({v})" for k, v in report["attribute_errors"].items()) or "none"),
        "most common caption shapes:",
    ]
    lines += [f"  {c:>3}x  {shape}" for shape, c in s["caption_shapes"]]
    sample = next((r for r in report["elements"] if r.get("attributes")), None)
    if sample:
        lines.append(f"attributes of element {sample['n']} (shapes only):")
        lines += [f"  {k}: {v}" for k, v in sample["attributes"].items()]
    return "\n".join(lines)


async def run_on_device(udid: str | None, pair: bool = False, **kw) -> dict[str, Any]:
    """Connect over USB with pymobiledevice3 and run the probe. Without ``pair``
    it uses an existing pairing only, so the device is not asked to trust anew."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.accessibilityaudit import AccessibilityAudit, Direction

    lockdown = await create_using_usbmux(serial=udid, autopair=pair)
    try:
        device = {
            "product_type": lockdown.all_values.get("ProductType"),
            "os_version": lockdown.product_version,
            "build": lockdown.all_values.get("BuildVersion"),
        }
        async with AccessibilityAudit(lockdown) as audit:
            report = await probe(audit, Direction.Next, **kw)
        report["device"] = device
        return report
    finally:
        await lockdown.close()
