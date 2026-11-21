import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from a6700_unlock.catalog import load_catalog
from a6700_unlock.desktop.controller import DesktopController
from a6700_unlock.desktop.protocol import capabilities
from a6700_unlock.feature_updates import revision_status
from a6700_unlock.preparation import STEP_IDS


class FtpUpdateTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.feature = next(f for f in self.catalog.data['features'] if f['id'] == 'ftp')
        self.edits = [e for e in self.catalog.edits if e.feature == 'ftp']
        self.enabled = {e.id: 'enabled' for e in self.edits}

    def test_complete_capability_set_is_current_revision(self):
        row = revision_status(self.feature, self.catalog.edits, self.enabled)
        self.assertEqual(row['state'], 'enabled')
        self.assertEqual(row['installed_revision'], 2)
        self.assertEqual(row['target_revision'], 2)
        self.assertEqual(row['update_state'], 'current')
        self.assertFalse(row['update_available'])

    def test_old_incomplete_capability_set_offers_update(self):
        states = dict(self.enabled)
        for edit_id in self.feature['updates'][0]['edit_ids']:
            states[edit_id] = 'disabled'
        row = revision_status(self.feature, self.catalog.edits, states)
        self.assertEqual(row['base_state'], 'enabled')
        self.assertEqual(row['installed_revision'], 1)
        self.assertEqual(row['update_state'], 'available')
        self.assertTrue(row['update_available'])
        self.assertEqual(set(row['update_edit_ids']), set(self.feature['updates'][0]['edit_ids']))

    def test_uninstalled_uses_normal_unlock_not_update(self):
        states = {e.id: 'disabled' for e in self.edits}
        row = revision_status(self.feature, self.catalog.edits, states)
        self.assertEqual(row['state'], 'disabled')
        self.assertEqual(row['installed_revision'], 0)
        self.assertEqual(row['update_state'], 'uninstalled')
        self.assertFalse(row['update_available'])

    def test_verified_revision_plan_changes_only_playback_key_fields(self):
        import inspect_ftp_update as target
        from a6700_unlock.device_protocol import response
        from a6700_unlock.feature_updates import plan_update
        from unittest.mock import patch
        import a6700_unlock.device_profile as profile_module
        from a6700_unlock.catalog import load_catalog as load_release_catalog
        from a6700_unlock.device_profile import load_device_profile as load_release_profile
        profile = load_release_profile(load_release_catalog())
        volatile = json.loads((target.ROOT/'release/src/a6700_unlock/data/guard-volatile.json').read_text())
        descriptor = json.loads((target.ROOT/'output/运行/FTP/远程验收/post-reboot-baseline.json').read_bytes())
        run = Path(descriptor['run_directory'])
        if target.sha((run/'result.json').read_bytes()) != descriptor['result_sha256']:
            raise ValueError('Baseline evidence changed')
        config = (run/'request-1.bin').read_bytes()
        before = response((run/'reply-1.bin').read_bytes(), config[8:24], len(profile.entries))['sectors']
        with patch.object(profile_module, '_guard_volatile_data', volatile):
            plan, after = plan_update(profile, before, 'ftp')
        self.assertEqual(plan['changed_bytes'], 1)
        self.assertEqual(plan['requested_action'], 'update')
        self.assertEqual(plan['from_revision'], 1)
        self.assertEqual(plan['target_revision'], 2)
        changed = [(row['device'], row['device_offset'])
                   for row, old, new in zip(profile.entries, before, after) if old != new]
        self.assertEqual(changed, [(0, 228334592)])
        with patch.object(profile_module, '_guard_volatile_data', volatile):
            final, _ = profile_module.inspect_sectors(profile, after)
        self.assertEqual(final['features'][0]['update_state'], 'current')

    def test_full_unlock_includes_0839_and_excludes_old_two_byte_fix(self):
        import inspect_ftp_update as target
        from a6700_unlock.device_protocol import response
        from a6700_unlock.device_profile import inspect_sectors
        from unittest.mock import patch
        import a6700_unlock.device_profile as profile_module
        from a6700_unlock.catalog import load_catalog as load_release_catalog
        from a6700_unlock.device_profile import load_device_profile as load_release_profile
        profile = load_release_profile(load_release_catalog())
        descriptor = json.loads((target.ROOT/'output/运行/FTP/正确解锁候选/实机验收/full-unlock-baseline.json').read_bytes())
        run = Path(descriptor['run_directory'])
        if target.sha((run/'result.json').read_bytes()) != descriptor['result_sha256']:
            raise ValueError('Full-unlock evidence changed')
        config = (run/descriptor['request_path']).read_bytes()
        before = response((run/descriptor['reply_path']).read_bytes(), config[8:24], len(profile.entries))['sectors']
        with patch.object(profile_module, '_guard_volatile_data',
                          json.loads((target.ROOT/'release/src/a6700_unlock/data/guard-volatile.json').read_text())):
            plan, after = inspect_sectors(profile, before, 'unlock', features=['ftp'])
        self.assertEqual(plan['changed_bytes'], 241)
        self.assertEqual(plan['changed_sectors'], 83)
        self.assertEqual(after[260][0x1f4], 1)
        self.assertEqual(after[260][0xe4], 0)
        self.assertEqual(after[261][0x12c], 0)

    def test_update_command_targets_only_ftp(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(folder)
            controller.reset('enabled')
            controller.check_environment()
            controller.acknowledge_notice(True)
            controller.begin_preparation()
            for ident in STEP_IDS:
                controller.confirm(ident, True)
            controller.operate('inspect')
            row = next(row for row in controller.features if row['id'] == 'ftp')
            with self.assertRaises(ValueError):
                controller.reserve_ftp_update()
            row.update(update_available=True, installed_revision=1, target_revision=2)
            controller.select_features('all')
            controller.reserve_ftp_update()
            self.assertEqual(controller.selected_features, {'ftp'})
            self.assertEqual(controller.action, 'unlock')
            self.assertIn('update_ftp', capabilities('physical')['commands'])
            self.assertIn('update_ftp', capabilities('simulation')['commands'])


def _has_repo_ftp_evidence():
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "scripts/inspect_ftp_update.py").is_file():
        return False
    for rel in (
        "output/运行/FTP/远程验收/post-reboot-baseline.json",
        "output/运行/FTP/正确解锁候选/实机验收/full-unlock-baseline.json",
    ):
        path = repo_root / rel
        if not path.is_file():
            return False
        descriptor = json.loads(path.read_bytes())
        if not Path(descriptor.get("run_directory", "")).is_dir():
            return False
    return True


def load_tests(loader, tests, pattern):
    # Distinct repo/standalone bundles, not skipped or silently passing tests.
    names = [
        "test_complete_capability_set_is_current_revision",
        "test_old_incomplete_capability_set_offers_update",
        "test_uninstalled_uses_normal_unlock_not_update",
        "test_update_command_targets_only_ftp",
    ]
    if _has_repo_ftp_evidence():
        names.extend([
            "test_verified_revision_plan_changes_only_playback_key_fields",
            "test_full_unlock_includes_0839_and_excludes_old_two_byte_fix",
        ])
    return unittest.TestSuite(FtpUpdateTests(name) for name in names)


if __name__ == '__main__':
    unittest.main()
