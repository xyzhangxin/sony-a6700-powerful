"""Build a unified profile v2 from one or more sparse trial packages.

The release package ships the resulting data/device-profile.json.  This
maintainer tool documents and reproduces how the union window is derived:

- union every target/guard sector from the supplied trial and release profiles;
- use the newest trial preimages as the raw sample for unmanaged-byte hashes;
- zero every catalog-owned byte in target sectors before hashing;
- keep full hashes for guard sectors and verify them against every supplied
  profile that treats the sector as a guard;
- validate the result with DeviceProfile.from_dict.

Repeat ``--trial-profile``/``--trial-preimages`` to merge multiple features.
It never accesses a camera.  Paths are explicit so the release package does
not depend on the research tree at runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a6700_unlock.catalog import Catalog
from a6700_unlock.device_profile import DeviceProfile, KNOWN_DEVICES


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "src/a6700_unlock/data/a6700-2.00.json")
    parser.add_argument("--trial-profile", type=Path, action="append", required=True,
                        help="profile.json for a feature window; repeat for multiple packages")
    parser.add_argument("--trial-preimages", type=Path, action="append", required=True,
                        help="preimages.json captured before that trial write; same order")
    parser.add_argument("--release-profile", type=Path,
                        help="previous release profile, used for target mappings and coverage checks")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--hardware-validation", default="pending-unified-selection")
    parser.add_argument("--volatile", type=Path,
                        help="JSON map 'device:offset' -> byte offsets excluded from unmanaged hash")
    args = parser.parse_args()
    if len(args.trial_profile) != len(args.trial_preimages):
        raise SystemExit("each --trial-profile requires one --trial-preimages")

    catalog_data = load_json(args.catalog)
    catalog = Catalog.from_dict(catalog_data)
    release = load_json(args.release_profile) if args.release_profile else None
    volatile_map = load_json(args.volatile) if args.volatile else {}

    profiles = [load_json(path) for path in args.trial_profile]
    preimages = [load_json(path) for path in args.trial_preimages]
    release_entries = ({(row["device"], row["device_offset"]): row
                        for row in release["entries"]} if release else {})

    keys = set(release_entries)
    trial_entries = []
    raw_by_key = {}
    guard_hashes = {}
    target_mapping = {}
    release_edit_order = {}
    for index, (profile, preimage) in enumerate(zip(profiles, preimages)):
        entries = {(row["device"], row["device_offset"]): row for row in profile["entries"]}
        if len(entries) != len(profile["entries"]):
            raise SystemExit("duplicate sector in trial profile %d" % index)
        if len(preimage["sectors"]) != len(profile["entries"]):
            raise SystemExit("preimage count does not match trial profile %d" % index)
        keys.update(entries)
        trial_entries.append(entries)
        for row, hex_sector in zip(profile["entries"], preimage["sectors"]):
            raw = bytes.fromhex(hex_sector)
            if len(raw) != 512:
                raise SystemExit("trial preimage contains a non-512-byte sector")
            key = (row["device"], row["device_offset"])
            raw_by_key[key] = raw
            if row["role"] == "guard":
                if key in guard_hashes and guard_hashes[key] != row["sha256"]:
                    raise SystemExit("guard hash differs between trials at %r" % (key,))
                guard_hashes[key] = row["sha256"]
            else:
                target_mapping.setdefault(key, (row["image"], row["file_offset"]))
    if release_entries:
        keys.update(release_entries)
        for key, row in release_entries.items():
            if row.get("role") == "guard":
                if key in guard_hashes and guard_hashes[key] != row["sha256"]:
                    raise SystemExit("release/trial guard hash differs at %r" % (key,))
                guard_hashes[key] = row["sha256"]
            elif row.get("role") == "target":
                mapping = (row["image"], row["file_offset"])
                if key in target_mapping and target_mapping[key] != mapping:
                    raise SystemExit("target mapping differs at %r" % (key,))
                target_mapping[key] = mapping
                if row.get("edit_ids"):
                    release_edit_order[key] = list(row["edit_ids"])
    for entries in trial_entries:
        for key, row in entries.items():
            if row.get("role") == "target":
                mapping = (row["image"], row["file_offset"])
                if key in target_mapping and target_mapping[key] != mapping:
                    raise SystemExit("target mapping differs at %r" % (key,))
                target_mapping[key] = mapping

    keys = sorted(keys)
    edits_by_key = {key: [] for key in keys}
    for edit in catalog.edits:
        if edit.image == "wbi":
            key = (1, edit.offset // 512 * 512)
        elif edit.image == "cpapp":
            key = (2, edit.offset // 512 * 512)
        else:
            candidates = [key for key, (image, offset) in target_mapping.items()
                          if image == "app" and offset <= edit.offset < offset + 512]
            if len(candidates) != 1:
                raise SystemExit("cannot map app edit %s to exactly one target sector" % edit.id)
            key = candidates[0]
        if key not in edits_by_key:
            raise SystemExit("edit %s maps outside the supplied windows" % edit.id)
        edits_by_key[key].append(edit)

    entries = []
    for index, key in enumerate(keys):
        if key not in raw_by_key:
            raise SystemExit("no raw preimage for sector %r" % (key,))
        sector = raw_by_key[key]
        if key in target_mapping:
            image, file_offset = target_mapping[key]
            masked = bytearray(sector)
            occupied = set()
            for edit in edits_by_key[key]:
                start = edit.offset - file_offset
                if start < 0 or start + len(edit.disabled) > 512:
                    raise SystemExit("edit %s falls outside its target sector" % edit.id)
                masked[start:start + len(edit.disabled)] = b"\0" * len(edit.disabled)
                occupied.update(range(start, start + len(edit.disabled)))
            volatile = volatile_map.get("%d:%d" % key, [])
            if not isinstance(volatile, list) or any(type(v) is not int or not 0 <= v < 512 for v in volatile):
                raise SystemExit("invalid volatile offsets at %r" % (key,))
            if set(volatile) & occupied:
                raise SystemExit("volatile offset overlaps a catalog field at %r" % (key,))
            for at in volatile:
                masked[at] = 0
            by_id = {edit.id: edit for edit in edits_by_key[key]}
            ordered_ids = []
            for ident in release_edit_order.get(key, []):
                if ident in by_id and ident not in ordered_ids:
                    ordered_ids.append(ident)
            for edit in edits_by_key[key]:
                if edit.id not in ordered_ids:
                    ordered_ids.append(edit.id)
            if not ordered_ids:
                raise SystemExit("target sector has no catalog field at %r" % (key,))
            row = dict(index=index, role="target", image=image,
                       file_offset=file_offset, device=key[0],
                       device_offset=key[1],
                       unmanaged_sha256=hashlib.sha256(masked).hexdigest(),
                       edit_ids=ordered_ids)
            if volatile:
                row["volatile_offsets"] = sorted(volatile)
            entries.append(row)
        else:
            if edits_by_key[key]:
                raise SystemExit("guard sector unexpectedly owns catalog fields at %r" % (key,))
            digest = hashlib.sha256(sector).hexdigest()
            if key in guard_hashes and guard_hashes[key] != digest:
                raise SystemExit("preimage guard hash mismatch at %r" % (key,))
            entries.append(dict(index=index, role="guard", device=key[0],
                                device_offset=key[1], sha256=digest))

    if len(entries) > 512:
        raise SystemExit("unified window exceeds the native profile limit")
    profile = dict(schema_version=2, profile_id=args.profile_id,
                   catalog_sha256=catalog.digest, sector_size=512,
                   devices=list(KNOWN_DEVICES[:max(key[0] for key in keys) + 1]),
                   hardware_validation=args.hardware_validation, entries=entries)
    validated = DeviceProfile.from_dict(profile, catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(dict(output=str(args.output), profile_id=args.profile_id,
                          profile_sha256=validated.digest,
                          catalog_sha256=catalog.digest, entries=len(entries),
                          targets=sum(row["role"] == "target" for row in entries),
                          guards=sum(row["role"] == "guard" for row in entries)),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
