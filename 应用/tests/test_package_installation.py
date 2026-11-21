import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from a6700_unlock import operations
from a6700_unlock.package_installation import (
    InstallationPlanError,
    PackageBinding,
    PackagePart,
    PackageWriteUnconfirmed,
    execute_package_installation,
    pending_package_installation,
    plan_installation,
)


def sha(label):
    return hashlib.sha256(label.encode()).hexdigest()


class PackageInstallationTests(unittest.TestCase):
    def setUp(self):
        self.binding = PackageBinding(
            "example", "1.0", sha("package"), sha("adapter"), sha("device"), "ILCE-6700", "2.00"
        )
        self.loader = PackagePart("loader", sha("old loader"), sha("new loader"))
        self.sound = PackagePart("sound", sha("old sound"), sha("new sound"))
        self.menu = PackagePart("menu", sha("old menu"), sha("new menu"))
        self.guard = PackagePart("other-settings", sha("settings"), sha("settings"))
        self.parts = (self.loader, self.sound, self.guard, self.menu)
        self.before = {part.part_id: part.before_sha256 for part in self.parts}
        self.after = {part.part_id: part.after_sha256 for part in self.parts}
        self.plan = plan_installation(self.binding, self.parts, self.before, action="install")

    def test_plan_pins_version_adapter_device_and_order(self):
        for field, value in (
            ("package_version", "1.1"),
            ("package_sha256", sha("another package")),
            ("adapter_sha256", sha("another adapter")),
            ("device_identity_sha256", sha("another body")),
            ("firmware", "2.01"),
        ):
            with self.subTest(field=field):
                other = replace(self.binding, **{field: value})
                plan = plan_installation(other, self.parts, self.before, action="install")
                self.assertNotEqual(self.plan.plan_sha256, plan.plan_sha256)
                with self.assertRaises(InstallationPlanError):
                    self.plan.check_preimages(other, self.before)
        reversed_plan = plan_installation(
            self.binding, reversed(self.parts), self.before, action="install"
        )
        self.assertNotEqual(self.plan.plan_sha256, reversed_plan.plan_sha256)
        self.assertEqual(self.plan.writes, (self.loader, self.sound, self.menu))

    def test_preimages_reject_stale_or_out_of_scope_data(self):
        with self.assertRaises(InstallationPlanError):
            plan_installation(self.binding, self.parts, self.after, action="install")
        with self.assertRaises(InstallationPlanError):
            self.plan.check_preimages(self.binding, dict(self.before, unrelated=sha("extra")))
        with self.assertRaises(InstallationPlanError):
            plan_installation(self.binding, (*self.parts, self.loader), self.before, action="install")

    def test_independent_readback_covers_guards_and_changed_parts(self):
        self.plan.verify_readback(self.binding, self.after)
        with self.assertRaises(InstallationPlanError):
            self.plan.verify_readback(self.binding, dict(self.after, menu=self.menu.before_sha256))
        with self.assertRaises(InstallationPlanError):
            self.plan.verify_readback(self.binding, {**self.after, "other-settings": sha("changed")})

    def test_recovery_after_last_write_or_partial_write(self):
        # After a failed readback, a fresh observation may show all or only a
        # prefix of the writes. Reconcile either state without rewriting guards.
        for prefix in range(len(self.plan.writes) + 1):
            with self.subTest(prefix=prefix):
                observed = dict(self.before)
                for part in self.plan.writes[:prefix]:
                    observed[part.part_id] = part.after_sha256
                rollback = self.plan.rollback_writes(self.binding, observed)
                self.assertEqual(
                    rollback, tuple(part.reversed() for part in reversed(self.plan.writes[:prefix]))
                )
                for part in rollback:
                    self.assertEqual(observed[part.part_id], part.before_sha256)
                    observed[part.part_id] = part.after_sha256
                self.plan.check_preimages(self.binding, observed)

    def test_unknown_state_requires_reconciliation(self):
        for observed in (
            dict(self.before, loader=sha("unknown write result")),
            dict(self.before, menu=self.menu.after_sha256),
            {**self.after, "other-settings": sha("external change")},
        ):
            with self.subTest(observed=observed), self.assertRaises(InstallationPlanError):
                self.plan.rollback_writes(self.binding, observed)

    def test_withdrawal_keeps_loader_when_other_feature_needs_it(self):
        loader_guard = PackagePart("loader", self.loader.after_sha256, self.loader.after_sha256)
        parts = (self.menu.reversed(), self.sound.reversed(), loader_guard, self.guard)
        withdrawal = plan_installation(self.binding, parts, self.after, action="withdraw")
        self.assertEqual(withdrawal.writes, parts[:2])
        self.assertEqual(withdrawal.rollback_writes(self.binding, self.after), ())
        observed = dict(self.before, loader=self.loader.after_sha256)
        withdrawal.verify_readback(self.binding, observed)
        self.assertEqual(
            withdrawal.rollback_writes(self.binding, observed), (self.sound, self.menu)
        )


