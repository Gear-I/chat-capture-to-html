"""The live probe, against a stand-in for the device's accessibility service."""

import asyncio
import json

import pytest

from chat_capture_to_html.live.probe import NoFocus, format_summary, probe
from chat_capture_to_html.live.redact import shape_text, shape_value

# Invented conversation in the caption style VoiceOver uses.
CAPTIONS = [
    "Back, Button",
    "Anna Schmidt, Heading",
    "Today 18:02",
    "Anna Schmidt, Bist du morgen im Büro?, iMessage, 18:02",
    "You, Ja, ab 9 Uhr. Warum?, Read, 18:05",
    "Anna Schmidt, Photo, 18:06",
    "You, Meeting moved to room 4.12, see you there, Delivered, 18:09",
]
SECRET_WORDS = {"anna", "schmidt", "bist", "morgen", "büro", "warum", "meeting", "room", "uhr"}


class Element:
    def __init__(self, ident):
        self.identifier = ident


class Focus:
    def __init__(self, ident, caption):
        self.element = Element(ident)
        self.caption = caption
        self.spoken_description = caption.replace(",", ".")


class FakeAudit:
    """Answers like AccessibilityAudit: focus moves through CAPTIONS, then back to the first."""

    def __init__(self, captions, attrs=None, locked=False):
        self.captions = captions
        self.attrs = attrs or {}
        self.locked = locked
        self.pos = -1
        self.calls = []

    async def set_show_visuals(self, value):
        self.calls.append(("show_visuals", value))

    async def capabilities(self):
        return ["deviceApiVersion", "deviceInspectorMoveWithOptions:"]

    async def move_focus_and_wait(self, direction, timeout=5.0):
        self.calls.append(("move", direction))
        if self.locked:
            raise TimeoutError
        self.pos = (self.pos + 1) % len(self.captions)
        return Focus(bytes([self.pos]), self.captions[self.pos])

    async def element_attribute(self, ident, name):
        if name == "Bogus":
            raise RuntimeError("unknown attribute")
        return self.attrs.get(name, {}).get(ident[0])


def run(coro):
    return asyncio.run(coro)


def all_text(report):
    return json.dumps(report, ensure_ascii=False).lower() + format_summary(report).lower()


def test_no_message_text_survives():
    attrs = {"Label": {i: c for i, c in enumerate(CAPTIONS)},
             "Value": {3: "Bist du morgen im Büro?"},
             "Identifier": {3: "MessageBubble.Anna"}}
    report = run(probe(FakeAudit(CAPTIONS, attrs), "next", attributes=["Bogus"]))
    text = all_text(report)
    leaked = [w for w in SECRET_WORDS if w in text]
    assert not leaked, leaked
    assert report["summary"]["elements"] == len(CAPTIONS)
    assert report["stopped"].startswith("the focus came back")
    assert report["attribute_errors"] == {"Bogus": "RuntimeError"}


def test_shapes_keep_structure_and_interface_words():
    assert shape_text("Anna Schmidt, Bist du morgen im Büro?, iMessage, 18:02") == \
        "<text 2 words>, <text 5 words>, iMessage, <time>"
    assert shape_text("You, Ja, ab 9 Uhr. Warum?, Read, 6:05 PM") == "You, <text 1 word>, <text 4 words>, Read, <time>"
    assert shape_text("Today 18:02") == "<date>"
    assert shape_text("Monday, 3 November") == "<date>, <date>"
    assert shape_text("{{0, 120}, {390, 44}}") == "{{0, 120}, {390, 44}}"  # geometry is kept
    assert shape_text("Back, Button") == "Back, Button"


def test_values_keep_numbers_and_redact_strings():
    assert shape_value({"x": 0, "y": 120.5, "label": "Hello there"}) == {"x": 0, "y": 120.5, "label": "<text 2 words>"}
    assert shape_value([1, 2, (3, 4)]) == [1, 2, [3, 4]]
    assert shape_value(b"\x00\x01") == "<bytes 2>"
    assert shape_value("MessageBubble", keep_strings=True) == "MessageBubble"


def test_attributes_sampled_then_only_working_ones_are_read():
    frames = {i: [0, 100 * i, 390, 44] for i in range(len(CAPTIONS))}
    audit = FakeAudit(CAPTIONS, {"Frame": frames})
    report = run(probe(audit, "next", attribute_sample=2))
    assert report["summary"]["attributes_answered"] == {"Frame": len(CAPTIONS)}
    assert report["elements"][5]["attributes"] == {"Frame": [0, 500, 390, 44]}
    assert ("show_visuals", False) in audit.calls


def test_identifiers_only_kept_on_request():
    audit = FakeAudit(CAPTIONS, {"Identifier": {3: "MessageCell"}})
    hidden = run(probe(audit, "next"))
    shown = run(probe(FakeAudit(CAPTIONS, {"Identifier": {3: "MessageCell"}}), "next", show_identifiers=True))
    assert hidden["elements"][3]["attributes"]["Identifier"] == "<text 1 word>"
    assert shown["elements"][3]["attributes"]["Identifier"] == "MessageCell"


def test_limit_and_locked_device():
    report = run(probe(FakeAudit(CAPTIONS), "next", limit=3))
    assert report["summary"]["elements"] == 3 and "limit" in report["stopped"]
    with pytest.raises(NoFocus):
        run(probe(FakeAudit(CAPTIONS, locked=True), "next"))


def test_probe_through_real_pymobiledevice3_decoding():
    """The real AccessibilityAudit with only its transport replaced, as
    pymobiledevice3's own tests do: checks the probe against the library's
    actual message decoding and method signatures."""
    pmd = pytest.importorskip("pymobiledevice3.services.accessibilityaudit")

    def focus_event(element, caption):
        return ("hostInspectorCurrentElementChanged:", [[{
            "ObjectType": "AXAuditInspectorFocus_v1",
            "Value": {"ObjectType": "passthrough", "Value": {
                "CaptionTextValue_v1": {"ObjectType": "passthrough", "Value": caption},
                "ElementValue_v1": {"ObjectType": "AXAuditElement_v1", "Value": {
                    "ObjectType": "passthrough",
                    "Value": {"PlatformElementValue_v1": {"ObjectType": "passthrough", "Value": element}}}},
            }},
        }]])

    class Audit(pmd.AccessibilityAudit):
        def __init__(self):
            self._event_queue = asyncio.Queue()
            self.moves = 0

        async def _ensure_ready(self):
            pass

        async def _invoke(self, selector, *args, expects_reply=True):
            if selector == "deviceInspectorMoveWithOptions:":
                i = self.moves % len(CAPTIONS)
                self.moves += 1
                self._event_queue.put_nowait(("hostAppStateChanged:", [[]]))
                self._event_queue.put_nowait(focus_event(bytes([i]) * 20, CAPTIONS[i]))
            if selector == "deviceElement:valueForAttribute:":
                name = args[1]["Value"]["Value"]["AttributeNameValue_v1"]["Value"]
                if name == "Label":
                    return {"ObjectType": "passthrough", "Value": "Bist du morgen im Büro?"}
                return None
            if selector == "deviceCapabilities":
                return ["deviceApiVersion"]
            return None

    report = run(probe(Audit(), pmd.Direction.Next, timeout=1.0))
    assert report["summary"]["elements"] == len(CAPTIONS)
    assert report["summary"]["attributes_answered"] == {"Label": len(CAPTIONS)}
    assert report["elements"][3]["caption"] == "<text 2 words>, <text 5 words>, iMessage, <time>"
    assert not [w for w in SECRET_WORDS if w in all_text(report)]
