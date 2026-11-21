"""Exact third-party guard variants survive normal inspection and transactions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_operations import Backend, sparse_fixture
from a6700_unlock import device_profile
from a6700_unlock.catalog import CatalogError, load_catalog
from a6700_unlock.device_profile import inspect_sectors, load_device_profile
from a6700_unlock.device_protocol import request
from a6700_unlock.operations import execute


class GuardCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.profile, self.sectors = sparse_fixture()
        self.variant = bytes(30) + b"\x01" + bytes(481)
        row = self.profile.entries[3]
        self.overlay = dict(schema=1, kind="guard-compatibility-overlay",
                            profile_sha256=self.profile.digest,
                            source=dict(name="synthetic playback filter"),
                            records={"3": dict(device=row["device"],
                                device_offset=row["device_offset"], original_sha256=row["sha256"],
                                variants=[dict(sha256=hashlib.sha256(self.variant).hexdigest(),
                                               patches=["synthetic-permission"])])})
        state = patch.object(device_profile, "_guard_compatibility_data", self.overlay)
        state.start()
        self.addCleanup(state.stop)

    def capture(self, value=None):
        return self.sectors[:3] + [self.variant if value is None else value]

    def test_exact_variant_preserved_in_all_plans_and_native_request(self):
        for action in ("inspect", "unlock", "close"):
            with self.subTest(action=action):
                before = self.capture()
                plan, after = inspect_sectors(self.profile, before, action)
                self.assertTrue(plan["compatible"])
                self.assertEqual(plan["issues"], [])
                self.assertEqual(plan["guard_compatibility"][0]["index"], 3)
                self.assertEqual(after[3], before[3])
                self.assertNotIn(3, plan["changed_indices"])
                self.assertEqual(self.profile.masks(writable_only=True)[3], bytes(64))
                request(self.profile, bytes(16), before, after)

    def test_original_guard_still_accepted_without_variant_marker(self):
        plan, _ = inspect_sectors(self.profile, self.sectors)
        self.assertTrue(plan["compatible"])
        self.assertEqual(plan["guard_compatibility"], [])

    def test_extra_or_unknown_permission_byte_rejects_and_preserves_capture(self):
        for offset in (30, 100):
            changed = bytearray(self.variant)
            changed[offset] = 2
            before = self.capture(bytes(changed))
            plan, after = inspect_sectors(self.profile, before, "unlock")
            self.assertFalse(plan["compatible"])
            self.assertEqual(after, before)
            self.assertEqual(plan["changed_indices"], [])
            issue = plan["issues"][0]
            self.assertEqual(issue["actual_sha256"], hashlib.sha256(changed).hexdigest())
            self.assertEqual(issue["expected_sha256"], self.profile.entries[3]["sha256"])

    def test_overlay_requires_exact_profile_and_guard_identity(self):
        other = deepcopy(self.overlay)
        other["profile_sha256"] = "0" * 64
        with patch.object(device_profile, "_guard_compatibility_data", other):
            self.assertFalse(inspect_sectors(self.profile, self.capture())[0]["compatible"])
        for key, value in (("device_offset", 4096), ("original_sha256", "0" * 64)):
            other = deepcopy(self.overlay)
            other["records"]["3"][key] = value
            with patch.object(device_profile, "_guard_compatibility_data", other):
                with self.assertRaises(CatalogError):
                    inspect_sectors(self.profile, self.capture())

    def test_normal_transaction_writes_only_owned_targets_and_independently_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            backend = Backend(self.capture(), state)
            with patch("a6700_unlock.operations.host_lock_path", return_value=state / "host.lock"):
                result = execute(self.profile, backend, "unlock", state_dir=state)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["verified"])
            self.assertEqual(backend.writes, 1)
            self.assertEqual(backend.reads, 2)
            self.assertEqual(backend.sectors[3], self.variant)
            run = Path(result["run_directory"])
            for name in ("preimages.json", "targets.json"):
                recorded = json.loads((run / name).read_text())
                self.assertEqual(recorded["sectors"][3], self.variant.hex())

    def test_shipped_overlay_binds_only_read_only_guards(self):
        with patch.object(device_profile, "_guard_compatibility_data", None):
            profile = load_device_profile(load_catalog())
            records = device_profile.guard_compatibility_overlay(profile)
        self.assertEqual(sorted(records), [9, 12, 67, 90, 133])
        self.assertEqual([len(records[i]) for i in sorted(records)], [1, 1, 1, 1, 15])
        for index in records:
            self.assertEqual(profile.entries[index]["role"], "guard")
            self.assertEqual(profile.masks(writable_only=True)[index], bytes(64))

    def test_adapter_identity_includes_guard_recognition_rules(self):
        from a6700_unlock.capability_packages import builtin_adapter_set
        before = builtin_adapter_set(self.profile, include_creative_looks=False)
        other = deepcopy(self.overlay)
        other["records"]["3"]["variants"][0]["sha256"] = hashlib.sha256(b"other variant").hexdigest()
        with patch.object(device_profile, "_guard_compatibility_data", other):
            after = builtin_adapter_set(self.profile, include_creative_looks=False)
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertNotEqual(before["guard_compatibility_sha256"], after["guard_compatibility_sha256"])

    def test_desktop_connection_advances_to_feature_management(self):
        from test_desktop import ENV, PhysicalDesktopTests
        from a6700_unlock.desktop.controller import DesktopController
        from a6700_unlock.preparation import STEP_IDS

        backend = PhysicalDesktopTests.Backend(self.capture())
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch("a6700_unlock.desktop.physical.load_catalog", return_value=self.profile.catalog), \
                    patch("a6700_unlock.desktop.physical.load_device_profile", return_value=self.profile), \
                    patch("a6700_unlock.operations.host_lock_path", return_value=state / "host.lock"):
                controller = DesktopController(None, environment_provider=lambda: ENV,
                    mode="physical", physical_state_dir=state,
                    physical_backend_factory=lambda progress: backend)
                try:
                    controller.check_environment()
                    controller.acknowledge_notice(True)
                    controller.begin_preparation()
                    for ident in STEP_IDS:
                        controller.confirm(ident, True)
                    result = controller.operate("inspect")
                    self.assertEqual(result["phase"], "home")
                    self.assertTrue(result["compatible"])
                    self.assertFalse(result["write_attempted"])
                    self.assertEqual(backend.sectors[3], self.variant)
                finally:
                    controller.service.close_session()

    def test_physical_inspect_recognizes_ten_slot_combined_stage_with_exact_binding(self):
        from a6700_unlock.desktop.physical import PhysicalService
        from a6700_unlock.package_installation import PackageBinding
        from test_desktop import PhysicalDesktopTests

        class BoundBackend(PhysicalDesktopTests.Backend):
            def __init__(self, sectors, binding_value):
                super().__init__(sectors)
                self._custom_sound_identity = None
                self._binding_value = binding_value

            def read_custom_sound_identity(self):
                self._custom_sound_identity = "c" * 64
                return self._custom_sound_identity

            def read_custom_sound_binding(self):
                return self._binding_value

        expected_binding = PackageBinding(
            "custom_shutter_sound", "ten-slot", "b" * 64,
            self.profile.digest, "c" * 64, "ILCE-6700", "2.00")

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            backend = BoundBackend(self.sectors, expected_binding)
            with patch("a6700_unlock.desktop.physical.load_catalog", return_value=self.profile.catalog), \
                 patch("a6700_unlock.desktop.physical.load_device_profile", return_value=self.profile), \
                 patch("a6700_unlock.fl23_package.availability", return_value=(True, True)), \
                 patch("a6700_unlock.operations.host_lock_path", return_value=state / "host.lock"):
                service = PhysicalService(state_dir=state, backend_factory=lambda _p: backend)
                service.fl23_available = True
                service.names_available = True
                with patch("a6700_unlock.operations._execute_session",
                           return_value=dict(
                               action="inspect", status="inspected", device_access=True,
                               write_attempted=False, run_directory=str(state / "run"),
                               plan=dict(compatible=True, features=[], guard_compatibility=[
                                   dict(index=9, patches=["custom-sound-menu-inode-size"]),
                                   dict(index=12, patches=["custom-sound-ten-slot-descriptors"]),
                               ]))):
                    res = service.perform("inspect")
                    self.assertEqual(res["status"], "inspected")
                    self.assertTrue(res["plan"]["compatible"])
                    self.assertEqual(res["fl23_inspection"]["mode"], "custom-sound-combined-stage")
                    self.assertEqual({f["id"]: f["state"] for f in res["plan"]["features"]},
                                     {"fl23": "enabled", "creative_look_names": "enabled"})
                    self.assertEqual(res["custom_sound"]["state"], "enabled")
                    self.assertEqual(res["custom_sound"]["mode"], "custom-sound-combined-stage")
                    self.assertFalse(res["custom_sound"]["installable"])
                    service.close_session()

                    backend_bad = BoundBackend(self.sectors, PackageBinding(
                        "custom_shutter_sound", "ten-slot", "b" * 64,
                        self.profile.digest, "d" * 64, "ILCE-6700", "2.00"))
                    service.backend_factory = lambda _p: backend_bad
                    res_bad = service.perform("inspect")
                    self.assertEqual(res_bad["status"], "blocked")
                    self.assertFalse(res_bad["plan"]["compatible"])
                    self.assertEqual(res_bad["custom_sound"]["state"], "error")
                    service.close_session()

                    with patch("a6700_unlock.operations._execute_session",
                               return_value=dict(
                                   action="inspect", status="inspected", device_access=True,
                                   write_attempted=False, run_directory=str(state / "run2"),
                                   plan=dict(compatible=True, features=[], guard_compatibility=[]))):
                        service_plain = PhysicalService(state_dir=state, backend_factory=lambda _p: backend)
                        res_plain = service_plain.perform("inspect")
                        self.assertEqual(res_plain["status"], "inspected")
                        self.assertEqual(res_plain["custom_sound"]["state"], "disabled")
                        self.assertEqual(res_plain["custom_sound"]["mode"], "not-installed")
                        service_plain.close_session()

    def test_unified_sound_keeps_install_state_when_runtime_binding_is_refused(self):
        from a6700_unlock.desktop.physical import PhysicalService
        from a6700_unlock.device_protocol import DeviceError
        from a6700_unlock.package_installation import PackageBinding
        from test_desktop import PhysicalDesktopTests

        class UnifiedBackend(PhysicalDesktopTests.Backend):
            def _command(self, *_args):
                raise AssertionError("the synthetic backend must not access a camera")

            def read_custom_sound_identity(self):
                self._custom_sound_identity = "c" * 64
                return self._custom_sound_identity

            def read_custom_sound_binding(self):
                if runtime_error:
                    self._fl23_transport_uncertain = transport_uncertain
                    self._custom_sound_service_restore_unconfirmed = restore_unconfirmed
                    raise DeviceError("机身拒绝加载器观察（原生状态 8）")
                return PackageBinding("custom_shutter_sound", "ten-slot", "b" * 64,
                                      profile.digest, "c" * 64, "ILCE-6700", "2.00")

        profile = self.profile
        for runtime_error, transport_uncertain, restore_unconfirmed, inspect_status, sound_state in (
            (False, False, False, "inspected", "enabled"),
            (True, False, False, "inspected", "error"),
            (True, True, False, "blocked", "error"),
            (True, False, True, "blocked", "error"),
        ):
            with self.subTest(runtime_error=runtime_error, transport_uncertain=transport_uncertain,
                              restore_unconfirmed=restore_unconfirmed), \
                 tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                backend = UnifiedBackend(self.sectors)
                result = dict(status="inspected", run_directory=str(state / "run"),
                              plan=dict(compatible=True, features=[]))
                package = dict(status="inspected", plan=dict(features=[
                    dict(id="custom_shutter_sound", state="enabled"),
                    dict(id="fl23", state="enabled")]))
                with patch("a6700_unlock.desktop.physical.load_catalog", return_value=profile.catalog), \
                     patch("a6700_unlock.desktop.physical.load_device_profile", return_value=profile), \
                     patch("a6700_unlock.operations.host_lock_path", return_value=state / "host.lock"), \
                     patch("a6700_unlock.operations._execute_session", return_value=result), \
                     patch("a6700_unlock.fl23_package.perform", return_value=package) as inspect_package:
                    service = PhysicalService(state_dir=state, backend_factory=lambda _p: backend)
                    service.fl23_available = service.custom_sound_available = True
                    try:
                        observed = service.perform("inspect")
                        self.assertEqual(observed["status"], inspect_status)
                        self.assertEqual(observed["custom_sound"]["state"], sound_state)
                        self.assertEqual(observed["custom_sound"]["mode"], "fl23-unified-carrier")
                        self.assertEqual(observed["plan"]["features"][0]["state"], "enabled")
                        self.assertEqual(observed["plan"]["features"][1]["state"], "enabled")
                        if runtime_error:
                            self.assertIn("原生状态 8", observed["custom_sound"]["reason"])
                        inspect_package.assert_called_once()
                    finally:
                        service.close_session()

    def test_combined_unlock_checks_carrier_before_writing_basic_sound_dependency(self):
        from a6700_unlock.desktop.physical import PhysicalService

        with tempfile.TemporaryDirectory() as directory, \
             patch("a6700_unlock.desktop.physical.load_catalog", return_value=self.profile.catalog), \
             patch("a6700_unlock.desktop.physical.load_device_profile", return_value=self.profile), \
             patch("a6700_unlock.operations._execute_session") as ordinary, \
             patch("a6700_unlock.fl23_package.perform", return_value={"status": "error", "error": "菜单冲突"}) as package:
            service = PhysicalService(state_dir=Path(directory), backend_factory=lambda _: None)
            service.custom_sound_available = True
            backend = object()
            result = service._perform_combined(backend, "unlock", Path(directory), lambda _: None,
                                               ["sound", "custom_shutter_sound"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "菜单冲突")
        self.assertFalse(result["write_attempted"])
        self.assertFalse(result["settings_write_attempted"])
        ordinary.assert_not_called()
        package.assert_called_once()
        self.assertEqual(package.call_args.kwargs, dict(features=["custom_shutter_sound"], validate_unlock=True))


if __name__ == "__main__":
    unittest.main()
