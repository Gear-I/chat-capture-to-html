import hashlib

from chat_capture_to_html.capture import HASH_MISMATCH, HASH_MISSING, HASH_OK, HASH_UNREADABLE, load_capture, parse_stamp


def _pair(folder, stem, data=b"png", digest=None, txt=True):
    (folder / f"{stem}.png").write_bytes(data)
    if txt:
        (folder / f"{stem}.txt").write_text(digest if digest is not None else hashlib.sha256(data).hexdigest())


def test_hash_statuses_and_order(tmp_path):
    chat = tmp_path / "WhatsApp" / "Anna"
    chat.mkdir(parents=True)
    _pair(chat, "Anna_11_12_2026_14_30_06", b"c")
    _pair(chat, "Anna_11_12_2026_14_30_05_1", b"b", digest="0" * 64)
    _pair(chat, "Anna_11_12_2026_14_30_05", b"a")
    _pair(chat, "Anna_11_12_2026_14_30_07", b"d", txt=False)
    _pair(chat, "Anna_11_12_2026_14_30_08", b"e", digest="not a hash")
    (chat / "Anna_11_12_2026_14_30_09.txt").write_text("f" * 64)

    cap = load_capture(chat)
    assert (cap.app, cap.chat) == ("WhatsApp", "Anna")
    assert [s.path.stem for s in cap.shots] == [
        "Anna_11_12_2026_14_30_05", "Anna_11_12_2026_14_30_05_1", "Anna_11_12_2026_14_30_06",
        "Anna_11_12_2026_14_30_07", "Anna_11_12_2026_14_30_08"]
    assert [s.status for s in cap.shots] == [HASH_OK, HASH_MISMATCH, HASH_OK, HASH_MISSING, HASH_UNREADABLE]
    assert [p.name for p in cap.orphan_hash_files] == ["Anna_11_12_2026_14_30_09.txt"]


def test_uppercase_hash_with_newline_verifies(tmp_path):
    _pair(tmp_path, "x_01_02_2026_03_04_05", b"abc", digest=hashlib.sha256(b"abc").hexdigest().upper() + "\r\n")
    assert load_capture(tmp_path).shots[0].status == HASH_OK


def test_parse_stamp_with_underscores_in_chat_name():
    taken, seq = parse_stamp("Team_Chat_2_11_12_2026_14_30_05_3")
    assert (taken.year, taken.month, taken.day, taken.second, seq) == (2026, 11, 12, 5, 3)
