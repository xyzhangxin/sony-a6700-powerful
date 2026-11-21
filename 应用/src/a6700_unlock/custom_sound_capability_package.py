"""Strict loader and sparse transaction helpers for custom-shutter-sound."""
from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

from .device_profile import _zero_inode_times
from .device_protocol import DeviceError, request as protocol_request, response as protocol_response


CAPABILITY_PACKAGE_SCHEMA = "a6700.custom-sound-capability-package/3"
ROOT_INPUT_PATH = "root-input/a6700-2.00.json"
ROOT_INPUT_LOGICAL_PATH = "release/src/a6700_unlock/data/a6700-2.00.json"
MENU_UI_PATH = "menu-ui/manifest.json"


class CustomSoundCapabilityPackageError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _MenuPartProfile:
    def __init__(self, manifest: dict):
        self.digest = manifest["profile_sha256"]
        self.entries = manifest["entries"]
        self.data = {"devices": manifest["devices"]}
        self.inode_size = manifest.get("inode_size")
        self.volatile_fields = tuple(tuple(span) for span in manifest.get("volatile_fields", ()))

    def masks(self, writable_only: bool = True):
        del writable_only
        return [bytes.fromhex(entry["mask_hex"]) for entry in self.entries]


def _validate_sparse_entry(entry: dict) -> bytes:
    mask_hex = entry.get("mask_hex")
    edits = entry.get("edits")
    unmanaged_sha = entry.get("unmanaged_sha256")
    if (not isinstance(mask_hex, str) or len(mask_hex) != 128
            or not isinstance(unmanaged_sha, str) or len(unmanaged_sha) != 64
            or not isinstance(edits, list) or not edits):
        raise CustomSoundCapabilityPackageError("自定义快门声菜单UI稀疏条目格式无效")
    computed_mask = bytearray(64)
    covered = set()
    for edit in edits:
        if not isinstance(edit, dict):
            raise CustomSoundCapabilityPackageError("自定义快门声菜单UI稀疏补丁项无效")
        offset = edit.get("offset")
        disabled = edit.get("disabled")
        enabled = edit.get("enabled")
        if (type(offset) is not int or not isinstance(disabled, str)
                or not isinstance(enabled, str)):
            raise CustomSoundCapabilityPackageError("自定义快门声菜单UI稀疏补丁字段无效")
        old_bytes = bytes.fromhex(disabled)
        new_bytes = bytes.fromhex(enabled)
        if (not old_bytes or len(old_bytes) != len(new_bytes)
                or old_bytes == new_bytes or offset < 0 or offset + len(old_bytes) > 512):
            raise CustomSoundCapabilityPackageError("自定义快门声菜单UI稀疏补丁范围无效")
        for at in range(offset, offset + len(old_bytes)):
            if at in covered:
                raise CustomSoundCapabilityPackageError("自定义快门声菜单UI稀疏补丁字节重叠")
            covered.add(at)
            if old_bytes[at - offset] != new_bytes[at - offset]:
                computed_mask[at // 8] |= 1 << (at % 8)
    if bytes(computed_mask).hex() != mask_hex:
        raise CustomSoundCapabilityPackageError("自定义快门声菜单UI稀疏掩码不一致")
    return bytes(computed_mask)


def load_custom_sound_menu_ui_assets(root: Path | None = None):
    """Load and verify the ordered custom-sound menu UI transaction parts.

    When loading the shipped package (``root is None``), only sparse edits and
    ``reader.elf``/``writer.elf`` payloads are allowed (no raw firmware ``.bin``
    sectors). Explicit research folders with ``before.bin``/``after.bin`` remain
    supported for offline cross-verification.
    """
    shipped = root is None
    if shipped:
        root = files("a6700_unlock").joinpath("data/custom-sound/capability/menu-ui")
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("camera_access") is not False
            or type(manifest.get("installable")) is not bool
            or (shipped and manifest.get("installable") is not True)
            or type(manifest.get("sector_count")) is not int
            or manifest["sector_count"] <= 0
            or manifest.get("schema") != "a6700-custom-sound-menu-ui-parts/1"
            or not isinstance(manifest.get("parts"), list)
            or not manifest["parts"]):
        raise CustomSoundCapabilityPackageError("自定义快门声菜单UI清单无效")
    for name, digest in manifest.get("files", {}).items():
        data = root.joinpath(name).read_bytes()
        if _sha(data) != digest:
            raise CustomSoundCapabilityPackageError("自定义快门声菜单UI资产校验失败")

    parts = []
    seen_targets = set()
    before_digests = []
    after_digests = []
    raw_before_chunks = []
    raw_after_chunks = []
    has_raw_bins = None

    for row in manifest["parts"]:
        name = row.get("name")
        if not isinstance(name, str) or "/" in name or not name:
            raise CustomSoundCapabilityPackageError("自定义快门声菜单UI分片名无效")
        folder = root.joinpath(name)
        part = json.loads(folder.joinpath("manifest.json").read_text(encoding="utf-8"))
        if (part.get("camera_access") is not False
                or type(part.get("installable")) is not bool
                or (shipped and part.get("installable") is not True)
                or part.get("part") != name
                or part.get("profile_sha256") != row.get("profile_sha256")
                or part.get("sector_count") != row.get("sector_count")
                or type(part.get("sector_count")) is not int
                or part["sector_count"] <= 0
                or part["sector_count"] > 1023
                or len(part.get("entries", [])) != part["sector_count"]):
            raise CustomSoundCapabilityPackageError("自定义快门声菜单UI分片清单无效")
        part_files = part.get("files", {})
        for file, digest in part_files.items():
            if _sha(folder.joinpath(file).read_bytes()) != digest:
                raise CustomSoundCapabilityPackageError("自定义快门声菜单UI分片资产校验失败")
        if "reader.elf" not in part_files or "writer.elf" not in part_files:
            raise CustomSoundCapabilityPackageError("自定义快门声菜单UI分片缺少原生事务载荷")

        part_has_bins = "before.bin" in part_files and "after.bin" in part_files
        if shipped and part_has_bins:
            raise CustomSoundCapabilityPackageError("发行包禁止附带原始固件扇区镜像")
        if has_raw_bins is None:
            has_raw_bins = part_has_bins
        elif has_raw_bins != part_has_bins:
            raise CustomSoundCapabilityPackageError("自定义快门声菜单UI分片格式不一致")

        count = part["sector_count"]
        for entry in part["entries"]:
            target_key = (entry.get("device"), entry.get("device_offset"))
            if target_key in seen_targets:
                raise CustomSoundCapabilityPackageError("自定义快门声菜单UI扇区重迭")
            seen_targets.add(target_key)
            if "edits" in entry or not part_has_bins:
                _validate_sparse_entry(entry)

        item = dict(
            name=name,
            folder=Path(str(folder)),
            manifest=part,
            profile=_MenuPartProfile(part),
            reader=folder.joinpath("reader.elf").read_bytes(),
            writer=folder.joinpath("writer.elf").read_bytes(),
            binary=folder.joinpath("reader.elf").read_bytes(),
        )
        if part_has_bins:
            part_before = folder.joinpath("before.bin").read_bytes()
            part_after = folder.joinpath("after.bin").read_bytes()
            if (len(part_before) != count * 512 or len(part_after) != len(part_before)
                    or _sha(part_before) != part.get("before_sha256")
                    or _sha(part_after) != part.get("after_sha256")
                    or _sha(part_before) != row.get("before_sha256")
                    or _sha(part_after) != row.get("after_sha256")):
                raise CustomSoundCapabilityPackageError("自定义快门声菜单UI分片镜像不一致")
            item["before"] = [part_before[i * 512:(i + 1) * 512] for i in range(count)]
            item["after"] = [part_after[i * 512:(i + 1) * 512] for i in range(count)]
            raw_before_chunks.append(part_before)
            raw_after_chunks.append(part_after)
        else:
            if (part.get("before_sha256") != row.get("before_sha256")
                    or part.get("after_sha256") != row.get("after_sha256")):
                raise CustomSoundCapabilityPackageError("自定义快门声菜单UI稀疏摘要不一致")
        before_digests.append(part.get("before_sha256"))
        after_digests.append(part.get("after_sha256"))
        parts.append(item)

    if sum(part["manifest"]["sector_count"] for part in parts) != manifest["sector_count"]:
        raise CustomSoundCapabilityPackageError("自定义快门声菜单UI总扇区数不一致")
    if has_raw_bins and (
            _sha(b"".join(raw_before_chunks)) != manifest.get("before_sha256")
            or _sha(b"".join(raw_after_chunks)) != manifest.get("after_sha256")):
        raise CustomSoundCapabilityPackageError("自定义快门声菜单UI整体镜像不一致")
    return dict(folder=Path(str(root)), manifest=manifest, parts=parts)


