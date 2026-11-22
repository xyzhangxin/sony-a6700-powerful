#!/usr/bin/env python3
"""Build the unified custom-shutter-sound capability package manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
DATA = ROOT / "src/a6700_unlock/data"
DESTINATION = DATA / "custom-sound/capability"
MENU_UI = DESTINATION / "menu-ui"
RESEARCH_MENU_UI = PROJECT_ROOT / "output/运行/FL2-FL3/统一启动底座/unified-menu-ui"
ROOT_INPUT = DESTINATION / "root-input/a6700-2.00.json"
ROOT_INPUT_LOGICAL_PATH = "release/src/a6700_unlock/data/a6700-2.00.json"
ROOT_INPUT_SHA256 = "d6b5eddabe1f05bd5f3069563dde6b9a4eee6800006d162fccfc8fec9951d698"
RECOVERY_PLAN = PROJECT_ROOT / "output/运行/快门声音/十槽固定编号/static-menu/recovery-coverage/chunk-01/plan.json"
HOST_ELF_SHA256 = "799c133ff8fdf5ff0f095fcdec92f6c0ea03c68e0112a8288836ac244d7a2d0a"
HOST_PACKAGE_SHA256 = "222953c7b0d286ca7726912c638da75dcd49a2b3d197bef2c586cd9f3ac65efe"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    root_input_data = ROOT_INPUT.read_bytes()
    if sha(root_input_data) != ROOT_INPUT_SHA256:
        raise ValueError("Custom-sound fixed root input differs from the reviewed pin")

    fl23_manifest_bytes = (DATA / "fl23/manifest.json").read_bytes()
    fl23_manifest = json.loads(fl23_manifest_bytes.decode("utf-8"))
    variants = fl23_manifest.get("variants", {})
    if not {"4", "5", "6", "7"} <= set(variants):
        raise ValueError("Unified /setting/fl23 manifest is missing custom_shutter_sound variants 4..7")

    variant_assets = {}
    for flag in ("4", "5", "6", "7"):
        row = variants[flag]
        carrier_bytes = (DATA / f"fl23/variants/{flag}/carrier.bin").read_bytes()
        runtime_bytes = (DATA / f"fl23/variants/{flag}/runtime/manifest.json").read_bytes()
        if (sha(carrier_bytes) != row["sha256"]
                or len(carrier_bytes) != row["bytes"]
                or sha(runtime_bytes) != row["runtime_sha256"]):
            raise ValueError(f"Unified /setting/fl23 variant {flag} differs from manifest")
        variant_assets[flag] = {
            "sha256": row["sha256"],
            "bytes": row["bytes"],
            "runtime_sha256": row["runtime_sha256"],
        }

    stage_profile = DATA / "fl23/variants/4/runtime/custom-stage/custom-sound-stage-profile.h"
    stage_profile_bytes = stage_profile.read_bytes()
    runtime4 = json.loads((DATA / "fl23/variants/4/runtime/manifest.json").read_text(encoding="utf-8"))
    if runtime4["files"].get("variants/4/runtime/custom-stage/custom-sound-stage-profile.h") != sha(stage_profile_bytes):
        raise ValueError("Unified custom-stage profile differs from variant 4 runtime manifest")

    binding_manifest_path = DATA / "native/custom-sound-binding/manifest.json"
    binding_manifest_bytes = binding_manifest_path.read_bytes()
    binding_manifest = json.loads(binding_manifest_bytes.decode("utf-8"))
    probe_bytes = (DATA / "native/custom-sound-binding/probe.elf").read_bytes()
    if (binding_manifest.get("host_sha256") != HOST_ELF_SHA256
            or sha(probe_bytes) != binding_manifest["files"]["probe.elf"]
            or set(binding_manifest.get("receipts", {})) != {"4", "5", "6", "7"}):
        raise ValueError("Custom-sound binding observer manifest is incomplete")

    writer_manifest_path = DATA / "custom-sound/manifest.json"
    writer_manifest_bytes = writer_manifest_path.read_bytes()
    writer_manifest = json.loads(writer_manifest_bytes.decode("utf-8"))
    writer_bytes = (DATA / "custom-sound/custom-sound-writer.elf").read_bytes()
    writer_row = writer_manifest["payloads"]["custom-sound-writer"]
    if (sha(writer_bytes) != writer_row["sha256"]
            or len(writer_bytes) != writer_row["bytes"]
            or writer_manifest["syscalls"].get("getdents64") != 61):
        raise ValueError("Custom-sound slot writer payload is missing directory wipe support")

    menu_ui_manifest = json.loads((MENU_UI / "manifest.json").read_text(encoding="utf-8"))
    research_menu_manifest = json.loads((RESEARCH_MENU_UI / "manifest.json").read_text(encoding="utf-8"))
    recovery_plan = json.loads(RECOVERY_PLAN.read_text(encoding="utf-8"))
    if (menu_ui_manifest.get("camera_access") is not False
            or menu_ui_manifest.get("installable") is not True
            or menu_ui_manifest.get("sector_count") != 23
            or menu_ui_manifest.get("row_address") != "0x3781000"
            or menu_ui_manifest.get("default_label") != "默认"
            or menu_ui_manifest.get("slot_count") != 10
            or menu_ui_manifest.get("profile_sha256") != research_menu_manifest.get("profile_sha256")
            or menu_ui_manifest.get("before_sha256") != research_menu_manifest.get("before_sha256")
            or menu_ui_manifest.get("after_sha256") != research_menu_manifest.get("after_sha256")
            or menu_ui_manifest.get("recovery_coverage_plan_id") != recovery_plan.get("plan_id")):
        raise ValueError("Unified sparse menu-ui manifest differs from verified baseline")

    part_sectors = set()
    for part in menu_ui_manifest["parts"]:
        part_dir = MENU_UI / part["name"]
        part_manifest = json.loads((part_dir / "manifest.json").read_text(encoding="utf-8"))
        if any(path.suffix == ".bin" for path in part_dir.iterdir() if path.is_file()):
            raise ValueError(f"Raw .bin firmware dump must not exist in release menu-ui part {part['name']}")
        for filename, digest in part_manifest["files"].items():
            if sha((part_dir / filename).read_bytes()) != digest:
                raise ValueError(f"Menu UI file mismatch: {part['name']}/{filename}")
        for entry in part_manifest["entries"]:
            part_sectors.add(entry["absolute_sector"])
    if part_sectors != {row["absolute_sector"] for row in recovery_plan["sectors"]}:
        raise ValueError("Menu UI sectors do not match recovery coverage plan")

    manifest = {
        "schema": "a6700.custom-sound-capability-package/3",
        "package_id": "custom_shutter_sound",
        "version": 2,
        "hardware_validation": "pending",
        "camera_io": False,
        "installable": True,
        "deployment": "fl23-unified-carrier",
        "dependencies": ["sound"],
        "root_input": {
            "path": ROOT_INPUT.relative_to(DESTINATION).as_posix(),
            "logical_path": ROOT_INPUT_LOGICAL_PATH,
            "sha256": sha(root_input_data),
            "bytes": len(root_input_data),
            "source": "Powerful6700 0.4.2 frozen source bundle",
            "purpose": "Fixed FL23 root-transaction catalog pin for custom_shutter_sound",
        },
        "carrier": {
            "deployment": "fl23-unified-carrier",
            "fl23_manifest_sha256": sha(fl23_manifest_bytes),
            "carrier_revision": fl23_manifest["carrier_revision"],
            "flags": [4, 5, 6, 7],
            "stage_profile_path": "data/fl23/variants/4/runtime/custom-stage/custom-sound-stage-profile.h",
            "stage_profile_sha256": sha(stage_profile_bytes),
            "host_elf_sha256": HOST_ELF_SHA256,
            "host_package_sha256": HOST_PACKAGE_SHA256,
            "variants": variant_assets,
        },
        "binding": {
            "manifest_sha256": sha(binding_manifest_bytes),
            "probe_sha256": binding_manifest["files"]["probe.elf"],
            "loader_version": binding_manifest["loader_version"],
            "receipts": binding_manifest["receipts"],
            "legacy_receipts": binding_manifest.get("legacy_receipts", {}),
        },
        "slot_writer": {
            "manifest_sha256": sha(writer_manifest_bytes),
            "binary_sha256": writer_row["sha256"],
            "bytes": writer_row["bytes"],
            "wipe_version": 4,
            "clear_pending_version": 5,
        },
        "menu_ui": {
            "status": menu_ui_manifest["status"],
            "layout": menu_ui_manifest["layout"],
            "sector_count": menu_ui_manifest["sector_count"],
            "before_sha256": menu_ui_manifest["before_sha256"],
            "after_sha256": menu_ui_manifest["after_sha256"],
            "profile_sha256": menu_ui_manifest["profile_sha256"],
            "row_address": menu_ui_manifest["row_address"],
            "default_label": menu_ui_manifest["default_label"],
            "slot_count": menu_ui_manifest["slot_count"],
            "recovery_coverage_plan_id": menu_ui_manifest["recovery_coverage_plan_id"],
            "parts": [part["name"] for part in menu_ui_manifest["parts"]],
            "files": {
                f"{part['name']}/manifest.json": sha((MENU_UI / part["name"] / "manifest.json").read_bytes())
                for part in menu_ui_manifest["parts"]
            },
        },
        "validation": {
            "full_chain_status": "unified-fl23-carrier-ten-slot-menu-ready",
            "recovery_coverage_plan_id": recovery_plan["plan_id"],
            "recovery_sectors": len(recovery_plan["sectors"]),
        },
    }
    (DESTINATION / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": "built",
        "package": str(DESTINATION.relative_to(PROJECT_ROOT)),
        "installable": True,
        "camera_io": False,
        "sector_count": menu_ui_manifest["sector_count"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
