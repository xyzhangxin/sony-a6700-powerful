"""Validated audio asset packages for the custom shutter-sound marketplace.

An audio package is deliberately data-only. It may describe PCM WAV files and
presentation metadata, but cannot carry host code, camera payloads, addresses or
workflow steps. Actual import remains a local, user-confirmed core operation.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import struct
import zipfile


AUDIO_PACKAGE_SCHEMA = "a6700.audio-package/v1"
AUDIO_INDEX_SCHEMA = "a6700.audio-index/v1"
CORE_API_VERSION = 1
MAX_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 68 * 1024 * 1024
MAX_WAV_BYTES = 512 * 1024
MAX_SOUNDS = 64
MAX_DURATION_MS = 2000
MAX_SAMPLE_RATE = 192000

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{1,95}\Z")
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}\.wav\Z")
_VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}(?:-rc\.\d{1,5})?\Z")
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,23}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LICENSES = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "MIT", "Apache-2.0"}
_BIT_DEPTHS = {8, 16, 24, 32}


class AudioPackageError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise AudioPackageError("音频包字段不匹配")
    return value


def _text(value, maximum, pattern=None):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AudioPackageError("音频包文本字段无效")
    if pattern is not None and not pattern.fullmatch(value):
        raise AudioPackageError("音频包文本字段格式无效")
    return value


def _integer(value, maximum, *, minimum=0):
    if type(value) is not int or value < minimum or value > maximum:
        raise AudioPackageError("音频包数值字段无效")
    return value


def _sha256(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise AudioPackageError("音频文件 SHA-256 无效")
    return value


def _sound(value):
    fields = ("sound_id", "title", "description", "filename", "bytes", "sha256",
              "duration_ms", "sample_rate", "channels", "bit_depth", "codec", "tags")
    row = _object(value, fields)
    _text(row["sound_id"], 96, _IDENTIFIER)
    _text(row["title"], 80)
    _text(row["description"], 500)
    _text(row["filename"], 67, _FILENAME)
    _integer(row["bytes"], MAX_WAV_BYTES, minimum=44)
    _sha256(row["sha256"])
    _integer(row["duration_ms"], MAX_DURATION_MS, minimum=1)
    _integer(row["sample_rate"], MAX_SAMPLE_RATE, minimum=8000)
    if row["channels"] not in (1, 2):
        raise AudioPackageError("音频声道数无效")
    if row["bit_depth"] not in _BIT_DEPTHS:
        raise AudioPackageError("音频位深无效")
    if row["codec"] != "pcm-wav":
        raise AudioPackageError("音频包只允许 PCM WAV")
    tags = row["tags"]
    if not isinstance(tags, list) or len(tags) > 8 or len(set(tags)) != len(tags):
        raise AudioPackageError("音频标签无效")
    for tag in tags:
        _text(tag, 24, _TAG)
    return row


def validate_audio_package(package):
    """Validate one data-only package and return it unchanged."""
    fields = ("schema", "package_id", "version", "release_sequence", "min_core_api",
              "kind", "title", "description", "author", "license", "sounds", "digest")
    row = _object(package, fields)
    if row["schema"] != AUDIO_PACKAGE_SCHEMA or row["kind"] != "audio-asset":
        raise AudioPackageError("音频包 schema 或类型无效")
    _text(row["package_id"], 96, _IDENTIFIER)
    _text(row["version"], 40, _VERSION)
    _integer(row["release_sequence"], 9007199254740991, minimum=1)
    _integer(row["min_core_api"], CORE_API_VERSION, minimum=1)
    _text(row["title"], 80)
    _text(row["description"], 500)
    _text(row["author"], 80)
    if row["license"] not in _LICENSES:
        raise AudioPackageError("音频包许可证不支持市场共享")
    sounds = row["sounds"]
    if not isinstance(sounds, list) or not sounds or len(sounds) > MAX_SOUNDS:
        raise AudioPackageError("音频包条目数量无效")
    validated = [_sound(sound) for sound in sounds]
    if len({sound["sound_id"] for sound in validated}) != len(validated):
        raise AudioPackageError("音频 ID 重复")
    if len({sound["filename"] for sound in validated}) != len(validated):
        raise AudioPackageError("音频文件名重复")
    body = dict(row)
    body.pop("digest")
    if row["digest"] != _digest(body):
        raise AudioPackageError("音频包摘要不匹配")
    return package


def validate_audio_index(index):
    """Validate a local marketplace index; signatures are handled by update layer."""
    fields = ("schema", "index_id", "version", "release_sequence", "min_core_api",
              "title", "packages", "digest")
    row = _object(index, fields)
    if row["schema"] != AUDIO_INDEX_SCHEMA:
        raise AudioPackageError("音频索引 schema 无效")
    _text(row["index_id"], 96, _IDENTIFIER)
    _text(row["version"], 40, _VERSION)
    _integer(row["release_sequence"], 9007199254740991, minimum=1)
    _integer(row["min_core_api"], CORE_API_VERSION, minimum=1)
    _text(row["title"], 80)
    packages = row["packages"]
    if not isinstance(packages, list) or not packages or len(packages) > 256:
        raise AudioPackageError("音频索引包数量无效")
    packages = [validate_audio_package(package) for package in packages]
    if len({package["package_id"] for package in packages}) != len(packages):
        raise AudioPackageError("音频包 ID 重复")
    sound_keys = {(package["package_id"], sound["sound_id"])
                  for package in packages for sound in package["sounds"]}
    if len(sound_keys) != sum(len(package["sounds"]) for package in packages):
        raise AudioPackageError("音频索引中的音频 ID 重复")
    body = dict(row)
    body.pop("digest")
    if row["digest"] != _digest(body):
        raise AudioPackageError("音频索引摘要不匹配")
    return index


def select_sound(index, package_id, sound_id):
    """Resolve one immutable sound description without downloading or writing."""
    validate_audio_index(index)
    if not isinstance(package_id, str) or not isinstance(sound_id, str):
        raise AudioPackageError("音频选择无效")
    for package in index["packages"]:
        if package["package_id"] == package_id:
            for sound in package["sounds"]:
                if sound["sound_id"] == sound_id:
                    return dict(package=package_id, package_version=package["version"],
                                package_digest=package["digest"], **sound)
    raise AudioPackageError("音频索引中没有所选音频")


def _default_sound_id(filename):
    stem = filename[:-4].replace("_", "-").replace(" ", "-").lower()
    stem = re.sub(r"[^a-z0-9.-]+", "-", stem).strip("-")
    if not _IDENTIFIER.fullmatch(stem):
        stem = ("sound-" + stem).strip("-")
    return stem if _IDENTIFIER.fullmatch(stem) else "sound"


def build_audio_package(sounds, *, package_id, version, title, description,
                        author, license, release_sequence=1,
                        min_core_api=CORE_API_VERSION):
    """Build one data-only package from one or more already valid WAV inputs.

    ``sounds`` is a list of mappings with ``filename``, ``data``, and optional
    presentation metadata. The builder never executes any code and does not
    attach camera payloads or workflow steps.
    """
    if not isinstance(sounds, list) or not sounds or len(sounds) > MAX_SOUNDS:
        raise AudioPackageError("音频包导出条目数量无效")
    rows = []
    used_ids = set()
    used_filenames = set()
    for sound in sounds:
        if not isinstance(sound, dict) or set(sound) - {
                "filename", "data", "sound_id", "title", "description", "tags"}:
            raise AudioPackageError("音频包导出字段无效")
        filename = sound.get("filename")
        data = sound.get("data")
        if not isinstance(filename, str) or not isinstance(data, bytes):
            raise AudioPackageError("音频包导出 WAV 无效")
        if filename in used_filenames:
            raise AudioPackageError("音频包导出文件名重复")
        used_filenames.add(filename)
        description = sound.get("description") or "Local shutter sound."
        inspected = inspect_pcm_wav(data, filename,
                                    title=sound.get("title"),
                                    description=description,
                                    tags=sound.get("tags", ()))
        sound_id = sound.get("sound_id") or _default_sound_id(filename)
        if sound_id in used_ids:
            raise AudioPackageError("音频包导出音频 ID 重复")
        used_ids.add(sound_id)
        row = dict(
            sound_id=sound_id, title=inspected["title"],
            description=inspected["description"], filename=filename,
            bytes=inspected["bytes"], sha256=inspected["sha256"],
            duration_ms=inspected["duration_ms"],
            sample_rate=inspected["sample_rate"],
            channels=inspected["channels"], bit_depth=inspected["bit_depth"],
            codec=inspected["codec"], tags=inspected["tags"])
        rows.append(row)
    body = dict(
        schema=AUDIO_PACKAGE_SCHEMA, package_id=package_id, version=version,
        release_sequence=release_sequence, min_core_api=min_core_api,
        kind="audio-asset", title=title, description=description,
        author=author, license=license, sounds=rows)
    package = dict(body, digest=_digest(body))
    return validate_audio_package(package)


def build_audio_bundle(package, sounds):
    """Create a deterministic data-only ZIP bundle for one package."""
    validate_audio_package(package)
    if not isinstance(sounds, list) or not sounds:
        raise AudioPackageError("音频包导出 WAV 列表无效")
    entries = {}
    for sound in sounds:
        if not isinstance(sound, dict) or set(sound) != {"filename", "data"}:
            raise AudioPackageError("音频包导出 WAV 字段无效")
        filename = sound["filename"]
        if filename in entries:
            raise AudioPackageError("音频包导出 WAV 文件名重复")
        entries[filename] = sound["data"]
    expected = {sound["filename"]: sound for sound in package["sounds"]}
    if set(entries) != set(expected):
        raise AudioPackageError("音频包导出 WAV 与清单不匹配")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest_json = json.dumps(package, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False).encode("utf-8")
        info = zipfile.ZipInfo("package.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100600 << 16
        archive.writestr(info, manifest_json)
        for filename in sorted(entries):
            info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            archive.writestr(info, entries[filename])
    bundle = output.getvalue()
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise AudioPackageError("音频包导出超过大小限制")
    validate_audio_bundle(bundle)
    return bundle


def _strict_json(raw, limit):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise AudioPackageError("音频包 JSON 大小无效")

    def duplicate(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AudioPackageError("音频包 JSON 字段重复")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise AudioPackageError("音频包 JSON 包含非有限数值")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate,
                          parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AudioPackageError("音频包 JSON 无效") from AudioPackageError


def _parse_pcm_wav(data):
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioPackageError("音频不是 RIFF WAV")
    if len(data) > MAX_WAV_BYTES:
        raise AudioPackageError("音频文件超过大小限制")
    declared_riff_size = struct.unpack_from("<I", data, 4)[0]
    if declared_riff_size != len(data) - 8:
        raise AudioPackageError("RIFF 长度不一致")

    fmt = None
    data_chunk = None
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > len(data):
            raise AudioPackageError("WAV chunk 长度越界")
        if chunk_id == b"fmt ":
            if chunk_size < 16 or fmt is not None:
                raise AudioPackageError("WAV fmt chunk 无效")
            fmt = struct.unpack_from("<HHIIHH", data, chunk_start)
        elif chunk_id == b"data":
            if data_chunk is not None:
                raise AudioPackageError("WAV 包含多个 data chunk")
            data_chunk = (chunk_start, chunk_size)
        offset = chunk_end + (chunk_size & 1)
    if offset != len(data) or fmt is None or data_chunk is None:
        raise AudioPackageError("WAV chunk 不完整")

    audio_format, channels, sample_rate, byte_rate, block_align, bits = fmt
    if audio_format != 1:
        raise AudioPackageError("音频必须是整数 PCM WAV")
    if channels not in (1, 2) or bits not in _BIT_DEPTHS:
        raise AudioPackageError("音频声道或位深无效")
    if not 8000 <= sample_rate <= MAX_SAMPLE_RATE:
        raise AudioPackageError("音频采样率无效")
    if block_align != channels * bits // 8 or byte_rate != sample_rate * block_align:
        raise AudioPackageError("WAV fmt 参数不一致")
    frame_count = data_chunk[1] // block_align
    if data_chunk[1] % block_align or frame_count < 1:
        raise AudioPackageError("WAV 存在不完整帧")
    duration_ms = math.ceil(frame_count * 1000 / sample_rate)
    if duration_ms > MAX_DURATION_MS:
        raise AudioPackageError("音频时长超过限制")
    payload = data[data_chunk[0]:data_chunk[0] + data_chunk[1]]
    maximum = (1 << (bits - 1)) - 1
    peak_square = 0
    rms_square = 0
    sample_count = 0
    if bits == 8:
        values = (value - 128 for value in payload)
    elif bits == 16:
        values = (value[0] for value in struct.iter_unpack("<h", payload))
    elif bits == 24:
        values = (((payload[index] << 16) | (payload[index + 1] << 8)
                   | payload[index + 2]) - (1 << 24)
                  if payload[index] & 0x80 else
                  (payload[index] << 16) | (payload[index + 1] << 8) | payload[index + 2]
                  for index in range(0, len(payload), 3))
    else:
        values = (value[0] for value in struct.iter_unpack("<i", payload))
    for value in values:
        square = value * value
        peak_square = max(peak_square, square)
        rms_square += square
        sample_count += 1
    rms = min(1.0, math.sqrt(rms_square / sample_count) / maximum)
    peak = min(1.0, math.sqrt(peak_square) / maximum)
    return dict(codec="pcm-wav", bytes=len(data), duration_ms=duration_ms,
                sample_rate=sample_rate, channels=channels, bit_depth=bits,
                frame_count=frame_count, peak_amplitude=round(peak, 6),
                rms_amplitude=round(rms, 6))


def inspect_pcm_wav(data, filename, *, title=None, description="", tags=()):
    """Inspect one user-selected PCM WAV and return path-free metadata."""
    from .custom_sound_slots import encode_slot_name

    if not isinstance(data, bytes) or not data:
        raise AudioPackageError("音频内容无效")
    try:
        encode_slot_name(filename)
    except ValueError as exc:
        raise AudioPackageError(str(exc)) from exc
    parsed = _parse_pcm_wav(data)
    resolved_title = title or _filename_title(filename)[:80].strip() or "Selected sound"
    parsed.update(
        filename=filename,
        title=_text(resolved_title, 80),
        description=_text(description, 500) if description else "",
        sha256=hashlib.sha256(data).hexdigest(),
        tags=list(tags),
    )
    return parsed


CAMERA_SAMPLE_RATE = 24000
# The camera worker allocates exactly one 350 ms Kind3 table per slot
# (8400 samples -> 271 frames -> 8672 bytes) and silently leaves any other
# length unloaded at cold boot. Every slot WAV is normalized to this shape.
CAMERA_CLIP_FRAMES = 8400


def _pcm_payload(data):
    offset = 12
    while offset + 8 <= len(data):
        size = struct.unpack_from("<I", data, offset + 4)[0]
        if data[offset:offset + 4] == b"data":
            return data[offset + 8:offset + 8 + size]
        offset += 8 + size + (size & 1)
    raise AudioPackageError("WAV chunk 不完整")


def _camera_wav(samples):
    payload = struct.pack(f"<{len(samples)}h", *samples)
    return (b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, CAMERA_SAMPLE_RATE,
                                    CAMERA_SAMPLE_RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", len(payload)) + payload)


def normalize_camera_clip(data):
    """Return the exact 24 kHz mono 16-bit 350 ms WAV a camera slot can load.

    Shorter clips are padded with trailing silence; longer clips are rejected
    instead of being silently cut. Applies to local, bundled and market audio.
    """
    if not isinstance(data, bytes) or not data:
        raise AudioPackageError("音频内容无效")
    parsed = _parse_pcm_wav(data)
    rate, channels, bits = parsed["sample_rate"], parsed["channels"], parsed["bit_depth"]
    frames = parsed["frame_count"]
    target = frames if rate == CAMERA_SAMPLE_RATE else max(
        1, round(frames * CAMERA_SAMPLE_RATE / rate))
    if target > CAMERA_CLIP_FRAMES + 1:
        raise AudioPackageError("相机槽位音频最长 350 毫秒，请先裁剪后再导入。")
    payload = _pcm_payload(data)
    if rate == CAMERA_SAMPLE_RATE and channels == 1 and bits == 16:
        if frames == CAMERA_CLIP_FRAMES and len(data) == 44 + CAMERA_CLIP_FRAMES * 2:
            return data
        samples = list(struct.unpack(f"<{frames}h", payload[:frames * 2]))
    else:
        width = bits // 8
        scale = float(1 << (bits - 1))
        mono = []
        for index in range(0, frames * width * channels, width * channels):
            total = 0
            for channel in range(channels):
                raw = payload[index + channel * width:index + (channel + 1) * width]
                total += raw[0] - 128 if bits == 8 else int.from_bytes(raw, "little", signed=True)
            mono.append(total / channels / scale)
        samples = []
        ratio = rate / CAMERA_SAMPLE_RATE
        last = len(mono) - 1
        for index in range(target):
            position = index * ratio
            left = min(int(position), last)
            right = min(left + 1, last)
            fraction = position - left if left < last else 0.0
            value = mono[left] * (1.0 - fraction) + mono[right] * fraction
            samples.append(max(-32768, min(32767, int(round(value * 32767.0)))))
    samples = samples[:CAMERA_CLIP_FRAMES]
    samples += [0] * (CAMERA_CLIP_FRAMES - len(samples))
    return _camera_wav(samples)


def _filename_title(filename):
    stem = filename[:-4] if filename.lower().endswith(".wav") else filename
    return stem.replace("_", " ").replace("-", " ").strip() or "Selected sound"


def _validate_pcm_wav(data, sound):
    parsed = _parse_pcm_wav(data)
    expected = {
        "bytes": sound["bytes"], "duration_ms": sound["duration_ms"],
        "sample_rate": sound["sample_rate"], "channels": sound["channels"],
        "bit_depth": sound["bit_depth"], "codec": sound["codec"],
    }
    actual = {key: parsed[key] for key in expected}
    if actual != expected:
        raise AudioPackageError("音频参数与清单不一致")


def validate_audio_bundle(data):
    """Validate a data-only ZIP bundle without extracting it."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_BUNDLE_BYTES:
        raise AudioPackageError("音频包大小无效")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AudioPackageError("音频包不是有效 ZIP") from exc
    if archive.comment:
        raise AudioPackageError("音频包不允许 ZIP 注释")
    infos = archive.infolist()
    if len(infos) > MAX_SOUNDS + 1:
        raise AudioPackageError("音频包条目数量无效")
    names = []
    for info in infos:
        if info.is_dir() or info.flag_bits & 0x1:
            raise AudioPackageError("音频包包含目录或加密条目")
        name = info.filename
        if (name != "package.json" and not _FILENAME.fullmatch(name)
                or "/" in name or "\\" in name or name.startswith(".")):
            raise AudioPackageError("音频包路径不安全")
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise AudioPackageError("音频包不允许符号链接")
        if info.file_size > MAX_WAV_BYTES:
            raise AudioPackageError("音频文件超过大小限制")
        names.append(name)
    if len(names) != len(set(names)) or "package.json" not in names:
        raise AudioPackageError("音频包条目重复或缺少清单")

    manifest_info = archive.getinfo("package.json")
    if manifest_info.file_size > MAX_PACKAGE_BYTES:
        raise AudioPackageError("音频包清单超过大小限制")
    manifest = _strict_json(archive.read("package.json"), MAX_PACKAGE_BYTES)
    validate_audio_package(manifest)
    expected = {sound["filename"]: sound for sound in manifest["sounds"]}
    if set(names) != {"package.json", *expected}:
        raise AudioPackageError("音频包文件与清单不匹配")

    entries = {}
    for filename, sound in expected.items():
        info = archive.getinfo(filename)
        if info.file_size != sound["bytes"]:
            raise AudioPackageError("音频文件大小与清单不一致")
        content = archive.read(filename)
        _validate_pcm_wav(content, sound)
        digest = hashlib.sha256(content).hexdigest()
        if digest != sound["sha256"]:
            raise AudioPackageError("音频文件哈希与清单不一致")
        entries[filename] = dict(bytes=len(content), sha256=digest)
    return dict(manifest=manifest, entries=entries)
