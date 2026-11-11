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

FEATURES = frozenset(("fl23", "creative_look_names", "ftp", "shutter",
                      "exposure", "sound", "pixel_shift", "dynamic_active"))
STATES = frozenset(("enabled", "disabled", "partial", "unknown", "original"))
LIMIT = 2 * 1024 * 1024


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
