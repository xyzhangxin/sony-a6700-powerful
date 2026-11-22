"""Shared custom shutter-sound slot limits."""

SLOT_COUNT = 10
MAX_SLOT_NAME_BYTES = 255
_ASCII_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
_DISALLOWED_UNICODE_RANGES = (
    (0x0080, 0x009F),  # C1 control characters
    (0x200B, 0x200F),  # zero-width / directional marks
    (0x202A, 0x202E),  # bidi embeddings & overrides
    (0x2066, 0x2069),  # bidi isolates
    (0xD800, 0xDFFF),  # UTF-16 surrogates
    (0xFEFF, 0xFEFF),  # BOM
    (0xFFFE, 0xFFFF),  # noncharacters
)


def valid_slot(value) -> bool:
    return type(value) is int and 1 <= value <= SLOT_COUNT


def valid_selection(value) -> bool:
    return type(value) is int and 0 <= value <= SLOT_COUNT


def encode_slot_name(filename: str) -> bytes:
    """Validate and UTF-8 encode one camera custom-sound slot name."""
    if not isinstance(filename, str) or not filename:
        raise ValueError("音频文件名无效")
    if filename[0] in (".", " ") or ".." in filename:
        raise ValueError("音频文件名不安全")
    for ch in filename:
        code = ord(ch)
        if code < 0x80:
            if ch not in _ASCII_ALLOWED:
                raise ValueError("音频文件名字符无效")
        elif any(lo <= code <= hi for lo, hi in _DISALLOWED_UNICODE_RANGES):
            raise ValueError("音频文件名字符无效")
    try:
        raw = filename.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("音频文件名必须为有效 UTF-8") from exc
    if not raw or len(raw) > MAX_SLOT_NAME_BYTES:
        raise ValueError("音频文件名长度无效")
    return raw


def slot_inventory(records=None):
    """Project observed slot records without confusing unread with empty.

    Keys absent from the snapshot are unknown. An explicit None means the
    adapter verified absence; bytes must be a validated, slot-bound record.
    No default slot is ever returned as an import destination.
    """
    from .custom_sound_records import decode_slot_record, CustomSoundRecordError

    if records is None:
        records = {}
    if not isinstance(records, dict) or any(not valid_slot(k) for k in records):
        raise ValueError("音频槽位快照无效")
    slots = []
    for slot in range(1, SLOT_COUNT + 1):
        name, status, generation = "[未读取]", "unknown", None
        if slot in records:
            if records[slot] is None:
                name, status = "[空]", "empty"
            else:
                try:
                    record = decode_slot_record(records[slot], expected_slot=slot)
                except (CustomSoundRecordError, TypeError, ValueError):
                    name, status = "[记录异常]", "invalid"
                else:
                    name, status = record["filename"], "imported"
                    generation = record["generation"]
        slots.append(dict(slot=slot, name=name, label=f"{slot} {name}",
                          status=status, generation=generation,
                          playable=status == "imported"))
    return slots
