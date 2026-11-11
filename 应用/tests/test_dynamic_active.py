"""Exercise the shipped six-byte adaptation with synthetic sector contents only."""
from copy import deepcopy
import hashlib
import unittest

from a6700_unlock.catalog import load_catalog
from a6700_unlock.device_profile import DeviceProfile, inspect_sectors, load_device_profile


def synthetic_release():
    catalog = load_catalog()
    shipped = load_device_profile(catalog)
    data = deepcopy(shipped.data)
    sectors = [bytearray(512) for _ in shipped.entries]
    for row in shipped.entries:
        if row['role'] == 'target':
            for ident in row['edit_ids']:
                edit = next(e for e in catalog.edits if e.id == ident)
                at = edit.offset - row['file_offset']
                sectors[row['index']][at:at + len(edit.disabled)] = edit.disabled
    for row, block, mask in zip(data['entries'], sectors, shipped.masks()):
        if row['role'] == 'target':
            normalized = bytearray(block)
            for at in range(512):
                if mask[at // 8] & (1 << (at % 8)):
                    normalized[at] = 0
            row['unmanaged_sha256'] = hashlib.sha256(normalized).hexdigest()
        else:
            row['sha256'] = hashlib.sha256(block).hexdigest()
    data['hardware_validation'] = 'synthetic-only'
    return DeviceProfile.from_dict(data, catalog), [bytes(b) for b in sectors]


class ReleaseAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.profile, self.before = synthetic_release()

    def position(self, image, offset):
        row = next(r for r in self.profile.entries if r.get('image') == image
                   and r['file_offset'] <= offset < r['file_offset'] + 512)
        return row['index'], offset - row['file_offset']

    def changed_byte(self, sectors, image, offset, value):
        result = list(sectors)
        index, at = self.position(image, offset)
        block = bytearray(result[index]); block[at] = value; result[index] = bytes(block)
        return result

    def experiment_state(self, enabled):
        sectors = self.before
        for edit in self.profile.catalog.edits:
            if edit.feature in ('pixel_shift', 'dynamic_active'):
                value = edit.enabled if enabled else edit.disabled
                for offset, byte in zip(range(edit.offset, edit.end), value):
                    sectors = self.changed_byte(sectors, edit.image, offset, byte)
        return sectors

    def test_defaults_and_missing_never_select_parked_features_in_any_state(self):
        self.assertEqual(set(self.profile.catalog.available_features), {'ftp', 'shutter', 'exposure', 'sound'})
        for enabled in (False, True):
            current = self.experiment_state(enabled)
            for selection in (None, 'missing'):
                for action in ('unlock', 'close'):
                    with self.subTest(enabled=enabled, selection=selection, action=action):
                        plan, after = inspect_sectors(self.profile, current, action, selection)
                        self.assertTrue(plan['compatible'])
                        self.assertFalse(set(plan['selected_features']) & {'pixel_shift', 'dynamic_active'})
                        for edit in self.profile.catalog.edits:
                            if edit.feature not in self.profile.catalog.available_features:
                                for offset in range(edit.offset, edit.end):
                                    index, at = self.position(edit.image, offset)
                                    self.assertEqual(after[index][at], current[index][at])
                        # A second incremental unlock is a no-op even when parked features are off.
                        if action == 'unlock':
                            repeated, _ = inspect_sectors(self.profile, after, action, 'missing')
                            self.assertEqual(repeated['status'], 'no-op')

    def test_explicit_parked_feature_and_mixed_selection_are_rejected(self):
        for feature in ('pixel_shift', 'dynamic_active'):
            for action in ('unlock', 'close'):
                for selection in ([feature], ['ftp', feature]):
                    with self.assertRaisesRegex(ValueError, '暂未开放'):
                        inspect_sectors(self.profile, self.before, action, selection)

    def test_host_request_rejects_all_parked_bytes_in_both_directions(self):
        from a6700_unlock.device_protocol import request
        recognition = self.profile.masks()
        writable = self.profile.masks(writable_only=True)
        enabled = self.experiment_state(True)
        for edit in self.profile.catalog.edits:
            if edit.feature in ('pixel_shift', 'dynamic_active'):
                for offset in range(edit.offset, edit.end):
                    index, at = self.position(edit.image, offset)
                    self.assertTrue(recognition[index][at // 8] & (1 << (at % 8)))
                    self.assertFalse(writable[index][at // 8] & (1 << (at % 8)))
        for before, after in ((self.before, enabled), (enabled, self.before)):
            with self.assertRaisesRegex(ValueError, 'outside available'):
                request(self.profile, bytes(16), before, after)

    def test_unknown_counter_and_upper_bytes_still_block_other_feature_writes(self):
        for offset, value in ((0x743f53c, 2), (0x743f53d, 1), (0x743f53e, 1), (0x743f53f, 1)):
            current = self.changed_byte(self.before, 'wbi', offset, value)
            plan, after = inspect_sectors(self.profile, current, 'unlock', ['ftp'])
            self.assertFalse(plan['compatible']); self.assertEqual(after, current)

    def test_other_menu_columns_remain_guarded(self):
        for image, gate in (('app', 0x4b7b93a), ('wbi', 0x2495b3a)):
            for delta in (3, 4, 5):
                current = self.changed_byte(self.before, image, gate + delta, 1)
                plan, after = inspect_sectors(self.profile, current, 'unlock', ['ftp'])
                self.assertFalse(plan['compatible']); self.assertEqual(after, current)

    def test_restore_defaults_preserve_parked_fields_and_explicit_restore_rejected(self):
        from a6700_unlock.backup import restore_plan
        from types import SimpleNamespace
        backup = SimpleNamespace(sectors=self.experiment_state(True), id='synthetic',
                                 archive_sha256='0' * 64, created_at='synthetic', manifest={})
        plan, after = restore_plan(self.profile, backup, self.before)
        self.assertEqual(after, self.before)
        self.assertEqual(plan['status'], 'no-op')
        for feature in ('pixel_shift', 'dynamic_active'):
            with self.assertRaisesRegex(ValueError, '暂未开放'):
                restore_plan(self.profile, backup, self.before, [feature])
