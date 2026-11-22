"""Camera-free FTP migration and strict public-profile acceptance checks."""
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from a6700_unlock.catalog import Catalog
from a6700_unlock.device_profile import DeviceProfile, inspect_sectors
from a6700_unlock.device_protocol import DeviceError, request, response
from a6700_unlock.ftp_migration import (MigrationBundle, SELECTED, execute_migration,
    generic_inspection, load_bundle, migration_plan, project_generic)
from test_operations import Backend


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture():
    """Construct invented sectors; never package original firmware arrays."""
    real = load_bundle()
    profile = real.profile
    sectors = [bytearray(512) for _ in profile.entries]
    by_file = {(r.get("image"), r.get("file_offset")): r["index"] for r in profile.entries
               if r["role"] == "target"}
    for edit in profile.catalog.edits:
        value = edit.enabled if edit.feature in ("shutter", "exposure", "sound", "ftp-third-party-repair") else edit.disabled
        for at, byte in zip(range(edit.offset, edit.end), value):
            sectors[by_file[(edit.image, at // 512 * 512)]][at % 512] = byte
    sectors = list(map(bytes, sectors))
    generic_sectors = project_generic(real, sectors)
    def rebound(source, blocks):
        data = deepcopy(source.data)
        data.pop("guard_volatile_overlay", None)
        for row, raw, mask in zip(data["entries"], blocks, source.masks()):
            normalized = bytearray(raw)
            if row["role"] == "target":
                for at in range(512):
                    if mask[at // 8] & (1 << (at % 8)) or at in row.get("volatile_offsets", []):
                        normalized[at] = 0
                row["unmanaged_sha256"] = digest(normalized)
            else:
                row["sha256"] = digest(raw)
        return DeviceProfile.from_dict(data, source.catalog)
    return MigrationBundle(rebound(profile, sectors), rebound(real.generic, generic_sectors), {}, "synthetic"), sectors


def fields(bundle, sectors, feature, enabled):
    after = list(map(bytearray, sectors))
    index = {(r.get("image"), r.get("file_offset")): r["index"] for r in bundle.profile.entries
             if r["role"] == "target"}
    for edit in bundle.profile.catalog.edits:
        if edit.feature != feature:
            continue
        value = edit.enabled if enabled else edit.disabled
        for at, byte in zip(range(edit.offset, edit.end), value):
            after[index[(edit.image, at // 512 * 512)]][at % 512] = byte
    return list(map(bytes, after))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.bundle, self.sectors = fixture()

    def test_all_64_third_party_combinations_end_in_public_profile(self):
        bundle = self.bundle
        base = fields(bundle, self.sectors, "ftp", True)
        repairs = [e for e in bundle.profile.catalog.edits if e.feature == "ftp-third-party-repair"]
        for flags in itertools.product((False, True), repeat=6):
            before = list(map(bytearray, base))
            for edited, field in zip(flags, repairs):
                if edited:
                    row = next(r for r in bundle.profile.entries if field.id in r.get("edit_ids", []))
                    at = field.offset - row["file_offset"]
                    before[row["index"]][at:at + len(field.disabled)] = field.disabled
            before = list(map(bytes, before))
            plan, after = migration_plan(bundle, before)
            self.assertEqual(plan["changed_bytes"], 134 + sum(
                len(e.disabled) for flag, e in zip(flags, repairs) if flag))
            self.assertTrue(generic_inspection(bundle, after)["compatible"])
            self.assertEqual(generic_inspection(bundle, before)["compatible"], not any(flags))
            for feature in ("shutter", "exposure", "sound"):
                old = [e for e in bundle.profile.catalog.edits if e.feature == feature]
                for field in old:
                    row = next(r for r in bundle.profile.entries if field.id in r.get("edit_ids", []))
                    at = field.offset - row["file_offset"]
                    self.assertEqual(after[row["index"]][at:at + len(field.enabled)], field.enabled)

    def test_base_ftp_is_completed_and_repeated_migration_is_noop(self):
        plan, after = migration_plan(self.bundle, self.sectors)
        self.assertEqual(plan["changed_bytes"], 241 + 134)
        next_plan, repeated = migration_plan(self.bundle, after)
        self.assertEqual(next_plan["changed_bytes"], 0)
        self.assertEqual(repeated, after)
        self.assertEqual(next(f for f in generic_inspection(self.bundle, after)["features"] if f["id"] == "ftp")["state"], "enabled")

    def test_partial_matrix_is_completed_and_unknown_values_stop(self):
        _, complete = migration_plan(self.bundle, self.sectors)
        partial = list(complete)
        edit = next(e for e in self.bundle.profile.catalog.edits if e.feature == "ftp-custom-key-matrix")
        row = next(r for r in self.bundle.profile.entries if edit.id in r.get("edit_ids", []))
        raw = bytearray(partial[row["index"]]); raw[edit.offset % 512] = 0
        partial[row["index"]] = bytes(raw)
        self.assertEqual(migration_plan(self.bundle, partial)[0]["changed_bytes"], 1)
        raw[edit.offset % 512] = 2; partial[row["index"]] = bytes(raw)
        with self.assertRaises(DeviceError):
            migration_plan(self.bundle, partial)

    def test_unknown_guard_repair_and_unmanaged_bytes_are_rejected(self):
        for row in self.bundle.profile.entries:
            if row["role"] == "guard" or any(i.startswith("ftp-repair-") for i in row.get("edit_ids", [])):
                before = list(self.sectors)
                raw = bytearray(before[row["index"]]); raw[0] ^= 0x80
                before[row["index"]] = bytes(raw)
                with self.assertRaises(DeviceError):
                    migration_plan(self.bundle, before)

    def test_shared_transaction_journal_readback_and_public_validation(self):
        before = fields(self.bundle, fields(self.bundle, self.sectors, "ftp", True), "ftp-third-party-repair", False)
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            backend = Backend(before, state)
            with patch("a6700_unlock.operations.host_lock_path", return_value=state / "host.lock"):
                result = execute_migration(self.bundle, backend, apply=True, state_dir=state, progress=lambda _: None)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["verified"])
            self.assertTrue(result["generic_compatible"])
            self.assertEqual((backend.writes, backend.reads), (1, 2))
            self.assertEqual(result["plan"]["changed_bytes"], 141)
            self.assertTrue((Path(result["run_directory"]) / "public-042-inspection.json").is_file())

    def test_lost_reply_blocks_second_execution_before_usb(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            backend = Backend(self.sectors, state, "lost-reply")
            with patch("a6700_unlock.operations.host_lock_path", return_value=state / "host.lock"):
                result = execute_migration(self.bundle, backend, apply=True, state_dir=state, progress=lambda _: None)
                self.assertEqual(result["status"], "uncertain")
                second = Backend(self.sectors, state)
                with self.assertRaises(DeviceError):
                    execute_migration(self.bundle, second, apply=True, state_dir=state, progress=lambda _: None)
                self.assertEqual((second.writes, second.reads), (0, 0))

    def test_packaged_writable_scope_is_only_ftp_matrix_and_seven_repair_bytes(self):
        bundle = load_bundle()
        self.assertEqual(set(bundle.profile.catalog.available_features), set(SELECTED))
        self.assertEqual(sum(len(e.disabled) for e in bundle.profile.catalog.edits
                             if e.feature == "ftp-third-party-repair"), 7)
        self.assertEqual(sum(len(e.disabled) for e in bundle.profile.catalog.edits
                             if e.feature == "ftp-custom-key-matrix"), 134)
        keys = {(r["device"], r["device_offset"]) for r in bundle.generic.entries}
        for row in bundle.profile.entries:
            if any(i in {e.id for e in bundle.profile.catalog.edits if e.feature == "ftp-custom-key-matrix"}
                   for i in row.get("edit_ids", [])):
                self.assertNotIn((row["device"], row["device_offset"]), keys)


class MigrationNativeTests(unittest.TestCase):
    def test_fixed_writer_readback_drift_rejection_and_rollback(self):
        from test_native import AVAILABLE, Machine
        self.assertTrue(AVAILABLE, "Install the documented native test dependencies")
        bundle = load_bundle(); profile = bundle.profile; nonce = bytes(range(16))
        before = [bytes(512) for _ in profile.entries]
        after = list(map(bytearray, before))
        for i, mask in enumerate(profile.masks(writable_only=True)):
            for at in range(512):
                if mask[at // 8] & (1 << (at % 8)):
                    after[i][at] = 1
        after = list(map(bytes, after))
        config = request(profile, nonce, before, after)
        vm = Machine(profile, bundle.assets["writer"], config, before)
        reply = response(vm.run(), nonce, 0)
        self.assertEqual(reply["status"], 0)
        written = [bytes(vm.blocks[(r["device"], r["device_offset"])]) for r in profile.entries]
        self.assertEqual(written, after)
        reader = Machine(profile, bundle.assets["reader"], request(profile, nonce), written)
        self.assertEqual(response(reader.run(), nonce, len(after))["sectors"], after)
        drift = list(before); drift[-1] = b"x" + drift[-1][1:]
        vm = Machine(profile, bundle.assets["writer"], config, drift)
        self.assertEqual(response(vm.run(), nonce, 0)["status"], 1)
        self.assertEqual(vm.write_count, 0)
        for failure in ("write", "fsync", "readback"):
            vm = Machine(profile, bundle.assets["writer"], config, before, failure)
            self.assertTrue(response(vm.run(), nonce, 0)["rolled_back"])
            self.assertEqual([bytes(vm.blocks[(r["device"], r["device_offset"])]) for r in profile.entries], before)


if __name__ == "__main__":
    unittest.main()
