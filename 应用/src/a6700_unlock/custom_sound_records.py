"""Binary records for the camera-owned custom shutter-sound slots.

The format mirrors the freestanding native codec: a 128-byte header followed by
the filename and complete WAV. Records never contain host paths, pointers or
native handles.
"""
from __future__ import annotations

import hashlib
import struct
import zlib

from .custom_sound_slots import (
    MAX_SLOT_NAME_BYTES, encode_slot_name, valid_selection, valid_slot,
)


HEADER_BYTES = 128
MAX_NAME_BYTES = MAX_SLOT_NAME_BYTES
MAX_WAV_BYTES = 512 * 1024
MAX_RECORD_BYTES = HEADER_BYTES + MAX_NAME_BYTES + MAX_WAV_BYTES
MAGIC = b"CSND"
SCHEMA = 2
TYPE_SELECTION = 1
TYPE_SLOT = 2


class CustomSoundRecordError(ValueError):
    pass


def _filename_bytes(filename: str) -> bytes:
    try:
        return encode_slot_name(filename)
    except ValueError as exc:
        raise CustomSoundRecordError(str(exc)) from exc


def _record_crc(record: bytearray) -> int:
    return zlib.crc32(record[68:], zlib.crc32(record[:64])) & 0xFFFFFFFF


def _finish(record: bytearray) -> bytes:
    struct.pack_into("<I", record, 24, len(record))
    struct.pack_into("<I", record, 64, _record_crc(record))
    return bytes(record)


def build_selection_record(selection: int) -> bytes:
    if not valid_selection(selection):
        raise CustomSoundRecordError("快门声选择无效")
    record = bytearray(HEADER_BYTES)
    struct.pack_into("<4sIIIIII", record, 0, MAGIC, SCHEMA, selection,
                     0, 0, 0, HEADER_BYTES)
    struct.pack_into("<I", record, 28, TYPE_SELECTION)
    return _finish(record)


def build_slot_record(candidate: dict, wav: bytes, slot: int, generation: int) -> bytes:
    if not valid_slot(slot):
        raise CustomSoundRecordError("音频槽位无效")
    if (type(generation) is not int or generation <= 0
            or (generation & 0xF0000000) != (slot << 28)
            or not (generation & 0x0FFFFFFF)):
        raise CustomSoundRecordError("音频槽generation无效")
    if not isinstance(wav, bytes) or not wav:
        raise CustomSoundRecordError("音频WAV无效")
    if len(wav) > MAX_WAV_BYTES:
        raise CustomSoundRecordError("音频WAV超过512KiB")
    name = _filename_bytes(candidate.get("filename"))
    expected_sha256 = candidate.get("sha256")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise CustomSoundRecordError("音频哈希无效")
    actual_sha256 = hashlib.sha256(wav).hexdigest()
    if actual_sha256 != expected_sha256:
        raise CustomSoundRecordError("音频WAV与计划哈希不一致")
    record = bytearray(HEADER_BYTES + len(name) + len(wav))
    struct.pack_into("<4sIIIIIII", record, 0, MAGIC, SCHEMA, slot, generation,
                     len(wav), len(name), len(record), TYPE_SLOT)
    record[32:64] = bytes.fromhex(actual_sha256)
    record[HEADER_BYTES:HEADER_BYTES + len(name)] = name
    record[HEADER_BYTES + len(name):] = wav
    return _finish(record)


def decode_slot_record(record: bytes, *, expected_slot: int | None = None,
                       expected_generation: int | None = None) -> dict:
    if not isinstance(record, bytes) or len(record) < HEADER_BYTES:
        raise CustomSoundRecordError("音频槽记录长度无效")
    if len(record) > MAX_RECORD_BYTES:
        raise CustomSoundRecordError("音频槽记录超过大小限制")
    magic, schema, slot, generation, wav_bytes, name_bytes, total, kind = \
        struct.unpack_from("<4sIIIIIII", record, 0)
    if magic != MAGIC or schema != SCHEMA or kind != TYPE_SLOT:
        raise CustomSoundRecordError("音频槽记录格式无效")
    if not valid_slot(slot) or generation <= 0:
        raise CustomSoundRecordError("音频槽记录身份无效")
    if total != len(record) or HEADER_BYTES + name_bytes + wav_bytes != len(record):
        raise CustomSoundRecordError("音频槽记录长度不一致")
    if name_bytes > MAX_NAME_BYTES or wav_bytes > MAX_WAV_BYTES or not name_bytes or not wav_bytes:
        raise CustomSoundRecordError("音频槽记录字段无效")
    if any(record[index] for index in range(68, HEADER_BYTES)):
        raise CustomSoundRecordError("音频槽记录保留字段非零")
    crc = struct.unpack_from("<I", record, 64)[0]
    if crc != _record_crc(bytearray(record)):
        raise CustomSoundRecordError("音频槽记录CRC无效")
    name = record[HEADER_BYTES:HEADER_BYTES + name_bytes]
    wav = record[HEADER_BYTES + name_bytes:]
    try:
        decoded_name = name.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CustomSoundRecordError("音频文件名 UTF-8 无效") from exc
    _filename_bytes(decoded_name)
    if hashlib.sha256(wav).hexdigest() != record[32:64].hex():
        raise CustomSoundRecordError("音频槽记录SHA-256无效")
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise CustomSoundRecordError("音频槽记录不是RIFF WAV")
    if expected_slot is not None and slot != expected_slot:
        raise CustomSoundRecordError("音频槽记录槽位不匹配")
    if expected_generation is not None and generation != expected_generation:
        raise CustomSoundRecordError("音频槽记录generation不匹配")
    return dict(slot=slot, generation=generation, filename=decoded_name,
                wav=wav, wav_sha256=record[32:64].hex(), record_sha256=hashlib.sha256(record).hexdigest())


def decode_selection_record(record: bytes) -> int:
    if not isinstance(record, bytes) or len(record) != HEADER_BYTES:
        raise CustomSoundRecordError("快门声选择记录长度无效")
    magic, schema, selection, generation, wav_bytes, name_bytes, total, kind = \
        struct.unpack_from("<4sIIIIIII", record, 0)
    if (magic != MAGIC or schema != SCHEMA or kind != TYPE_SELECTION
            or not valid_selection(selection) or generation != 0
            or wav_bytes != 0 or name_bytes != 0 or total != HEADER_BYTES):
        raise CustomSoundRecordError("快门声选择记录格式无效")
    if any(record[index] for index in range(68, HEADER_BYTES)):
        raise CustomSoundRecordError("快门声选择记录保留字段非零")
    if struct.unpack_from("<I", record, 64)[0] != _record_crc(bytearray(record)):
        raise CustomSoundRecordError("快门声选择记录CRC无效")
    return selection
