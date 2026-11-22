"""Derive exact FTP playback guard variants from an explicit original App file.

This offline maintainer command reads regular files and never opens a camera.
The six patch definitions were verified statically in SonyCameraTool 1.0.0.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a6700_unlock.catalog import load_catalog
from a6700_unlock.device_profile import load_device_profile


PATCHES = (
    (67, 0x04BCA360, "0000", "0100", "playback-filter-container"),
    (90, 0x04C0B512, "0000", "0100", "ftp-transfer-status-row"),
    (133, 0x04C36A6A, "0000", "0100", "non-transferred-leaf"),
    (133, 0x04C36B0A, "0000", "0100", "failed-transfer-leaf"),
    (133, 0x04C36BAA, "0000", "0100", "transferred-leaf"),
    (133, 0x04C36B8C, "ce800002", "77600002", "transferred-leaf-title"),
)
FILE_SECTORS = {67: 0x04BCA200, 90: 0x04C0B400, 133: 0x04C36A00}


def build(app, profile):
    if not app.is_file() or app.is_symlink():
        raise ValueError("The original App must be a regular local file")
    original = app.read_bytes()
    if hashlib.sha256(original).hexdigest() != "e65fd595052be5d0e98ce8000dfcb94f90a2f8309de70ef140375052009b0ef3":
        raise ValueError("Original A6700 2.00 App digest differs")
    records = {}
    for index, start in FILE_SECTORS.items():
        row = profile.entries[index]
        sector = original[start:start + 512]
        if row["role"] != "guard" or hashlib.sha256(sector).hexdigest() != row["sha256"]:
            raise ValueError("Original App/profile guard mapping differs")
        patches = [p for p in PATCHES if p[0] == index]
        for _, offset, old, new, _ in patches:
            if original[offset:offset + len(bytes.fromhex(old))] != bytes.fromhex(old):
                raise ValueError("Original FTP patch bytes differ")
            if not start <= offset < offset + len(bytes.fromhex(new)) <= start + 512:
                raise ValueError("FTP patch crosses the pinned guard sector")
        variants = []
        for flags in itertools.product((False, True), repeat=len(patches)):
            if not any(flags):
                continue
            changed = bytearray(sector)
            selected = []
            for enabled, (_, offset, old, new, name) in zip(flags, patches):
                if enabled:
                    value = bytes.fromhex(new)
                    changed[offset - start:offset - start + len(value)] = value
                    selected.append(name)
            variants.append(dict(sha256=hashlib.sha256(changed).hexdigest(), patches=selected))
        records[str(index)] = dict(device=row["device"], device_offset=row["device_offset"],
                                  original_sha256=row["sha256"], file_offset=start, variants=variants)
    return dict(schema=1, kind="guard-compatibility-overlay", profile_sha256=profile.digest,
                source=dict(name="SonyCameraTool 1.0.0 FTP playback filter",
                            url="https://github.com/mingwucn/SonyCameraTool_Download",
                            archive_sha256="6c02eb670a95f6fcf7377b3ae863ef3466a6289ffd4df28f336108368addf127",
                            targets_pyc_sha256="f7b2dc14570e6c7dc9e027a925ac04e83035c1734db2c074d6846fbb74d3f7e8"),
                patches=[dict(index=i, file_offset=o, original=a, patched=b, id=n)
                         for i, o, a, b, n in PATCHES], records=records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.app, load_device_profile(load_catalog()))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
