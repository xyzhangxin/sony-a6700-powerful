"""Summarize existing local app records. No camera, network or source writes.

Export only allowlisted installation metadata, never raw replies, preimages,
service files, identities, absolute paths or exception messages. This is not
a camera crash dump and cannot identify the failing instruction.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import re
import stat

FEATURES = frozenset(("fl23", "creative_look_names", "custom_shutter_sound", "ftp", "shutter",
                      "exposure", "sound", "pixel_shift", "dynamic_active"))
STATES = frozenset(("enabled", "disabled", "partial", "error", "unknown", "original"))
LIMIT = 2 * 1024 * 1024
_RECOVERY_MODES = frozenset(("power-cycle-wait", "close-custom-sound", "update-carrier"))
_DIAG_BOOLS = (
    "boot_socket", "same_boot_session", "receipt_present", "receipt_matched",
    "wrapper_present", "autoload_present", "autoload_pending_retained",
)
_DIAG_SMALL_INTS = ("boot_page_matches", "fl23_worker_state", "wrapper_primary_ran", "wrapper_custom_started")
_DIAG_SIGNED_INTS = (
    "fl23_worker_error", "wrapper_primary_rc", "wrapper_custom_rc",
    "wrapper_early_abort_rc", "autoload_step", "autoload_status",
)


def read_record(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError("Not a bounded regular record")
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Record too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Not a record object")
    return data


def feature_rows(rows):
    if not isinstance(rows, list):
        return []
    output = []
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in FEATURES or row.get("state") not in STATES:
            continue
        item = {"id": row["id"], "state": row["state"]}
        for key in ("installed_revision", "target_revision"):
            if type(row.get(key)) is int and 0 <= row[key] <= 255:
                item[key] = row[key]
        if type(row.get("update_available")) is bool:
            item["update_available"] = row["update_available"]
        if row.get("update_state") in ("available", "current", "uninstalled", "unknown"):
            item["update_state"] = row["update_state"]
        output.append(item)
    return output


def custom_sound_runtime_summary(runtime):
    """Return strictly allowlisted scalar telemetry for custom-sound probe checks."""
    if not isinstance(runtime, dict):
        return None
    summary = {}
    if runtime.get("status") in ("verified", "error", "unknown"):
        summary["status"] = runtime["status"]
    native_status = runtime.get("native_status")
    if type(native_status) is int and 0 <= native_status <= 255:
        summary["native_status"] = native_status
    diag = runtime.get("diagnostic")
    if isinstance(diag, dict):
        safe_diag = {}
        category = diag.get("category")
        if isinstance(category, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,47}", category):
            safe_diag["category"] = category
        recovery_mode = diag.get("recovery_mode")
        if recovery_mode in _RECOVERY_MODES:
            safe_diag["recovery_mode"] = recovery_mode
        for key in _DIAG_BOOLS:
            if type(diag.get(key)) is bool:
                safe_diag[key] = diag[key]
        for key in _DIAG_SMALL_INTS:
            val = diag.get(key)
            if type(val) is int and 0 <= val <= 1000:
                safe_diag[key] = val
        for key in _DIAG_SIGNED_INTS:
            val = diag.get(key)
            if type(val) is int and -100000 <= val <= 100000:
                safe_diag[key] = val
        if safe_diag:
            summary["diagnostic"] = safe_diag
    return summary or None


def collect(state_dir):
    runs = state_dir / "runs"
    if state_dir.is_symlink() or runs.is_symlink():
        raise ValueError("Symlink record directory refused")
    if not runs.is_dir():
        raise ValueError("Application record directory not found")
    candidates = []
    for path in runs.iterdir():
        if (path.is_symlink() or not path.is_dir()
                or not re.fullmatch(r"(?:looks|fl23)-[0-9a-f]{32}", path.name)):
            continue
        result = path / "result.json"
        if result.is_symlink():
            continue
        if result.is_file():
            candidates.append((result.stat().st_mtime, path))
    output = []
    unreadable = 0
    for modified, folder in sorted(candidates, reverse=True)[:8]:
        try:
            result = read_record(folder / "result.json")
            item = {"record_id": folder.name,
                    "record_modified_utc": datetime.fromtimestamp(
                        modified, timezone.utc).isoformat()}
            if result.get("action") in ("inspect", "unlock", "close"):
                item["action"] = result["action"]
            if isinstance(result.get("status"), str) and re.fullmatch(
                    r"[a-z][a-z-]{0,63}", result["status"]):
                item["status"] = result["status"]
            for key in ("verified", "device_access", "write_attempted",
                        "normal_msc_observed", "service_restore_verified",
                        "package_updated", "restart_required"):
                if type(result.get(key)) is bool:
                    item[key] = result[key]
            profile = result.get("profile_sha256")
            if isinstance(profile, str) and re.fullmatch(r"[0-9a-f]{64}", profile):
                item["profile_sha256"] = profile
            selected = result.get("selected_features", [])
            if isinstance(selected, list):
                item["selected_features"] = [x for x in selected
                                             if isinstance(x, str) and x in FEATURES]
            item["final_features"] = feature_rows(result.get("final_features"))
            plan = result.get("plan")
            if isinstance(plan, dict):
                item["observed_features"] = feature_rows(plan.get("features"))
            intent = folder / "write-intent.json"
            if intent.is_file() and not intent.is_symlink():
                data = read_record(intent)
                for key in ("before_flags", "after_flags"):
                    if type(data.get(key)) is int and 0 <= data[key] <= 3:
                        item[key] = data[key]
                for key in ("installed_revision", "target_revision"):
                    if type(data.get(key)) is int and 0 <= data[key] <= 255:
                        item["intent_" + key] = data[key]
                if type(data.get("package_update")) is bool:
                    item["intent_package_update"] = data["package_update"]
                digest = data.get("carrier_sha256")
                if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
                    item["carrier_sha256"] = digest
            inspect_file = folder / "fl23-inspect-result.json"
            if inspect_file.is_file() and not inspect_file.is_symlink():
                inspect_data = read_record(inspect_file)
                runtime = custom_sound_runtime_summary(inspect_data.get("custom_sound_runtime"))
                if runtime is not None:
                    item["custom_sound_runtime"] = runtime
                inspect_plan = inspect_data.get("plan")
                if isinstance(inspect_plan, dict):
                    fl23_rows = feature_rows(inspect_plan.get("features"))
                    if fl23_rows:
                        item["fl23_features"] = fl23_rows
            output.append(item)
        except (OSError, ValueError, TypeError, KeyError):
            unreadable += 1
    return {"schema": 1, "kind": "fl23-local-install-summary",
            "camera_access": False, "network_access": False,
            "source_writes": False, "crash_log_collected": False,
            "records": output, "unreadable_records": unreadable}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path,
                        default=Path.home() / "Library/Application Support/a6700-unlock")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app-bundle", type=Path)
    args = parser.parse_args()
    result = collect(args.state_dir)
    if args.app_bundle:
        info = args.app_bundle / "Contents/Info.plist"
        if not info.is_symlink() and info.is_file() and info.stat().st_size <= LIMIT:
            with info.open("rb") as stream:
                metadata = plistlib.load(stream)
            for key in ("A6700DevelopmentVersion", "CFBundleShortVersionString"):
                version = metadata.get(key)
                if isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}(?:\.dev[0-9]+)?", version):
                    result["collector_app_version"] = version
                    break
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print("已生成安装摘要：" + str(args.output))
    print("未连接相机；不包含固件、个人身份或机身崩溃日志。")


if __name__ == "__main__":
    main()