class SimulatedPackageAdapter:
    def __init__(self, binding):
        self.binding = binding
        self.data = {"loader": b"old loader", "sound": b"old sound"}
        self.events = []
        self.reads = 0
        self.bad_read = None
        self.fail_after_write = None
        self.unconfirmed = False

    def read_snapshot(self, part_ids):
        self.reads += 1
        self.events.append("read")
        snapshot = {key: self.data[key] for key in part_ids}
        if self.reads == self.bad_read:
            snapshot["loader"] = b"bad readback"
        return self.binding, snapshot

    def apply_part(self, part):
        assert hashlib.sha256(self.data[part.part_id]).hexdigest() == part.before_sha256
        self.events.append("apply " + part.part_id)
        self.data[part.part_id] = ("new " + part.part_id).encode()
        if self.unconfirmed:
            raise PackageWriteUnconfirmed("timeout")
        if part.part_id == self.fail_after_write:
            raise RuntimeError("write failed after commit")

    def restore_part(self, part, preimage):
        assert hashlib.sha256(self.data[part.part_id]).hexdigest() == part.before_sha256
        assert hashlib.sha256(preimage).hexdigest() == part.after_sha256
        self.events.append("restore " + part.part_id)
        self.data[part.part_id] = preimage


class PackageExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.journals = self.root / "package-installations"
        lock_path = patch(
            "a6700_unlock.operations.host_lock_path", return_value=self.root / ".assistant.lock"
        )
        lock_path.start()
        self.addCleanup(lock_path.stop)
        state_dir = patch(
            "a6700_unlock.operations.default_state_dir", return_value=self.root / "app"
        )
        state_dir.start()
        self.addCleanup(state_dir.stop)
        self.binding = PackageBinding(
            "example", "1", sha("package"), sha("adapter"), sha("device"), "ILCE-6700", "2.00"
        )
        parts = tuple(PackagePart(key, sha("old " + key), sha("new " + key))
                      for key in ("loader", "sound"))
        self.plan = plan_installation(
            self.binding, parts, {part.part_id: part.before_sha256 for part in parts}, action="install"
        )
        self.adapter = SimulatedPackageAdapter(self.binding)

    def execute(self):
        return execute_package_installation(self.plan, self.adapter, state_dir=self.root / "app")

    def test_success_saves_preimages_before_write_and_verifies_independently(self):
        apply = self.adapter.apply_part

        def checked_apply(part):
            run = pending_package_installation(self.journals)
            preimages = json.loads((run / "preimages.json").read_text(encoding="utf-8"))
            self.assertEqual(base64.b64decode(preimages[part.part_id]), self.adapter.data[part.part_id])
            apply(part)

        self.adapter.apply_part = checked_apply
        report = self.execute()
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["functional_acceptance"], "not_run")
        self.assertEqual(self.adapter.events, ["read", "apply loader", "apply sound", "read"])
        self.assertIsNone(pending_package_installation(self.journals))

    def test_failure_after_partial_write_rolls_back_written_part(self):
        self.adapter.fail_after_write = "loader"
        report = self.execute()
        self.assertEqual(report["status"], "rolled_back")
        self.assertEqual(self.adapter.events, ["read", "apply loader", "read", "restore loader", "read"])
        self.assertIsNone(pending_package_installation(self.journals))

    def test_final_readback_failure_rechecks_then_restores_the_whole_package(self):
        self.adapter.bad_read = 2
        report = self.execute()
        self.assertEqual(report["status"], "rolled_back")
        self.assertEqual(report["rollback_parts"], ["sound", "loader"])
        self.assertEqual(self.adapter.events[-4:], ["read", "restore sound", "restore loader", "read"])
        self.assertEqual(self.adapter.data, {"loader": b"old loader", "sound": b"old sound"})

    def test_unconfirmed_write_stops_device_access_and_blocks_retry(self):
        self.adapter.unconfirmed = True
        report = self.execute()
        self.assertEqual(report["status"], "uncertain")
        self.assertEqual(self.adapter.events, ["read", "apply loader"])
        with self.assertRaises(InstallationPlanError):
            execute_package_installation(self.plan, self.adapter, state_dir=self.root / "other")
        with self.assertRaises(InstallationPlanError):
            with operations.device_lock(self.root / "other"):
                self.fail("a second operation acquired a pending package session")
        self.assertEqual(self.adapter.events, ["read", "apply loader"])

    def test_failed_recovery_readback_keeps_transaction_pending(self):
        self.adapter.fail_after_write = "loader"
        self.adapter.bad_read = 3
        report = self.execute()
        self.assertEqual(report["status"], "uncertain")
        self.assertEqual(report["rollback_parts"], ["loader"])
        self.assertEqual(pending_package_installation(self.journals), Path(report["run_directory"]))

    def test_missing_result_blocks_before_device_access(self):
        run = self.journals / "interrupted"
        run.mkdir(parents=True)
        (run / "intent.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(InstallationPlanError):
            self.execute()
        self.assertEqual(self.adapter.events, [])

    def test_stale_preimage_prevents_write(self):
        self.adapter.data["loader"] = b"changed before lock"
        with self.assertRaises(InstallationPlanError):
            self.execute()
        self.assertEqual(self.adapter.events, ["read"])
        self.assertFalse(self.journals.exists())
