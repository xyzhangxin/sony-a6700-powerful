"""Build an exact, offline FTP migration adapter; never open a camera."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from a6700_unlock.catalog import Catalog, load_catalog
from a6700_unlock.device_profile import DeviceProfile, load_device_profile
from build_guard_compatibility import PATCHES, FILE_SECTORS

RESOURCE_NAMES = (
    "catalog.json", "profile.json", "generic-catalog.json", "generic-profile.json",
    "generic-guard-volatile.json", "native/manifest.json", "native/profile.h",
    "native/transaction.c", "native/reader.elf", "native/writer.elf",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def build(app_path, matrix_dir, output):
    generic = load_device_profile(load_catalog())
    if generic.digest != "c2860f5a1687d0190ac74d9500b971fa8301ff12784c7c567cee0d5c20a056fb":
        raise ValueError("Migration requires the verified public 0.4.2 adapter")
    if not app_path.is_file() or app_path.is_symlink():
        raise ValueError("Original App must be an explicit regular local file")
    app = app_path.read_bytes()
    if sha(app) != "e65fd595052be5d0e98ce8000dfcb94f90a2f8309de70ef140375052009b0ef3":
        raise ValueError("Original A6700 2.00 App identity differs")
    data = json.loads((matrix_dir / "catalog.json").read_bytes())
    matrix = Catalog.from_dict(data)
    profile_data = json.loads((matrix_dir / "profile.json").read_bytes())
    old_profile = DeviceProfile.from_dict(profile_data, matrix)
    if (matrix.digest != "3c17f21e3eefad0bcb67882dd99d59d133834ebab743660188d48ba35cb4ba25"
            or old_profile.digest != "3db0eca3dd582012f380fe95d22b239393a0e3d3d5c8e66e8eabee25ea3cc559"):
        raise ValueError("Reviewed 134-byte matrix candidate differs")
    data = deepcopy(data)
    for feature in data["features"]:
        if feature["id"] not in ("ftp", "ftp-custom-key-matrix"):
            feature.update(available=False, unavailable_reason="迁移工具只修复 FTP；其他功能保持原状")
    data["features"].append(dict(id="ftp-third-party-repair", name="恢复通用版 FTP 守卫", available=True,
        verification="offline-migration-tested", limits=["反馈机身重启及通用版连接仍需验收"]))
    repair_ids = []
    for index, offset, original, patched, ident in PATCHES:
        original, patched = bytes.fromhex(original), bytes.fromhex(patched)
        if app[offset:offset + len(original)] != original:
            raise ValueError("Original patch field differs")
        differing = [i for i, (a, b) in enumerate(zip(original, patched)) if a != b]
        start, end = min(differing), max(differing) + 1
        original, patched = original[start:end], patched[start:end]
        edit_id = "ftp-repair-" + ident
        repair_ids.append(edit_id)
        data["edits"].append(dict(id=edit_id, feature="ftp-third-party-repair", image="app",
            offset=offset + start, disabled=patched.hex(), enabled=original.hex(),
            known_values=[patched.hex(), original.hex()], purpose="撤回已知第三方字段：" + ident))
    data.update(catalog_id="a6700-2.00-ftp-migrate-to-public-042", bundle_version="0.4.2.ftp-repair1")
    catalog = Catalog.from_dict(data)
    profile_data = deepcopy(profile_data)
    for generic_index, file_start in FILE_SECTORS.items():
        old = generic.entries[generic_index]
        sector = app[file_start:file_start + 512]
        if sha(sector) != old["sha256"]:
            raise ValueError("Original generic guard differs")
        row = next(r for r in profile_data["entries"]
                   if (r["device"], r["device_offset"]) == (old["device"], old["device_offset"]))
        if row.get("role") != "guard" or row["sha256"] != old["sha256"]:
            raise ValueError("Matrix/generic guard mapping differs")
        fields = [e for e in catalog.edits if e.id in repair_ids and file_start <= e.offset < file_start + 512]
        normalized = bytearray(sector)
        for edit in fields:
            at = edit.offset - file_start
            normalized[at:at + len(edit.disabled)] = bytes(len(edit.disabled))
        row.pop("sha256")
        row.update(role="target", image="app", file_offset=file_start,
                   edit_ids=[e.id for e in fields], unmanaged_sha256=sha(normalized))
    overlay = json.loads((matrix_dir / "guard-volatile.json").read_bytes())
    if overlay["profile_sha256"] != old_profile.digest:
        raise ValueError("Matrix inode overlay differs")
    overlay = {k: deepcopy(overlay[k]) for k in ("schema", "kind", "inode_size", "volatile_fields", "records")}
    profile_data.update(profile_id="a6700-2.00-ftp-migrate-to-public-042",
        catalog_sha256=catalog.digest, guard_volatile_overlay=overlay,
        hardware_validation="pending-feedback-camera-migration")
    profile = DeviceProfile.from_dict(profile_data, catalog)
    # The matrix must stay outside the complete public inspection window.
    generic_keys = {(r["device"], r["device_offset"]) for r in generic.entries}
    matrix_ids = {e.id for e in matrix.edits if e.feature == "ftp-custom-key-matrix"}
    for row in profile.entries:
        if matrix_ids.intersection(row.get("edit_ids", [])):
            if (row["device"], row["device_offset"]) in generic_keys:
                raise ValueError("Matrix overlaps the public inspection window")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "catalog.json", catalog.data)
    write(output / "profile.json", profile.data)
    write(output / "generic-catalog.json", generic.catalog.data)
    generic_data = deepcopy(generic.data)
    # Copy the existing public inode-time normalization separately to preserve
    # the public profile identity used by the strict acceptance check.
    write(output / "generic-profile.json", generic_data)
    write(output / "generic-guard-volatile.json", json.loads(
        (ROOT / "src/a6700_unlock/data/guard-volatile.json").read_bytes()))
    subprocess.run([sys.executable, str(ROOT / "scripts/build_native.py"),
        "--catalog", str(output / "catalog.json"), "--profile", str(output / "profile.json"),
        "--output-dir", str(output / "native")], check=True)
    manifest = dict(schema=1, kind="ftp-migration-to-public-042", profile_sha256=profile.digest,
        catalog_sha256=catalog.digest, generic_profile_sha256=generic.digest,
        selected_features=["ftp", "ftp-custom-key-matrix", "ftp-third-party-repair"],
        repair_bytes=7, matrix_bytes=134, files={})
    for name in sorted(RESOURCE_NAMES):
        manifest["files"][name] = sha((output / name).read_bytes())
    write(output / "manifest.json", manifest)
    print(json.dumps(dict(bundle_manifest_sha256=sha((output / "manifest.json").read_bytes()),
                          profile_sha256=profile.digest, entries=len(profile.entries))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--matrix-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.app, args.matrix_dir, args.output)
