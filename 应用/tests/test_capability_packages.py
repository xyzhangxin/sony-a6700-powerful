from copy import deepcopy
from dataclasses import replace
import tempfile
import unittest

from a6700_unlock.capability_packages import (
    ADAPTER_SET_SCHEMA, CapabilityPackageError, _digest, builtin_adapter_set,
    package_versions, resolve_capability_selection, validate_adapter_set)
from a6700_unlock.catalog import load_catalog
from a6700_unlock.device_profile import load_device_profile
from a6700_unlock.desktop.controller import DesktopController
from a6700_unlock.desktop.simulation import SimulationService


class CapabilityPackageTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_device_profile(load_catalog())
        self.adapter = builtin_adapter_set(self.profile)

    def resign(self, adapter):
        body = deepcopy(adapter)
        body.pop('digest', None)
        return dict(body, digest=_digest(body))

    def test_builtin_set_binds_exact_profile_and_all_current_capabilities(self):
        self.assertEqual(self.adapter['schema'], ADAPTER_SET_SCHEMA)
        self.assertEqual(self.adapter['profile_sha256'], self.profile.digest)
        self.assertEqual(self.adapter['catalog_sha256'], self.profile.catalog.digest)
        self.assertEqual(len(self.adapter['packages']), 9)
        self.assertEqual(self.adapter['digest'], builtin_adapter_set(self.profile)['digest'])
        versions = package_versions(self.adapter)
        self.assertEqual(versions['fl23']['workflow_revision'], 1)
        self.assertFalse(versions['pixel_shift']['available'])
        self.assertTrue(versions['custom_shutter_sound']['available'])

    def test_availability_is_part_of_the_exact_adapter_digest(self):
        other = builtin_adapter_set(self.profile, fl23_available=False)
        self.assertNotEqual(self.adapter['digest'], other['digest'])
        with self.assertRaises(CapabilityPackageError):
            resolve_capability_selection(other, {'fl23'}, 'unlock')

    def test_creative_options_share_loader_without_false_dependency(self):
        result = resolve_capability_selection(self.adapter, {'fl23', 'creative_look_names'}, 'unlock')
        self.assertEqual(result['resolved_features'], ['fl23', 'creative_look_names'])
        self.assertEqual(result['adapter_set_sha256'], self.adapter['digest'])
        for package in self.adapter['packages'][-2:]:
            self.assertEqual(package['dependencies'], [])
            self.assertEqual(package['shared_resources'][0]['retention'],
                             'retain-while-any-dependent-enabled')

    def test_custom_shutter_sound_resolves_sound_on_unlock_and_preserves_on_close(self):
        package = next(row for row in self.adapter['packages']
                       if row['package_id'] == 'custom_shutter_sound')
        self.assertTrue(package['available'])
        self.assertEqual(package['dependencies'], ['sound'])
        self.assertEqual(package['shared_resources'], [
            {'id': 'startup-loader', 'retention': 'retain-while-any-dependent-enabled'},
        ])
        self.assertEqual(package['modification']['startup_action'], {
            'id': 'custom_shutter_sound', 'loader': 'startup-loader', 'earliest_boot_ms': 10_000,
        })
        self.assertEqual(package['modification']['executor'],
                         'custom-shutter-sound-package')
        self.assertEqual(package['modification']['slot_count'], 10)
        self.assertTrue(package['modification']['installable'])
        unlocked = resolve_capability_selection(self.adapter, {'custom_shutter_sound'}, 'unlock')
        self.assertEqual(unlocked['resolved_features'], ['sound', 'custom_shutter_sound'])
        closed = resolve_capability_selection(self.adapter, {'custom_shutter_sound'}, 'close')
        self.assertEqual(closed['resolved_features'], ['custom_shutter_sound'])
        unavailable = builtin_adapter_set(self.profile, custom_sound_available=False)
        with self.assertRaisesRegex(CapabilityPackageError, '未开放'):
            resolve_capability_selection(unavailable, {'custom_shutter_sound'}, 'unlock')

    def test_unknown_unavailable_and_tampered_packages_are_rejected(self):
        with self.assertRaises(CapabilityPackageError):
            resolve_capability_selection(self.adapter, {'not-a-feature'}, 'unlock')
        with self.assertRaises(CapabilityPackageError):
            resolve_capability_selection(self.adapter, {'pixel_shift'}, 'unlock')
        tampered = deepcopy(self.adapter)
        tampered['digest'] = '0' * 64
        with self.assertRaises(CapabilityPackageError):
            validate_adapter_set(tampered)
        incompatible = deepcopy(self.adapter)
        incompatible['packages'][0]['min_core_api'] = 99
        with self.assertRaises(CapabilityPackageError):
            validate_adapter_set(self.resign(incompatible))
        bad_core = deepcopy(self.adapter)
        bad_core['core_api_version'] = 2
        with self.assertRaises(CapabilityPackageError):
            validate_adapter_set(self.resign(bad_core))

    def test_unknown_and_cyclic_dependencies_are_rejected(self):
        unknown = deepcopy(self.adapter)
        unknown['packages'][0]['dependencies'] = ['missing-package']
        with self.assertRaises(CapabilityPackageError):
            validate_adapter_set(self.resign(unknown))
        cyclic = deepcopy(self.adapter)
        cyclic['packages'][0]['dependencies'] = ['fl23']
        cyclic['packages'][-2]['dependencies'] = ['ftp']
        with self.assertRaises(CapabilityPackageError):
            validate_adapter_set(self.resign(cyclic))

    def test_conflicting_byte_targets_are_rejected_before_execution(self):
        first, second = self.profile.catalog.edits[:2]
        changed = replace(second, offset=first.offset,
                          enabled=bytes([first.enabled[0] ^ 0xff]) + first.enabled[1:])
        catalog = replace(self.profile.catalog,
                          edits=(first, changed, *self.profile.catalog.edits[2:]))
        profile = replace(self.profile, catalog=catalog)
        with self.assertRaisesRegex(CapabilityPackageError, '同字节写入目标冲突'):
            builtin_adapter_set(profile)

    def test_desktop_snapshot_and_simulation_result_pin_adapter_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = DesktopController(directory)
            snapshot = controller.snapshot()
            self.assertEqual(snapshot['adapter_set_sha256'], controller.service.adapter_set['digest'])
            self.assertIn('fl23', snapshot['capability_packages'])
            service = SimulationService(directory, 'original')
            result = service.perform('unlock', features={'ftp'})
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['capability_resolution']['adapter_set_sha256'],
                             service.adapter_set['digest'])
            self.assertEqual(result['capability_resolution']['resolved_features'], ['ftp'])


if __name__ == '__main__':
    unittest.main()