def plan_menu_ui_part(part: dict, sectors: list[bytes], action: str):
    """Inspect or plan one sparse menu-UI transaction part from observed sectors."""
    if action not in ("inspect", "unlock", "close"):
        raise ValueError("Unsupported menu UI action")
    profile = part["profile"]
    entries = profile.entries
    if len(sectors) != len(entries) or any(len(s) != 512 for s in sectors):
        raise DeviceError("自定义快门声菜单UI读取扇区数不完整")

    inode_size = getattr(profile, "inode_size", None)
    volatile_fields = getattr(profile, "volatile_fields", ())
    masks = profile.masks(writable_only=True)
    sector_states = []
    after_sectors = []
    changed_bytes = 0

    for entry, raw, mask in zip(entries, sectors, masks):
        unmanaged = bytearray(raw)
        for at in range(512):
            if mask[at // 8] & (1 << (at % 8)):
                unmanaged[at] = 0
        unmanaged_bytes = bytes(unmanaged)
        if inode_size and volatile_fields:
            unmanaged_bytes = _zero_inode_times(unmanaged_bytes, inode_size, volatile_fields)
        if _sha(unmanaged_bytes) != entry["unmanaged_sha256"]:
            raise DeviceError(
                f"自定义快门声菜单UI扇区 {entry['absolute_sector']} 非目标内容发生变化"
            )

        target = bytearray(raw)
        edit_states = []
        for edit in entry["edits"]:
            at = edit["offset"]
            old = bytes.fromhex(edit["disabled"])
            new = bytes.fromhex(edit["enabled"])
            actual = raw[at:at + len(old)]
            if actual not in (old, new):
                raise DeviceError(
                    f"自定义快门声菜单UI扇区 {entry['absolute_sector']} 当前字节不是已支持状态"
                )
            edit_states.append("enabled" if actual == new else "disabled")
            if action == "unlock":
                target[at:at + len(old)] = new
            elif action == "close":
                target[at:at + len(old)] = old

        target_bytes = bytes(target)
        sector_state = (
            "enabled" if set(edit_states) == {"enabled"}
            else "disabled" if set(edit_states) == {"disabled"}
            else "partial"
        )
        sector_states.append(sector_state)
        if action in ("unlock", "close"):
            key = ("normalized_" if (inode_size and volatile_fields) else "") + (
                "after_sha256" if action == "unlock" else "before_sha256"
            )
            checked_target = (
                _zero_inode_times(target_bytes, inode_size, volatile_fields)
                if (inode_size and volatile_fields) else target_bytes
            )
            if _sha(checked_target) != entry[key]:
                raise DeviceError(
                    f"自定义快门声菜单UI扇区 {entry['absolute_sector']} 目标校验失败"
                )
        changed_bytes += sum(a != b for a, b in zip(raw, target_bytes))
        after_sectors.append(target_bytes)

    state = (
        "enabled" if sector_states and set(sector_states) == {"enabled"}
        else "disabled" if sector_states and set(sector_states) == {"disabled"}
        else "partial"
    )
    return dict(part=part["name"], state=state, sector_count=len(entries), changed_bytes=changed_bytes), after_sectors


def menu_ui_request(part: dict, nonce: bytes, before=None, after=None) -> bytes:
    return protocol_request(part["profile"], nonce, before, after)


def menu_ui_response(data: bytes, nonce: bytes, part: dict, *, writing: bool) -> dict:
    return protocol_response(data, nonce, 0 if writing else len(part["profile"].entries))


def load_custom_sound_capability_package() -> dict:
    data_root = files("a6700_unlock").joinpath("data")
    root = data_root.joinpath("custom-sound/capability")
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != CAPABILITY_PACKAGE_SCHEMA
            or manifest.get("package_id") != "custom_shutter_sound"
            or manifest.get("camera_io") is not False
            or manifest.get("installable") is not True
            or manifest.get("deployment") != "fl23-unified-carrier"
            or manifest.get("dependencies") != ["sound"]):
        raise CustomSoundCapabilityPackageError("自定义快门声能力包清单无效")

    fl23_manifest_bytes = data_root.joinpath("fl23/manifest.json").read_bytes()
    fl23_manifest = json.loads(fl23_manifest_bytes.decode("utf-8"))
    carrier = manifest.get("carrier")
    if (not isinstance(carrier, dict)
            or carrier.get("fl23_manifest_sha256") != _sha(fl23_manifest_bytes)
            or carrier.get("flags") != [4, 5, 6, 7]):
        raise CustomSoundCapabilityPackageError("自定义快门声载体清单不一致")
    stage_profile = data_root.joinpath("fl23/variants/4/runtime/custom-stage/custom-sound-stage-profile.h").read_bytes()
    if _sha(stage_profile) != carrier.get("stage_profile_sha256"):
        raise CustomSoundCapabilityPackageError("自定义快门声阶段配置文件与清单不一致")
    variants = carrier.get("variants")
    if not isinstance(variants, dict) or set(variants) != {"4", "5", "6", "7"}:
        raise CustomSoundCapabilityPackageError("自定义快门声载体组合不完整")
    for flag, row in variants.items():
        bin_bytes = data_root.joinpath(f"fl23/variants/{flag}/carrier.bin").read_bytes()
        runtime_bytes = data_root.joinpath(f"fl23/variants/{flag}/runtime/manifest.json").read_bytes()
        fl23_row = fl23_manifest.get("variants", {}).get(flag, {})
        if (_sha(bin_bytes) != row.get("sha256")
                or len(bin_bytes) != row.get("bytes")
                or _sha(runtime_bytes) != row.get("runtime_sha256")
                or row.get("sha256") != fl23_row.get("sha256")):
            raise CustomSoundCapabilityPackageError("自定义快门声载体资产校验失败")

    binding_manifest_bytes = data_root.joinpath("native/custom-sound-binding/manifest.json").read_bytes()
    binding_manifest = json.loads(binding_manifest_bytes.decode("utf-8"))
    probe_bytes = data_root.joinpath("native/custom-sound-binding/probe.elf").read_bytes()
    binding = manifest.get("binding")
    if (not isinstance(binding, dict)
            or binding.get("manifest_sha256") != _sha(binding_manifest_bytes)
            or binding.get("probe_sha256") != _sha(probe_bytes)
            or binding_manifest.get("files", {}).get("probe.elf") != _sha(probe_bytes)):
        raise CustomSoundCapabilityPackageError("自定义快门声加载器观察器校验失败")

    writer_manifest_bytes = data_root.joinpath("custom-sound/manifest.json").read_bytes()
    writer_manifest = json.loads(writer_manifest_bytes.decode("utf-8"))
    writer_bytes = data_root.joinpath("custom-sound/custom-sound-writer.elf").read_bytes()
    slot_writer = manifest.get("slot_writer")
    if (not isinstance(slot_writer, dict)
            or slot_writer.get("manifest_sha256") != _sha(writer_manifest_bytes)
            or slot_writer.get("binary_sha256") != _sha(writer_bytes)
            or writer_manifest.get("payloads", {}).get("custom-sound-writer", {}).get("sha256") != _sha(writer_bytes)):
        raise CustomSoundCapabilityPackageError("自定义快门声写槽载荷校验失败")

    menu_ui = manifest.get("menu_ui")
    if (not isinstance(menu_ui, dict)
            or menu_ui.get("sector_count") != 23
            or menu_ui.get("slot_count") != 10
            or menu_ui.get("default_label") != "默认"
            or menu_ui.get("row_address") != "0x3781000"
            or menu_ui.get("profile_sha256") !=
                json.loads(root.joinpath(MENU_UI_PATH).read_text(encoding="utf-8")).get("profile_sha256")):
        raise CustomSoundCapabilityPackageError("自定义快门声菜单UI清单无效")
    menu_ui_assets = load_custom_sound_menu_ui_assets()
    if (menu_ui_assets["manifest"]["before_sha256"] != menu_ui.get("before_sha256")
            or menu_ui_assets["manifest"]["after_sha256"] != menu_ui.get("after_sha256")
            or menu_ui_assets["manifest"]["profile_sha256"] != menu_ui.get("profile_sha256")
            or menu_ui_assets["manifest"]["sector_count"] != menu_ui.get("sector_count")):
        raise CustomSoundCapabilityPackageError("自定义快门声菜单UI清单与资产不一致")

    root_input = manifest.get("root_input")
    if (not isinstance(root_input, dict)
            or root_input.get("path") != ROOT_INPUT_PATH
            or root_input.get("logical_path") != ROOT_INPUT_LOGICAL_PATH):
        raise CustomSoundCapabilityPackageError("自定义快门声固定研究输入清单无效")
    root_input_data = root.joinpath(ROOT_INPUT_PATH).read_bytes()
    if (_sha(root_input_data) != root_input.get("sha256")
            or len(root_input_data) != root_input.get("bytes")):
        raise CustomSoundCapabilityPackageError("自定义快门声固定研究输入校验失败")

    return manifest
