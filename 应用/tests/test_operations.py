from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_planner import fixture
from platform_lock import lock_file
from platform_lock import lock_file
from a6700_unlock.backup import (ARCHIVE_NAMES, KIND, list_backups, load_backup,
                                 restore_plan)
from a6700_unlock.catalog import Catalog
from a6700_unlock.device_profile import DeviceProfile, inspect_sectors, load_device_profile
from a6700_unlock.device_protocol import DeviceError, REQUEST, RESPONSE, request, response
from a6700_unlock.operations import execute, execute_backup, execute_restore, device_lock


def sparse_fixture(schema=1):
    data, images = fixture()
    catalog = Catalog.from_dict(data)
    entries = []
    for image, off, physical in [("app", 0, 4096), ("app", 512, 8192), ("wbi", 0, 0)]:
        digest = hashlib.sha256(images[image][off:off+512]).hexdigest()
        row = dict(index=len(entries), role="target", image=image, file_offset=off,
                   device=0 if image == "app" else 1, device_offset=physical,
                   edit_ids=[e.id for e in catalog.edits if e.image == image and e.offset < off+512 and e.end > off])
        row["unmanaged_sha256" if schema == 2 else "normalized_sha256"] = digest
        entries.append(row)
    entries.append(dict(index=3, role="guard", device=0, device_offset=12288,
                        sha256=hashlib.sha256(bytes(512)).hexdigest()))
    profile = DeviceProfile.from_dict(dict(schema_version=schema, sector_size=512,
        devices=["/dev/nflasha15", "/dev/nflasha5"], catalog_sha256=catalog.digest,
        profile_id="synthetic", hardware_validation="synthetic-only", entries=entries), catalog)
    return profile, [bytes(512) for _ in entries]


