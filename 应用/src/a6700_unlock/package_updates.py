"""Signed update indexes and local content-addressed update caches.

Network download and camera activation are deliberately absent. Callers supply
index/artifact bytes and a pinned trust store; this module verifies and stores
them without treating a server response as proof of correctness. Adapter sets
and data-only audio bundles use the same trust root but separate validators.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .audio_packages import validate_audio_bundle
from .capability_packages import validate_adapter_set
from .host_io import open_exclusive, sync_directory
from .operations import make_directory

CHANNELS = {"stable", "testing"}
MAX_ENVELOPE = 262144
MAX_PAYLOAD = 250000
MAX_ARTIFACTS = 256
MAX_ADAPTER_BYTES = 4 * 1024 * 1024
MAX_AUDIO_BUNDLE_BYTES = 68 * 1024 * 1024
_KEY_ID = re.compile(r"[a-z0-9_.-]{1,64}\Z")
_VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}(?:(?:\.dev|-rc\.)\d{1,5})?\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_B64 = re.compile(r"[A-Za-z0-9+/]+={0,2}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
_URL_PREFIX = "/xyzhangxin/sony-a6700-powerful/releases/download/"


class UpdateError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("更新内容包含重复字段")
        result[key] = value
    return result


def _json(raw, limit):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise UpdateError("更新内容大小或编码无效")
    def invalid_constant(value):
        raise UpdateError("更新内容包含非有限数值")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("更新内容不是严格 JSON") from exc
    stack = [(result, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > 12:
            raise UpdateError("更新内容嵌套过深")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    return result


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise UpdateError("更新内容字段不匹配")
    return value


def _text(value, maximum, pattern=None):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise UpdateError("更新文本字段无效")
    if pattern is not None and not pattern.fullmatch(value):
        raise UpdateError("更新文本字段格式无效")
    return value


def _integer(value, maximum):
    if type(value) is not int or value < 0 or value > maximum:
        raise UpdateError("更新数值字段无效")
    return value


def _decode64(value, maximum):
    text = _text(value, maximum, _B64)
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UpdateError("Base64 编码无效") from exc
    if base64.b64encode(decoded).decode("ascii") != text:
        raise UpdateError("Base64 编码不是规范形式")
    return decoded


def _digest(value):
    return _text(value, 64, _DIGEST)


def _time(value):
    text = _text(value, 30, _TIME)
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _url(value):
    from urllib.parse import urlsplit
    text = _text(value, 2048)
    url = urlsplit(text)
    if (url.scheme != "https" or url.hostname != "github.com"
            or not url.path.startswith(_URL_PREFIX)
            or url.username or url.password or url.query or url.fragment):
        raise UpdateError("更新附件地址不在已审查的发布源")
    return text


def _artifact(value):
    fields = ("id", "version", "url", "source_url", "bytes", "sha256",
              "kind", "platform", "arch", "min_core_api")
    row = _object(value, fields)
    _text(row["id"], 96, re.compile(r"[a-z][a-z0-9_.-]{1,95}\Z"))
    _text(row["version"], 40, _VERSION)
    _url(row["url"])
    _url(row["source_url"])
    if _integer(row["bytes"], 1073741824) == 0:
        raise UpdateError("空附件不被接受")
    _digest(row["sha256"])
    if row["kind"] not in ("app", "package", "adapter-set", "audio-bundle"):
        raise UpdateError("未知附件类型")
    if row["platform"] not in ("macos", "windows", "linux", "camera", "any"):
        raise UpdateError("未知附件平台")
    if row["arch"] not in ("arm64", "x64", "any"):
        raise UpdateError("未知附件架构")
    if row["kind"] == "audio-bundle":
        if row["platform"] != "any" or row["arch"] != "any":
            raise UpdateError("音频数据包必须为平台无关附件")
    elif row["platform"] == "any" or row["arch"] == "any":
        raise UpdateError("该附件不允许使用平台无关标识")
    _integer(row["min_core_api"], 100000)
    return row


def verify_index_envelope(raw, trusted_keys, channel, now, *, allow_expired=False):
    """Verify exact signed bytes and payload semantics; never mutate the cache."""
    if channel not in CHANNELS or not isinstance(now, datetime):
        raise UpdateError("更新通道或检查时间无效")
    if now.tzinfo is None:
        raise UpdateError("检查时间必须包含时区")
    envelope = _object(_json(raw, MAX_ENVELOPE), ("payload", "signature", "key_id"))
    key_id = _text(envelope["key_id"], 64, _KEY_ID)
    if key_id not in trusted_keys:
        raise UpdateError("更新签名者不受信任")
    public_key = _decode64(trusted_keys[key_id], 100)
    signature = _decode64(envelope["signature"], 100)
    payload_bytes = _decode64(envelope["payload"], MAX_PAYLOAD)
    if len(public_key) != 32 or len(signature) != 64:
        raise UpdateError("更新密钥或签名长度无效")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise UpdateError("缺少签名验证依赖，请安装 a6700-unlock[updates]") from exc
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload_bytes)
    except Exception as exc:
        raise UpdateError("更新索引签名无效") from exc

    payload = _object(_json(payload_bytes, MAX_PAYLOAD),
                      ("schema_version", "sequence", "channel", "generated_at",
                       "expires_at", "artifacts", "revoked"))
    if payload["schema_version"] != 1 or payload["channel"] != channel:
        raise UpdateError("更新索引 schema 或通道不匹配")
    if _integer(payload["sequence"], 9007199254740991) == 0:
        raise UpdateError("更新序号必须大于零")
    generated = _time(payload["generated_at"])
    expires = _time(payload["expires_at"])
    if generated.timestamp() > now.timestamp() + 300 or expires <= generated:
        raise UpdateError("更新索引时间范围无效")
    if not allow_expired and expires <= now:
        raise UpdateError("更新索引已过期")
    if not isinstance(payload["artifacts"], list) or len(payload["artifacts"]) > MAX_ARTIFACTS:
        raise UpdateError("更新附件清单无效")
    artifacts = [_artifact(row) for row in payload["artifacts"]]
    if len({row["id"] for row in artifacts}) != len(artifacts):
        raise UpdateError("更新附件 ID 重复")
    if not isinstance(payload["revoked"], list) or len(payload["revoked"]) > MAX_ARTIFACTS:
        raise UpdateError("撤回清单无效")
    revoked = [_digest(item) for item in payload["revoked"]]
    return dict(raw_envelope=raw, payload=payload, sequence=payload["sequence"],
                channel=channel, generated_at=generated, expires_at=expires,
                artifacts=artifacts, revoked=revoked)


def _write_exclusive(path, data):
    fd = open_exclusive(path)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    sync_directory(path.parent)


def _atomic_replace(path, data):
    make_directory(path.parent)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        _write_exclusive(temporary, data)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _regular_file(path):
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


class LocalUpdateCache:
    def __init__(self, root):
        self.root = Path(root)
        self.indexes = self.root / "indexes"
        self.artifacts = self.root / "artifacts"

    def _current(self, channel, trusted_keys, now):
        path = self.indexes / (channel + ".json")
        if not _regular_file(path):
            raise FileNotFoundError("本机没有已激活的更新索引")
        return verify_index_envelope(path.read_bytes(), trusted_keys, channel, now, allow_expired=True)

    def _store_artifact(self, artifact, data):
        if len(data) != artifact["bytes"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise UpdateError("附件长度或 SHA-256 与签名索引不一致")
        if artifact["kind"] == "adapter-set":
            if len(data) > MAX_ADAPTER_BYTES:
                raise UpdateError("适配集附件超过本地上限")
            value = _json(data, MAX_ADAPTER_BYTES)
            validate_adapter_set(value)
        elif artifact["kind"] == "audio-bundle":
            if len(data) > MAX_AUDIO_BUNDLE_BYTES:
                raise UpdateError("音频包附件超过本地上限")
            value = validate_audio_bundle(data)
        else:
            raise UpdateError("该附件类型不由本地功能包缓存管理")
        path = self.artifacts / artifact["sha256"]
        if path.exists():
            if not _regular_file(path) or path.read_bytes() != data:
                raise UpdateError("缓存附件已损坏；不会覆盖")
            return value
        make_directory(path.parent)
        _write_exclusive(path, data)
        return value

    def activate_index(self, index, trusted_keys, now, artifact_bytes):
        """Activate only a newer valid index, after all adapter bytes are durable."""
        index = verify_index_envelope(index["raw_envelope"], trusted_keys,
                                      index["channel"], now)
        current_raw = None
        current = None
        try:
            current = self._current(index["channel"], trusted_keys, now)
            current_raw = self.indexes.joinpath(index["channel"] + ".json").read_bytes()
        except FileNotFoundError:
            pass
        if current is not None:
            if current["sequence"] > index["sequence"]:
                raise UpdateError("拒绝回放旧更新序号")
            if current["sequence"] == index["sequence"]:
                if current_raw != index["raw_envelope"]:
                    raise UpdateError("同一更新序号绑定了不同内容")
                return dict(status="unchanged", sequence=index["sequence"])
        if sum(artifact["kind"] == "adapter-set" for artifact in index["artifacts"]) != 1:
            raise UpdateError("更新索引必须精确对应一个适配集")
        for artifact in index["artifacts"]:
            if artifact["kind"] not in ("adapter-set", "audio-bundle"):
                continue
            if artifact["id"] not in artifact_bytes:
                raise UpdateError("缓存附件尚未下载完整：" + artifact["id"])
            self._store_artifact(artifact, artifact_bytes[artifact["id"]])
        _atomic_replace(self.indexes / (index["channel"] + ".json"), index["raw_envelope"])
        return dict(status="activated", sequence=index["sequence"])

    def cached_etag(self, channel):
        path = self.indexes / (channel + ".json")
        if not _regular_file(path):
            return None
        return '"' + hashlib.sha256(path.read_bytes()).hexdigest() + '"'

    def active_adapter_set(self, channel, trusted_keys, now, *, for_write=True):
        try:
            index = self._current(channel, trusted_keys, now)
        except FileNotFoundError as exc:
            raise UpdateError("本机没有已激活的更新索引") from exc
        artifacts = [row for row in index["artifacts"] if row["kind"] == "adapter-set"]
        if len(artifacts) != 1:
            raise UpdateError("当前索引必须精确对应一个适配集")
        artifact = artifacts[0]
        path = self.artifacts / artifact["sha256"]
        if not _regular_file(path):
            raise UpdateError("已激活适配集附件缺失")
        data = path.read_bytes()
        if len(data) != artifact["bytes"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise UpdateError("已激活适配集附件校验失败")
        adapter = _json(data, MAX_ADAPTER_BYTES)
        validate_adapter_set(adapter)
        if for_write and adapter["digest"] in index["revoked"]:
            raise UpdateError("当前适配集已撤回，停止新写入；保留诊断和恢复资料")
        expired = index["expires_at"] <= now
        return dict(adapter_set=adapter, sequence=index["sequence"],
                    status="cached-expired" if expired else "active",
                    artifact=artifact)

    def active_audio_bundles(self, channel, trusted_keys, now, *, for_write=True):
        try:
            index = self._current(channel, trusted_keys, now)
        except FileNotFoundError as exc:
            raise UpdateError("本机没有已激活的更新索引") from exc
        bundles = []
        package_ids = []
        for artifact in index["artifacts"]:
            if artifact["kind"] != "audio-bundle":
                continue
            path = self.artifacts / artifact["sha256"]
            if not _regular_file(path):
                raise UpdateError("已激活音频包附件缺失")
            data = path.read_bytes()
            if (len(data) != artifact["bytes"]
                    or hashlib.sha256(data).hexdigest() != artifact["sha256"]):
                raise UpdateError("已激活音频包附件校验失败")
            bundle = validate_audio_bundle(data)
            package_id = bundle["manifest"]["package_id"]
            if package_id in package_ids:
                raise UpdateError("音频市场包 ID 重复")
            package_ids.append(package_id)
            if for_write and artifact["sha256"] in index["revoked"]:
                raise UpdateError("当前音频包已撤回，停止新导入；保留诊断和本地资料")
            bundles.append(dict(bundle=bundle, artifact=artifact))
        expired = index["expires_at"] <= now
        return dict(bundles=bundles, sequence=index["sequence"],
                    status="cached-expired" if expired else "active")
