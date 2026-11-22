"""Host protocol for the offline custom shutter-sound native payload."""
from __future__ import annotations

import hashlib
import json
import struct
from importlib.resources import files

from .device_protocol import DeviceError, response
from .custom_sound_slots import valid_slot
from .custom_sound_records import CustomSoundRecordError, decode_slot_record, MAX_RECORD_BYTES


CUSTOM_SOUND_REQUEST = struct.Struct("<4sI16s32sIIII")
CUSTOM_SOUND_NATIVE_SCHEMA = "a6700.custom-sound-native-payload/1"
SLOT_RESPONSE = struct.Struct('<4sI16sIIIII')
UNGUARDED = object()


def custom_sound_read_request(slot: int, nonce: bytes) -> bytes:
    if not valid_slot(slot) or not isinstance(nonce, bytes) or len(nonce) != 16:
        raise DeviceError('音频槽读取身份无效')
    return CUSTOM_SOUND_REQUEST.pack(b'CSNI', 1, nonce, bytes(32), slot, 0, 0, 0)


def custom_sound_read_response(data: bytes, nonce: bytes, slot: int):
    """Return observed bytes or verified ENOENT; errors are never empty slots."""
    custom_sound_read_request(slot, nonce)
    if not isinstance(data, bytes) or not 44 <= len(data) <= 44 + MAX_RECORD_BYTES:
        raise DeviceError('音频槽读取回包长度无效')
    magic, version, echoed, status, target, size, reserved, present = SLOT_RESPONSE.unpack(data[:44])
    if (magic != b'CSNR' or version != 1 or echoed != nonce or target != slot
            or reserved or present not in (0,1) or len(data) != 44 + size
            or (not present and size) or (status and (size or present))):
        raise DeviceError('音频槽读取回包身份或字段无效')
    if status:
        raise DeviceError(f'音频槽读取失败：{status}')
    return data[44:] if present else None


def load_custom_sound_reader() -> bytes:
    root = files('a6700_unlock').joinpath('data/native')
    manifest = json.loads(root.joinpath('custom-sound-reader.json').read_text(encoding='utf-8'))
    source = files('a6700_unlock').joinpath('native/custom_sound_read.c').read_bytes()
    payload = root.joinpath('custom-sound-reader.elf').read_bytes()
    row = manifest.get('payloads', {}).get('custom-sound-reader', {})
    if (manifest.get('schema') != 'a6700.custom-sound-slot-observer/1'
            or manifest.get('slot_count') != 10 or manifest.get('persistent_writes') is not False
            or manifest.get('not_installable') is not True
            or hashlib.sha256(source).hexdigest() != manifest.get('source_sha256')
            or hashlib.sha256(payload).hexdigest() != row.get('sha256') or len(payload) != row.get('bytes')):
        raise DeviceError('音频槽读取器与清单不匹配')
    return payload
CUSTOM_SOUND_ERRORS = {
    2: "invalid custom-sound request",
    3: "cannot open or create custom-sound storage",
    4: "custom-sound write, fsync, or read failure",
    5: "custom-sound atomic rename failure",
    6: "custom-sound independent readback mismatch",
    8: "custom-sound rollback incomplete",
    9: "custom-sound temporary or backup record needs reconciliation",
    10: "custom-sound slot preimage changed",
}


def custom_sound_request(plan: dict, record: bytes, nonce: bytes, *, expected=UNGUARDED,
                         remove=False) -> bytes:
    if not isinstance(plan, dict) or not isinstance(record, bytes):
        raise DeviceError("自定义快门声请求输入无效")
    slot = plan.get("target", {}).get("slot")
    generation = record[12:16]
    if not valid_slot(slot) or len(record) < 128 or len(nonce) != 16:
        raise DeviceError("自定义快门声请求身份无效")
    try:
        decode_slot_record(record, expected_slot=slot)
    except CustomSoundRecordError as exc:
        raise DeviceError("自定义快门声槽记录无效") from exc
    packed_generation = struct.unpack("<I", generation)[0]
    if ((packed_generation & 0xF0000000) != (slot << 28)
            or not (packed_generation & 0x0FFFFFFF)):
        raise DeviceError("自定义快门声generation与目标槽不匹配")
    if remove:
        if expected is not UNGUARDED:
            raise DeviceError('删除恢复请求不能携带替换内容')
        version, extent, preimage = 3, 0, b''
    elif expected is UNGUARDED:
        version, extent, preimage = 1, 0, b''
    elif expected is None:
        version, extent, preimage = 2, 0xFFFFFFFF, b''
    else:
        try:
            decode_slot_record(expected, expected_slot=slot)
        except (CustomSoundRecordError, TypeError, ValueError) as exc:
            raise DeviceError('音频槽前态无效') from exc
        version, extent, preimage = 2, len(expected), expected
    header = CUSTOM_SOUND_REQUEST.pack(
        b"CSND", version, nonce, bytes.fromhex(plan["digest"]), slot,
        packed_generation, len(record), extent)
    return header + record + preimage


def custom_sound_wipe_request(nonce: bytes) -> bytes:
    """Build a zero-payload request to recursively remove /setting/csnd."""
    if not isinstance(nonce, bytes) or len(nonce) != 16:
        raise DeviceError("自定义快门声清空请求身份无效")
    return CUSTOM_SOUND_REQUEST.pack(b"CSND", 4, nonce, bytes(32), 0, 0, 0, 0)


def custom_sound_clear_pending_request(nonce: bytes) -> bytes:
    """Build a zero-payload request to unlink only autoload lock markers under /setting/csnd."""
    if not isinstance(nonce, bytes) or len(nonce) != 16:
        raise DeviceError("自定义快门声无损清理请求身份无效")
    return CUSTOM_SOUND_REQUEST.pack(b"CSND", 5, nonce, bytes(32), 0, 0, 0, 0)


def custom_sound_response(data: bytes, nonce: bytes) -> dict:
    result = response(data, nonce, 0)
    if result["status"]:
        result["error"] = CUSTOM_SOUND_ERRORS.get(result["status"], "unknown custom-sound error")
    result["backup_cleanup_failed"] = bool(data[40] & 2)
    result["markers_cleared"] = bool(data[40] & 4)
    return result


def load_custom_sound_payload() -> bytes:
    root = files("a6700_unlock").joinpath("data/custom-sound")
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    source = files("a6700_unlock").joinpath("native/custom_sound_import.c").read_bytes()
    if (manifest.get("schema") != CUSTOM_SOUND_NATIVE_SCHEMA
            or manifest.get("hardware_validation") != "pending"
            or manifest.get("camera_io") is not False
            or manifest.get("not_installable") is not True):
        raise DeviceError("自定义快门声载荷清单无效")
    if hashlib.sha256(source).hexdigest() != manifest.get("source_sha256"):
        raise DeviceError("自定义快门声载荷源码与清单不一致")
    payload = root.joinpath("custom-sound-writer.elf").read_bytes()
    row = manifest.get("payloads", {}).get("custom-sound-writer", {})
    if (hashlib.sha256(payload).hexdigest() != row.get("sha256")
            or len(payload) != row.get("bytes")):
        raise DeviceError("自定义快门声载荷校验失败")
    return payload
