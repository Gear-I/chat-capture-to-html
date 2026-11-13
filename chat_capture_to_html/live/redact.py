"""Turn what a device reports into its *shape*, never its content.

The probe's output is meant to be pasted into an issue or a chat, so nothing a
person wrote may survive: a caption such as ``"Anna, See you at 6, 18:02"``
becomes ``"<text 1 word>, <text 4 words>, <time>"``. Only fixed interface words
(``Button``, ``iMessage``, ``Read``), times, dates, numbers and screen geometry
are kept, because they describe how the app labels things, not what was said.
"""

from __future__ import annotations

import re
from typing import Any

# Words iOS and messaging apps put in accessibility captions themselves. A part
# of a caption is kept verbatim only when it is one of these, ignoring case.
SAFE_PARTS = {
    "button", "image", "heading", "header", "link", "text", "static text", "text field", "search field",
    "selected", "dimmed", "adjustable", "tab", "tab bar", "toolbar", "navigation bar", "collection",
    "cell", "list", "table", "scroll view", "window", "alert", "keyboard", "back", "close", "cancel",
    "done", "edit", "more", "details", "info", "menu", "send", "camera", "photos", "apps",
    "message", "messages", "imessage", "text message", "sms", "rcs", "sms/mms", "mms",
    "you", "me", "read", "delivered", "sent", "not delivered", "unread", "edited", "unsent",
    "photo", "video", "attachment", "audio", "audio message", "voice message", "sticker", "gif",
    "link preview", "location", "contact", "file", "document", "reply", "replies", "tapback",
    "reaction", "liked", "loved", "laughed at", "emphasized", "disliked", "questioned",
    "today", "yesterday", "now", "typing", "is typing", "pinned", "muted", "online", "last seen",
    "voice call", "video call", "missed call", "call", "facetime", "group", "new message",
    "compose", "search", "filter", "conversations", "chats", "contacts", "settings", "status",
    "double tap to open", "double tap to play", "double tap to view", "swipe up or down to select a custom action, then double tap to activate",
    "actions available", "heading level 1", "heading level 2", "unread message", "unread messages",
}
WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun"
MONTHS = ("january february march april may june july august september october november december "
          "jan feb mar apr jun jul aug sep sept oct nov dec")
DATE_WORDS = set(WEEKDAYS.split()) | set(MONTHS.split()) | {"at", "on", "of", "the", "today", "yesterday", "am", "pm"}
TIME_RE = re.compile(r"^\d{1,2}[:.]\d{2}(?::\d{2})?(?:\s?[ap]\.?\s?m\.?)?$", re.I)
NUMBER_RE = re.compile(r"^[\d\s.,:/+\-%]+$")
GEOMETRY_RE = re.compile(r"^[\s\d.,\-{}()\[\]]+$")
WORD_RE = re.compile(r"\w+", re.UNICODE)


def _date_like(part: str) -> bool:
    words = WORD_RE.findall(part.lower())
    if not words:
        return False
    has_name = any(w in DATE_WORDS for w in words if not w.isdigit())
    return has_name and all(w.isdigit() or w in DATE_WORDS or TIME_RE.match(w) for w in words)


def shape_part(part: str) -> str:
    p = part.strip()
    if not p:
        return ""
    if p.lower() in SAFE_PARTS:
        return p
    if TIME_RE.match(p):
        return "<time>"
    if _date_like(p) or re.match(r"^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}$", p):
        return "<date>"
    if NUMBER_RE.match(p):
        return "<number>"
    n = len(WORD_RE.findall(p))
    return f"<text {n} word{'s' if n != 1 else ''}>" if n else "<symbols>"


def shape_text(text: str | None) -> str | None:
    """The shape of a caption: its comma-separated parts, each kept or replaced
    by its kind. Geometry such as ``{{0, 120}, {390, 44}}`` is kept as is."""
    if text is None:
        return None
    if GEOMETRY_RE.match(text) and any(ch.isdigit() for ch in text):
        return text
    return ", ".join(s for s in (shape_part(p) for p in text.split(",")) if s)


def shape_value(value: Any, keep_strings: bool = False, depth: int = 0) -> Any:
    """Redact any attribute value: numbers and booleans stay, strings become
    their shape (or stay, when ``keep_strings`` is set for developer-set values
    such as identifiers), containers are walked, anything else becomes its type."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if keep_strings else shape_text(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes {len(value)}>"
    if depth > 4:
        return f"<{type(value).__name__}>"
    if isinstance(value, (list, tuple)):
        return [shape_value(v, keep_strings, depth + 1) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k): shape_value(v, keep_strings, depth + 1) for k, v in list(value.items())[:40]}
    return f"<{type(value).__name__}>"