class Backend:
    def __init__(self, sectors, directory, failure=None):
        self.sectors = list(sectors)
        self.directory = directory
        self.failure = failure
        self.writes = self.reads = 0
        self.closed = False
        self.cleanup_warning = None
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def read(self, nonce):
        self.reads += 1
        if self.failure == "read-error": raise DeviceError("synthetic read error")
        if self.failure == "bad-verify" and self.writes:
            data = bytearray(self.sectors[0]);data[10] = 99
            return [bytes(data)] + self.sectors[1:]
        return list(self.sectors)
    def write(self, before, after, nonce):
        self.writes += 1
        runs = list((self.directory / "runs").iterdir())
        assert len(runs) == 1
        for name in ("preimages.json", "targets.json", "write-intent.json"):
            assert (runs[0]/name).is_file(), "Writer sent before durable journal"
        if self.failure == "lost-reply":
            self.sectors = after
            raise DeviceError("synthetic lost response")
        if self.failure == "interrupt": raise KeyboardInterrupt()
        if self.failure == "rejected-with-drift":
            self.sectors[0] = b"x" + self.sectors[0][1:]
            return dict(status=1,error="preimage changed",touched=0,rolled_back=False,failed_index=0,sectors=[])
        if self.failure == "rollback":
            return dict(status=5,error="write failure",touched=1,rolled_back=True,failed_index=0,sectors=[])
        self.sectors = after
        return dict(status=0,error=None,touched=3,rolled_back=False,failed_index=None,sectors=[])


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.profile, self.sectors = sparse_fixture()
        lock_patch = patch("a6700_unlock.operations.host_lock_path", return_value=self.directory / "host/.assistant.lock")
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
    def run_case(self, action="unlock", failure=None, sectors=None, **kwargs):
        backend = Backend(sectors or self.sectors, self.directory, failure)
        result = execute(self.profile, backend, action, self.directory, **kwargs)
        self.assertTrue(backend.closed)
        return result, backend

    def backup_case(self, sectors=None, dry_run=False, name="backup"):
        state = self.directory / name
        backend = Backend(sectors or self.sectors, state)
        result = execute_backup(self.profile, backend, self.directory / "archives",
                                state, dry_run=dry_run)
        self.assertTrue(backend.closed)
        return result, backend

    def restore_case(self, backup, sectors, dry_run=False, name="restore", features=None):
        state = self.directory / name
        backend = Backend(sectors, state)
        result = execute_restore(self.profile, backend, backup, state,
                                 dry_run=dry_run, features=features)
        self.assertTrue(backend.closed)
        return result, backend
    def test_journal_precedes_writer_and_independent_read_follows(self):
        result, backend = self.run_case()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["verified"])
        self.assertEqual({f["state"] for f in result["final_features"]},{"enabled"})
        self.assertEqual((backend.writes,backend.reads),(1,2))
        self.assertFalse(result["full_backup"])
        public = json.loads((Path(result["run_directory"])/"result.json").read_text())
        self.assertNotIn("sectors",public["native_result"])
    def test_unknown_guard_blocks_entire_operation(self):
        bad = list(self.sectors);bad[3] = b"x"+bad[3][1:]
        result, backend = self.run_case(sectors=bad)
        self.assertEqual(result["status"],"blocked")
        self.assertFalse(result["write_attempted"])
        self.assertEqual(backend.writes,0)
    def test_dry_run_and_inspect_do_not_write(self):
        result, backend = self.run_case(dry_run=True)
        self.assertEqual(result["status"],"dry-run")
        self.assertEqual(backend.writes,0)
    def test_already_installed_is_noop(self):
        _, installed = inspect_sectors(self.profile,self.sectors,"unlock")
        result, backend = self.run_case(sectors=installed)
        self.assertEqual(result["status"],"no-op")
        self.assertEqual(backend.writes,0)
    def test_close_restores_synthetic_original(self):
        _, installed = inspect_sectors(self.profile,self.sectors,"unlock")
        result, backend = self.run_case("close",sectors=installed)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(backend.sectors,self.sectors)
    def test_native_rollback_is_independently_verified(self):
        result, backend = self.run_case(failure="rollback")
        self.assertEqual(result["status"],"rolled-back")
        self.assertEqual((backend.writes,backend.reads),(1,2))
    def test_lost_write_reply_never_retries(self):
        result, backend = self.run_case(failure="lost-reply")
        self.assertEqual(result["status"],"uncertain")
        self.assertEqual(backend.writes,1)
        self.assertTrue((Path(result["run_directory"])/"write-intent.json").exists())
    def test_post_readback_failure_never_blindly_rewrites(self):
        result, backend = self.run_case(failure="bad-verify")
        self.assertEqual(result["status"],"verification-failed")
        self.assertFalse(result["verified"])
        self.assertEqual(backend.writes,1)
    def test_interrupt_after_writer_intent_is_uncertain_and_saved(self):
        result, backend = self.run_case(failure="interrupt")
        self.assertEqual(result["status"],"uncertain")
        self.assertTrue((Path(result["run_directory"])/"result.json").exists())
    def test_post_write_report_failure_still_reads_back_and_reports_success(self):
        from a6700_unlock.operations import save_json
        def fail_native(path, data):
            if path.name == "native-result.json":
                raise OSError("synthetic post-write report failure")
            return save_json(path, data)
        with patch("a6700_unlock.operations.save_json", side_effect=fail_native):
            result, backend = self.run_case()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["verified"])
        self.assertEqual((backend.writes, backend.reads), (1, 2))
        self.assertIn("相机执行结果", result["record_warning"])
        stored = json.loads((Path(result["run_directory"]) / "result.json").read_text())
        self.assertEqual(stored["record_warning"], result["record_warning"])

    def test_multiple_report_failures_preserve_warnings_and_verified_state(self):
        from a6700_unlock.operations import save_json
        def fail_reports(path, data):
            if path.name in ("native-result.json", "result.json"):
                raise OSError("synthetic full disk")
            return save_json(path, data)
        with patch("a6700_unlock.operations.save_json", side_effect=fail_reports):
            result, backend = self.run_case()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["verified"])
        self.assertEqual((backend.writes, backend.reads), (1, 2))
        self.assertIn("相机执行结果", result["record_warning"])
        self.assertIn("最终报告", result["record_warning"])

    def test_native_rejection_with_drift_is_not_reported_as_unchanged(self):
        result, backend = self.run_case(failure="rejected-with-drift")
        self.assertEqual(result["status"], "verification-failed")
        self.assertFalse(result["verified"])
        self.assertEqual((backend.writes, backend.reads), (1, 2))

    def test_journal_failure_prevents_writer(self):
        backend = Backend(self.sectors,self.directory)
        with patch("a6700_unlock.operations.os.fsync",side_effect=OSError("disk failed")):
            try:
                execute(self.profile,backend,"unlock",self.directory)
            except OSError:
                # POSIX: the failing fsync surfaces from lock/directory setup.
                # Windows only fsyncs journal files, so the same failure is
                # reported through the error result instead of an exception.
                pass
        self.assertEqual(backend.writes,0)
    def test_lock_prevents_concurrent_device_operations(self):
        with device_lock(self.directory):
            with self.assertRaises(DeviceError):
                execute(self.profile,Backend(self.sectors,self.directory),"unlock",self.directory)
    def test_host_lock_covers_different_state_directories_and_research_tools(self):
        from a6700_unlock.operations import host_lock_path, make_directory
        first, second = self.directory / "one", self.directory / "two"
        with device_lock(first):
            with self.assertRaises(DeviceError):
                execute(self.profile, Backend(self.sectors, second), "inspect", second)
        path = host_lock_path()
        make_directory(path.parent)
        with path.open("a") as research:
            lock_file(research)
            with self.assertRaises(DeviceError):
                with device_lock(second):
                    self.fail("Research and release tools must not share the camera")
        with device_lock(second):
            pass

    def test_sparse_backup_archive_is_checked_and_never_claims_full_disk(self):
        _, installed = inspect_sectors(self.profile, self.sectors, "unlock")
        result, backend = self.backup_case(installed)
        self.assertEqual(result["status"], "backup-completed")
        self.assertTrue(result["verified"])
        self.assertFalse(result["full_backup"])
        self.assertEqual(backend.writes, 0)
        path = Path(result["backup_path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".a6700backup")
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), ARCHIVE_NAMES)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["kind"], KIND)
            self.assertEqual(manifest["scope"], "sparse-feature-state-not-full-disk")
            self.assertEqual(manifest["entry_count"], len(self.profile.entries))
            self.assertEqual(len(archive.read("sectors.bin")), len(self.profile.entries) * 512)
            readme = archive.read("README.txt").decode()
            self.assertIn("不是整盘", readme)
            self.assertNotIn("BEGIN PRIVATE", readme)
        backup = load_backup(path, self.profile)
        self.assertEqual(list(backup.sectors), installed)
        self.assertEqual(backup.archive_sha256, result["backup_sha256"])
        rows = list_backups(self.directory / "archives")
        self.assertEqual([row["id"] for row in rows], [path.name])

    def test_backup_dry_run_creates_no_archive(self):
        result, backend = self.backup_case(dry_run=True)
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(backend.writes, 0)
        self.assertEqual(list_backups(self.directory / "archives"), [])

    def test_restore_writes_only_managed_fields_and_verifies(self):
        _, installed = inspect_sectors(self.profile, self.sectors, "unlock")
        created, _ = self.backup_case(installed)
        backup = load_backup(created["backup_path"], self.profile)
        result, backend = self.restore_case(backup, self.sectors)
        self.assertEqual(result["status"], "restored")
        self.assertTrue(result["verified"])
        self.assertTrue(result["write_attempted"])
        self.assertEqual((backend.writes, backend.reads), (1, 2))
        self.assertEqual(backend.sectors, installed)
        self.assertEqual(result["backup_id"], backup.id)
        self.assertTrue((Path(result["run_directory"]) / "preimages.json").is_file())
        self.assertTrue((Path(result["run_directory"]) / "restore-source.json").is_file())

    def test_restore_is_noop_and_dry_run_does_not_write(self):
        _, installed = inspect_sectors(self.profile, self.sectors, "unlock")
        created, _ = self.backup_case(installed)
        backup = load_backup(created["backup_path"], self.profile)
        result, backend = self.restore_case(backup, installed, name="same")
        self.assertEqual(result["status"], "no-op")
        self.assertTrue(result["verified"])
        self.assertEqual(backend.writes, 0)
        result, backend = self.restore_case(backup, self.sectors, dry_run=True, name="dry")
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(backend.writes, 0)

    def test_restore_blocks_guard_or_unmanaged_differences(self):
        _, installed = inspect_sectors(self.profile, self.sectors, "unlock")
        created, _ = self.backup_case(installed)
        backup = load_backup(created["backup_path"], self.profile)
        guard_changed = list(self.sectors)
        guard_changed[3] = b"x" + guard_changed[3][1:]
        result, backend = self.restore_case(backup, guard_changed, name="guard")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(backend.writes, 0)
        self.assertEqual({issue["kind"] for issue in result["plan"]["issues"]}, {"guard-mismatch"})
        unmanaged = list(installed)
        sector = bytearray(unmanaged[0])
        sector[0] ^= 1
        unmanaged[0] = bytes(sector)
        result, backend = self.restore_case(backup, unmanaged, name="unmanaged")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(backend.writes, 0)
        self.assertEqual({issue["kind"] for issue in result["plan"]["issues"]}, {"unmanaged-bytes"})

    def test_incompatible_content_cannot_become_a_backup(self):
        bad = list(self.sectors)
        bad[3] = b"x" + bad[3][1:]
        result, backend = self.backup_case(bad)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(backend.writes, 0)
        self.assertEqual(list_backups(self.directory / "archives"), [])

    def test_tampered_backup_payload_is_rejected_before_device_access(self):
        created, _ = self.backup_case()
        source = Path(created["backup_path"])
        with zipfile.ZipFile(source) as archive:
            manifest = archive.read("manifest.json")
            payload = bytearray(archive.read("sectors.bin"))
            readme = archive.read("README.txt")
        payload[0] ^= 1
        tampered = self.directory / "tampered.a6700backup"
        with zipfile.ZipFile(tampered, "w") as archive:
            archive.writestr("manifest.json", manifest)
            archive.writestr("sectors.bin", bytes(payload))
            archive.writestr("README.txt", readme)
        with self.assertRaisesRegex(DeviceError, "哈希"):
            load_backup(tampered, self.profile)

    def test_restore_plan_reports_guard_and_unmanaged_boundaries(self):
        _, installed = inspect_sectors(self.profile, self.sectors, "unlock")
        created, _ = self.backup_case(installed)
        backup = load_backup(created["backup_path"], self.profile)
        plan, after = restore_plan(self.profile, backup, self.sectors)
        self.assertTrue(plan["compatible"])
        self.assertEqual(after, installed)
        self.assertEqual(plan["changed_sectors"], 3)
        bad = list(self.sectors)
        bad[3] = b"x" + bad[3][1:]
        plan, after = restore_plan(self.profile, backup, bad)
        self.assertFalse(plan["compatible"])
        self.assertEqual(after, bad)

    def test_protocol_requires_nonce_and_complete_capture_and_masks(self):
        nonce=bytes(16)
        self.assertEqual(len(request(self.profile,nonce)),REQUEST.size)
        _,after=inspect_sectors(self.profile,self.sectors,"unlock")
        self.assertEqual(len(request(self.profile,nonce,self.sectors,after)),REQUEST.size+len(after)*1024)
        after[3]=b"x"+after[3][1:]
        with self.assertRaises(ValueError):request(self.profile,nonce,self.sectors,after)
        data=RESPONSE.pack(b"A67R",1,nonce,0,0,0xffffffff,0,0)
        self.assertEqual(response(data,nonce,0)["status"],0)
        with self.assertRaises(DeviceError):response(data,bytes([1])*16,0)
        with self.assertRaises(DeviceError):response(data+b"extra",nonce,0)
    def test_profile_rejects_wrong_catalog_or_incomplete_coverage(self):
        bad=deepcopy(self.profile.data);bad["catalog_sha256"]="0"*64
        with self.assertRaises(ValueError):DeviceProfile.from_dict(bad,self.profile.catalog)
        bad=deepcopy(self.profile.data);bad["entries"][0]["edit_ids"].remove("a-app")
        with self.assertRaises(ValueError):DeviceProfile.from_dict(bad,self.profile.catalog)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.profile, self.sectors = sparse_fixture(2)
        lock_patch = patch("a6700_unlock.operations.host_lock_path",
                           return_value=self.directory / "host/.assistant.lock")
        lock_patch.start()
        self.addCleanup(lock_patch.stop)

    def test_v2_profile_accepts_known_changes_in_sibling_feature_fields(self):
        _, a_enabled = inspect_sectors(self.profile, self.sectors, "unlock", features=["a"])
        plan, _ = inspect_sectors(self.profile, a_enabled, "inspect")
        self.assertTrue(plan["compatible"])
        self.assertEqual({row["id"]: row["state"] for row in plan["features"]},
                         {"a": "enabled", "b": "disabled"})
        _, b_enabled = inspect_sectors(self.profile, self.sectors, "unlock", features=["b"])
        plan, _ = inspect_sectors(self.profile, b_enabled, "inspect")
        self.assertTrue(plan["compatible"])
        self.assertEqual({row["id"]: row["state"] for row in plan["features"]},
                         {"a": "disabled", "b": "enabled"})

    def test_selection_limits_after_image_and_plan(self):
        plan, after = inspect_sectors(self.profile, self.sectors, "unlock", features=["a"])
        self.assertEqual(plan["selected_features"], ["a"])
        self.assertTrue(plan["compatible"])
        self.assertEqual({row["id"]: row["selected"] for row in plan["features"]},
                         {"a": True, "b": False})
        final, _ = inspect_sectors(self.profile, after, "inspect")
        self.assertEqual({row["id"]: row["state"] for row in final["features"]},
                         {"a": "enabled", "b": "disabled"})
        plan, _ = inspect_sectors(self.profile, self.sectors, "unlock", features="missing")
        self.assertEqual(plan["selected_features"], ["a", "b"])
        with self.assertRaises(ValueError):
            inspect_sectors(self.profile, self.sectors, "unlock", features=[])
        with self.assertRaises(ValueError):
            inspect_sectors(self.profile, self.sectors, "unlock", features=["unknown"])

    def test_all_missing_with_nothing_missing_is_noop(self):
        _, installed = inspect_sectors(self.profile, self.sectors, "unlock")
        plan, after = inspect_sectors(self.profile, installed, "unlock", features="missing")
        self.assertTrue(plan["compatible"])
        self.assertEqual(plan["status"], "no-op")
        self.assertEqual(plan["selected_features"], [])
        self.assertEqual(after, installed)

    def test_execute_selection_only_writes_selected_feature(self):
        backend = Backend(self.sectors, self.directory)
        result = execute(self.profile, backend, "unlock", self.directory, features=["a"])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["verified"])
        self.assertEqual(result["plan"]["selected_features"], ["a"])
        self.assertEqual({row["id"]: row["state"] for row in result["final_features"]},
                         {"a": "enabled", "b": "disabled"})
        self.assertEqual(backend.writes, 1)

    def test_restore_selection_only_restores_selected_feature(self):
        _, installed = inspect_sectors(self.profile, self.sectors, "unlock")
        backup_state = self.directory / "backup-state"
        created = execute_backup(self.profile, Backend(installed, backup_state),
                                 self.directory / "archives", backup_state)
        backup = load_backup(created["backup_path"], self.profile)
        plan, after = restore_plan(self.profile, backup, self.sectors, features=["a"])
        self.assertTrue(plan["compatible"])
        self.assertEqual(plan["selected_features"], ["a"])
        final, _ = inspect_sectors(self.profile, after, "inspect")
        self.assertEqual({row["id"]: row["state"] for row in final["features"]},
                         {"a": "enabled", "b": "disabled"})
        restore_state = self.directory / "restore-state"
        result = execute_restore(self.profile, Backend(self.sectors, restore_state),
                                 backup, restore_state, features=["a"])
        self.assertEqual(result["status"], "restored")
        self.assertEqual({row["id"]: row["state"] for row in result["final_features"]},
                         {"a": "enabled", "b": "disabled"})

    def test_unknown_value_in_unselected_feature_still_blocks(self):
        changed = list(self.sectors)
        sector = bytearray(changed[0])
        sector[15] = 3  # b-app only accepts 00/02
        changed[0] = bytes(sector)
        plan, _ = inspect_sectors(self.profile, changed, "unlock", features=["a"])
        self.assertFalse(plan["compatible"])
        self.assertEqual({issue["kind"] for issue in plan["issues"]}, {"unknown-field"})


if __name__=="__main__":unittest.main()
