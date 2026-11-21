"""Pinned, sparse physical layout for supported patch targets and guards."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json

from .catalog import Catalog, CatalogError
from .planner import _combined_state

# Supported camera-side block devices, in profile order. Shipped profiles use
# the first two; the optional third device targets the CP firmware partition.
KNOWN_DEVICES = ("/dev/nflasha15", "/dev/nflasha5", "/dev/nflasha8")
DEVICE_SECTORS = {0: 0x165800, 1: 0x96000, 2: 48844}
IMAGE_DEVICES = {"app": 0, "wbi": 1, "cpapp": 2}
PLAIN_IMAGES = {"wbi", "cpapp"}

# Host diagnostic only. Bind the comparison to the known ext2 primary
# superblock and its exact original digest, independent of profile numbering.
_EXT2_SUPERBLOCK_SHA256 = "264930c556e6d4308bcc6e7b4dc6156e9e867fd7c02d056950c03b0c625207ad"
_EXT2_WITHOUT_RUNTIME_SHA256 = "a4c466b8180ca921b44dba2a7499a079a18b3b116f1d772ddb8d94fd9864bb10"
_EXT2_RUNTIME_FIELDS = ((12, 20), (44, 54), (58, 60), (64, 68), (232, 236))


def _superblock_difference(row, sector):
    if (row["device"], row["device_offset"], row.get("sha256")) != (0, 1024, _EXT2_SUPERBLOCK_SHA256):
        return None
    if sector[56:58] != b"\x53\xef":
        return "magic_mismatch"
    # Exclude space/inode counters, mount/write/check times, mount counts,
    # filesystem state and last orphan. Keep every other byte in the comparison.
    normalized = bytearray(sector)
    for start, end in _EXT2_RUNTIME_FIELDS:
        normalized[start:end] = bytes(end - start)
    if hashlib.sha256(normalized).hexdigest() == _EXT2_WITHOUT_RUNTIME_SHA256:
        return "runtime_fields_only"
    return "other_fields_changed"


@dataclass(frozen=True)
class DeviceProfile:
    data: dict
    digest: str
    catalog: Catalog

    @classmethod
    def from_dict(cls, data, catalog):
        schema = data.get("schema_version")
        devices = data.get("devices")
        if (schema not in (1, 2) or data.get("sector_size") != 512
                or not isinstance(devices, list) or not 1 <= len(devices) <= len(KNOWN_DEVICES)
                or list(devices) != list(KNOWN_DEVICES[:len(devices)])
                or data.get("catalog_sha256") != catalog.digest):
            raise CatalogError("Unsupported or mismatched device profile")
        entries = data.get("entries", [])
        if not entries or len(entries) > 512:
            raise CatalogError("Invalid sparse target/guard count")
        keys, covered = set(), {}
        known = {e.id: e for e in catalog.edits}
        for index, row in enumerate(entries):
            if (row.get("index") != index or type(row.get("device")) is not int
                    or not 0 <= row["device"] < len(devices) or type(row.get("device_offset")) is not int
                    or row["device_offset"] < 0 or row["device_offset"] % 512):
                raise CatalogError("Invalid device target address/index")
            limit = DEVICE_SECTORS[row["device"]] * 512
            if row["device_offset"] + 512 > limit:
                raise CatalogError("Target outside supported partition")
            key = row["device"], row["device_offset"]
            if key in keys:
                raise CatalogError("Duplicate device sector")
            keys.add(key)
            if row.get("role") == "target":
                image, off = row.get("image"), row.get("file_offset")
                if (image not in catalog.data["images"] or type(off) is not int or off < 0 or off % 512
                        or off + 512 > catalog.data["images"][image]["size"]
                        or IMAGE_DEVICES.get(image) != row["device"]):
                    raise CatalogError("Invalid logical-to-physical target mapping")
                if image in PLAIN_IMAGES and off != row["device_offset"]:
                    raise CatalogError("Plain-image offset mapping mismatch")
                if not row.get("edit_ids"):
                    raise CatalogError("Target must be owned by catalog edits")
                for ident in row["edit_ids"]:
                    e = known.get(ident)
                    if not e or e.image != image or not (e.offset < off + 512 and e.end > off):
                        raise CatalogError("Invalid edit-to-sector reference")
                    covered.setdefault(ident, set()).add(off)
                digest = row.get("unmanaged_sha256" if schema == 2 else "normalized_sha256", "")
                if schema == 2:
                    volatile = row.get("volatile_offsets", [])
                    if (not isinstance(volatile, list)
                            or any(type(value) is not int or not 0 <= value < 512 for value in volatile)
                            or len(set(volatile)) != len(volatile)):
                        raise CatalogError("Invalid volatile byte offsets")
                    field_bytes = set()
                    for ident in row["edit_ids"]:
                        e = known[ident]
                        start = max(e.offset, off) - off
                        end = min(e.end, off + 512) - off
                        field_bytes.update(range(start, end))
                    if set(volatile) & field_bytes:
                        raise CatalogError("Volatile byte overlaps a catalog field")
            elif row.get("role") == "guard":
                digest = row.get("sha256", "")
            else:
                raise CatalogError("Unknown sparse entry role")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise CatalogError("Invalid sparse guard SHA-256")
        for e in catalog.edits:
            if covered.get(e.id) != set(range(e.offset // 512 * 512, (e.end - 1) // 512 * 512 + 1, 512)):
                raise CatalogError("Profile does not fully cover every catalog field")
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return cls(json.loads(canonical), hashlib.sha256(canonical).hexdigest(), catalog)

    @property
    def entries(self):
        return self.data["entries"]

    def masks(self, *, writable_only=False):
        """Recognition includes parked fields; write masks exclude them entirely."""
        edits = {e.id: e for e in self.catalog.edits}
        result = []
        for row in self.entries:
            mask = bytearray(64)
            if row["role"] == "target":
                off = row["file_offset"]
                for ident in row["edit_ids"]:
                    e = edits[ident]
                    if writable_only and e.feature not in self.catalog.available_features:
                        continue
                    for at in range(max(e.offset, off), min(e.end, off + 512)):
                        relative = at - off
                        mask[relative // 8] |= 1 << (relative % 8)
            result.append(bytes(mask))
        return result


def load_device_profile(catalog):
    data = json.loads(files("a6700_unlock").joinpath("data/device-profile.json").read_text(encoding="utf-8"))
    return DeviceProfile.from_dict(data, catalog)


GUARD_VOLATILE_KIND = "guard-volatile-overlay"
_guard_volatile_data = None
_guard_compatibility_data = None


def _load_guard_volatile_data():
    global _guard_volatile_data
    if _guard_volatile_data is None:
        data = json.loads(files("a6700_unlock").joinpath("data/guard-volatile.json")
                          .read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("kind") != GUARD_VOLATILE_KIND:
            raise CatalogError("Unsupported guard volatile overlay")
        _guard_volatile_data = data
    return _guard_volatile_data


def guard_volatile_overlay(profile):
    """Guard sectors whose pinned bytes include ext2 inode time fields.

    The camera refreshes atime/ctime/mtime inside its own /usr inode table on
    ordinary boots, so a full-sector pin of those guard sectors flags an
    unmodified camera as changed. For the bound profile such guards compare
    the sector with the time fields zeroed against a factory-normalized hash;
    every other byte of the sector stays pinned byte-for-byte. Any other
    profile (synthetic test fixtures, future revisions) gets no overlay.
    """
    # A separately built fixed migration profile can carry its reviewed inode
    # overlay inside the profile itself. Its complete layout is then covered
    # by the same digest compiled into the reader/writer, without global state.
    data = profile.data.get("guard_volatile_overlay")
    if data is None:
        data = _load_guard_volatile_data()
        if data.get("profile_sha256") != profile.digest:
            return {}
    elif data.get("schema") != 1 or data.get("kind") != GUARD_VOLATILE_KIND:
        raise CatalogError("Unsupported inline guard volatile overlay")
    inode = data.get("inode_size")
    fields = data.get("volatile_fields")
    if (type(inode) is not int or not 0 < inode <= 512 or not isinstance(fields, list)
            or any(not (isinstance(span, list) and len(span) == 2
                        and type(span[0]) is int and type(span[1]) is int
                        and 0 <= span[0] < span[1] <= inode) for span in fields)):
        raise CatalogError("Invalid guard volatile field layout")
    records = {}
    entries = profile.entries
    for key, record in (data.get("records") or {}).items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            raise CatalogError("Invalid guard volatile index") from None
        if not 0 <= index < len(entries):
            raise CatalogError("Guard volatile index outside profile")
        row = entries[index]
        if (row.get("role") != "guard" or row.get("device") != record.get("device")
                or row.get("device_offset") != record.get("device_offset")
                or len(record.get("normalized_sha256", "")) != 64
                or any(c not in "0123456789abcdef" for c in record["normalized_sha256"])):
            raise CatalogError("Guard volatile record does not match profile entry")
        records[index] = dict(inode_size=inode,
                              fields=[tuple(span) for span in fields],
                              normalized_sha256=record["normalized_sha256"])
    return records


def _zero_inode_times(sector, inode_size, fields):
    if len(sector) % inode_size:
        return bytes(sector)
    normalized = bytearray(sector)
    for base in range(0, len(normalized), inode_size):
        for start, end in fields:
            normalized[base + start:base + end] = b"\0" * (end - start)
    return bytes(normalized)


def guard_compatibility_overlay(profile):
    """Recognize exact third-party guard contents while preserving their bytes."""
    global _guard_compatibility_data
    if _guard_compatibility_data is None:
        _guard_compatibility_data = json.loads(
            files("a6700_unlock").joinpath("data/guard-compatibility.json")
            .read_text(encoding="utf-8"))
    data = _guard_compatibility_data
    if data.get("schema") != 1 or data.get("kind") != "guard-compatibility-overlay":
        raise CatalogError("Unsupported guard compatibility overlay")
    if data.get("profile_sha256") != profile.digest:
        return {}
    records = {}
    for key, record in data["records"].items():
        try:
            index = int(key)
        except ValueError:
            raise CatalogError("Invalid guard compatibility index") from None
        if not 0 <= index < len(profile.entries):
            raise CatalogError("Guard compatibility index outside profile")
        row = profile.entries[index]
        if (row["role"] != "guard" or row["device"] != record["device"]
                or row["device_offset"] != record["device_offset"]
                or row["sha256"] != record["original_sha256"]):
            raise CatalogError("Guard compatibility record does not match profile entry")
        variants = {}
        for variant in record["variants"]:
            digest = variant["sha256"]
            if (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                    or digest == row["sha256"] or digest in variants):
                raise CatalogError("Invalid guard compatibility digest")
            variants[digest] = dict(source=data["source"]["name"], patches=variant["patches"])
        records[index] = variants
    return records


def _feature_selection(catalog, features):
    ids = [f["id"] for f in catalog.data["features"]]
    if features is None:
        return "all", list(catalog.available_features)
    if features == "missing":
        return "missing", list(catalog.available_features)
    if isinstance(features, str):
        features = [features]
    selected = []
    for ident in features:
        if ident not in ids:
            raise ValueError(f"Unknown feature: {ident}")
        if ident not in catalog.available_features:
            raise ValueError(f"功能暂未开放：{ident}，开发与验证尚未完成")
        if ident not in selected:
            selected.append(ident)
    if not selected:
        raise ValueError("至少选择一个功能")
    return "explicit", selected


def inspect_sectors(profile: DeviceProfile, sectors: list[bytes], action="inspect", features=None,
                    *, allow_guard_compatibility=True):
    if action not in ("inspect", "unlock", "close"):
        raise ValueError("Unsupported device operation")
    if len(sectors) != len(profile.entries) or any(len(s) != 512 for s in sectors):
        raise ValueError("Incomplete sparse capture")
    catalog = profile.catalog
    schema = profile.data.get("schema_version", 1)
    edits = {e.id: e for e in catalog.edits}
    index_by_file = {(r["image"], r["file_offset"]): r["index"]
                     for r in profile.entries if r["role"] == "target"}
    issues = []
    masks = profile.masks()
    volatile_guards = guard_volatile_overlay(profile)
    compatible_guards = guard_compatibility_overlay(profile) if allow_guard_compatibility else {}
    recognized_guards = []
    for row, sector, mask in zip(profile.entries, sectors, masks):
        if row["role"] == "target":
            if schema == 2:
                # v2 pins only bytes that no catalog field owns; known feature
                # values can change independently in the same sector.
                normalized = bytearray(sector)
                for at in range(512):
                    if mask[at // 8] & (1 << (at % 8)):
                        normalized[at] = 0
                for at in row.get("volatile_offsets", []):
                    normalized[at] = 0
                expected = row["unmanaged_sha256"]
            else:
                normalized = bytearray(sector)
                off = row["file_offset"]
                for ident in row["edit_ids"]:
                    e = edits[ident]
                    start, end = max(e.offset, off), min(e.end, off + 512)
                    normalized[start - off:end - off] = e.disabled[start - e.offset:end - e.offset]
                expected = row["normalized_sha256"]
        else:
            normalized = sector
            expected = row["sha256"]
            overlay_row = volatile_guards.get(row["index"])
            if overlay_row is not None:
                # ext2 inode guard: the camera refreshes these time fields
                # itself, so compare with them zeroed (see guard-volatile.json).
                normalized = _zero_inode_times(sector, overlay_row["inode_size"],
                                               overlay_row["fields"])
                expected = overlay_row["normalized_sha256"]
        actual_digest = hashlib.sha256(normalized).hexdigest()
        if actual_digest != expected:
            variant = compatible_guards.get(row["index"], {}).get(actual_digest)
            if variant is not None:
                recognized_guards.append(dict(index=row["index"], **variant))
            else:
                issue = dict(kind="sector-guard-mismatch", role=row["role"], index=row["index"],
                             device=profile.data["devices"][row["device"]],
                             offset=row["device_offset"],
                             expected_sha256=expected, actual_sha256=actual_digest)
                difference = _superblock_difference(row, sector)
                if difference is not None:
                    issue["superblock_difference"] = difference
                if actual_digest in guard_compatibility_overlay(profile).get(row["index"], {}):
                    issue["known_ftp_variant"] = True
                issues.append(issue)
    observations = []
    for edit in catalog.edits:
        actual = bytes(sectors[index_by_file[(edit.image, at // 512 * 512)]][at % 512]
                       for at in range(edit.offset, edit.end))
        state = edit.state(actual)
        observations.append((edit, state))
        if state == "unknown":
            issues.append(dict(kind="unknown-field", edit_id=edit.id, image=edit.image,
                               offset=edit.offset, actual=actual.hex()))
    mode, requested = _feature_selection(catalog, features)
    states = {feature["id"]: _combined_state(
        [s for e, s in observations if e.feature == feature["id"]])
        for feature in catalog.data["features"]}
    from .feature_updates import revision_status
    edit_states = {e.id: state for e, state in observations}
    revisions = {f["id"]: revision_status(f, catalog.edits, edit_states) for f in catalog.data["features"]}
    states.update({ident: info["state"] for ident, info in revisions.items() if info})
    if mode == "missing":
        goal = "enabled" if action == "unlock" else "disabled"
        selected = [feature["id"] for feature in catalog.data["features"]
                    if feature["id"] in requested and states[feature["id"]] != goal]
    else:
        selected = requested
    if action != "inspect" and not selected and mode != "missing":
        raise ValueError("所选功能没有需要修改的项")
    selected_set = set(selected)
    for feature in catalog.data["features"]:
        if feature["id"] not in selected_set:
            continue
        for required in feature.get("requires", []):
            if required not in selected_set and states.get(required) != "enabled":
                issues.append(dict(kind="feature-requirement", feature=feature["id"],
                                   requires=required, required_state=states.get(required)))
    after = [bytearray(s) for s in sectors]
    if action != "inspect":
        for edit in catalog.edits:
            if edit.feature not in selected_set:
                continue
            target = edit.enabled if action == "unlock" else edit.disabled
            for at, value in zip(range(edit.offset, edit.end), target):
                after[index_by_file[(edit.image, at // 512 * 512)]][at % 512] = value
    compatible = not issues
    if not compatible:
        after = [bytearray(s) for s in sectors]
    features_out = []
    for feature in catalog.data["features"]:
        row = {key: value for key, value in feature.items() if key not in ("id", "name")}
        row.update(id=feature["id"], name=feature["name"], state=states[feature["id"]],
                   selected=feature["id"] in selected_set)
        row.update(revisions[feature["id"]])
        features_out.append(row)
    changed = [i for i in range(len(sectors)) if sectors[i] != after[i]]
    plan = dict(profile_id=profile.data["profile_id"], profile_sha256=profile.digest,
                catalog_sha256=catalog.digest, model=catalog.data["model"], firmware=catalog.data["firmware"],
                action=action, compatible=compatible, features=features_out, issues=issues,
                selection_mode=mode, selected_features=[feature["id"] for feature in catalog.data["features"]
                                                        if feature["id"] in selected_set],
                bundle_state=_combined_state([s for _, s in observations]) if compatible else "unknown",
                status="blocked" if not compatible else "inspected" if action == "inspect" else "ready" if changed else "no-op",
                read_sectors=len(sectors), read_bytes=len(sectors) * 512,
                changed_indices=changed, changed_sectors=len(changed),
                changed_bytes=sum(sum(a != b for a, b in zip(sectors[i], after[i])) for i in changed),
                hardware_validation=profile.data["hardware_validation"], full_backup=False,
                guard_volatile=sorted(volatile_guards),
                guard_compatibility=recognized_guards,
                guard_compatibility_sha256=hashlib.sha256(json.dumps(
                    compatible_guards, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False).encode()).hexdigest() if compatible_guards else None)
    return plan, [bytes(s) for s in after]
