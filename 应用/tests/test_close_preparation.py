"""Closing must never remove firmware before selected runtime states settle."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from a6700_unlock.backup import create_backup, load_backup
from a6700_unlock.close_preparation import RULES, prepare_close
from a6700_unlock.device_profile import inspect_sectors
from a6700_unlock.desktop.physical import PhysicalService
from a6700_unlock.desktop.simulation import fixture, MemoryDevice, SimulationService
from a6700_unlock.operations import _execute_session, _restore_session, save_json


class Device(MemoryDevice):
    def __init__(self, profile, sectors, scenario='close-active'):
        super().__init__(profile, sectors, scenario)
        self.observations = 0
        self.firmware_reads = 0

    def read_close_state(self, features, nonce):
        self.observations += 1
        return super().read_close_state(features, nonce)

    def read(self, nonce):
        self.firmware_reads += 1
        return super().read(nonce)


class ClosePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state_patch = patch('a6700_unlock.operations.default_state_dir', return_value=self.root)
        state_patch.start(); self.addCleanup(state_patch.stop)
        self.profile, self.original = fixture()
        _, installed = inspect_sectors(self.profile, self.original, 'unlock')
        self.device = Device(self.profile, installed)

    def close(self, features=None, **kwargs):
        return _execute_session(self.profile, self.device, 'close', self.root,
                                kwargs.pop('progress', lambda _: None),
                                features=features or list(RULES), **kwargs)

    def test_all_settings_stop_save_readback_before_firmware_write(self):
        original_write = self.device.write
        def write(*args):
            self.assertEqual(self.device.close_setting_writes,
                             [('ftp', 'off'), ('sound', 'on'), ('shutter', 'off'), ('exposure', 'pasm')])
            run = next((self.root/'runs').iterdir())
            self.assertTrue((run/'plan-after-close-preparation.json').exists())
            self.assertTrue((run/'close-preparation-recheck.json').exists())
            for index in range(4):
                self.assertTrue((run/('close-setting-%02d-intent.json' % index)).exists())
                self.assertTrue((run/('close-setting-%02d-verified.json' % index)).exists())
            return original_write(*args)
        self.device.write = write
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)
        self.assertTrue(result['settings_write_attempted'])
        self.assertTrue(result['write_attempted'])
        self.assertEqual(result['close_preparation']['completed'], list(RULES))

    def test_only_selected_setting_changes_and_legal_off_is_preserved(self):
        before = deepcopy(self.device.close_settings)
        self.device.close_settings['sound'].update(current='off', saved='off')
        result = self.close(['sound'])
        self.assertTrue(result['verified'], result)
        self.assertEqual(self.device.close_setting_writes, [])
        for key in ('ftp', 'shutter', 'exposure'):
            self.assertEqual(before[key], self.device.close_settings[key])

    def test_both_new_sound_options_return_to_on(self):
        for value in ('electronic-only', 'except-shutter'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                self.device.close_settings['sound'].update(current=value, saved=value)
                report = prepare_close(self.device, ['sound'], Path(folder))
                self.assertTrue(report['ready'], report)
                self.assertEqual(self.device.close_settings['sound']['saved'], 'on')

    def test_last_unknown_item_blocks_first_setting_and_all_firmware(self):
        self.device.close_settings['exposure']['verified'] = False
        result = self.close()
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertFalse(result['settings_write_attempted'])
        self.assertFalse(result['write_attempted'])
        self.assertEqual(self.device.close_setting_writes, [])

    def test_completed_setting_waiting_for_restart_does_not_write_firmware(self):
        from a6700_unlock.close_setting import SettingRestartRequired
        def reset(*_):raise SettingRestartRequired('FTP已保存为关；请重启后继续')
        self.device.reset_close_setting=reset
        result=self.close(['ftp'])
        self.assertEqual(result['status'],'close-preparation-restart-required')
        self.assertTrue(result['close_preparation']['restart_required'])
        self.assertFalse(result['write_attempted'])
        self.assertEqual(self.device.writes,0)

    def test_missing_automatic_capability_blocks_whole_group(self):
        self.device.automatic_close_settings = {'ftp', 'sound', 'shutter'}
        result = self.close()
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertFalse(result['settings_write_attempted'])

    def test_ftp_off_is_not_stopped_while_media_is_owned(self):
        self.device.close_settings['ftp'].update(current='off', saved='off')
        result = self.close(['ftp'])
        self.assertTrue(result['verified'], result)
        self.assertEqual(self.device.close_setting_writes, [('ftp', 'off')])
        self.assertTrue(self.device.close_settings['ftp']['media_released'])

    def test_ftp_unknown_release_is_not_treated_as_off(self):
        self.device.close_settings['ftp'].update(current='off', saved='off', media_released=None)
        result = self.close(['ftp'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertEqual(self.device.close_setting_writes, [])

    def test_recall_and_nonboolean_verification_are_rejected(self):
        for field, value in (('recall_inactive', False), ('recall_inactive', None), ('verified', 1)):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as folder:
                self.device.close_settings['exposure'].update(recall_inactive=True, verified=True)
                self.device.close_settings['exposure'][field] = value
                report = prepare_close(self.device, ['exposure'], Path(folder))
                self.assertFalse(report['ready'])
                self.assertFalse(report['settings_write_attempted'])

    def test_camera_busy_and_mismatched_saved_value_block(self):
        read = self.device.read_close_state
        self.device.read_close_state = lambda ids, nonce: dict(read(ids, nonce), camera_idle=False)
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertEqual(self.device.close_setting_writes, [])

    def test_saved_value_disagreement_blocks_without_guessing(self):
        self.device.close_settings['sound']['saved'] = 'on'
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertEqual(self.device.close_setting_writes, [])

    def test_ack_without_state_change_is_not_success(self):
        self.device.reset_close_setting = lambda *_: None
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-uncertain')
        self.assertTrue(result['settings_write_attempted'])
        self.assertFalse(result['write_attempted'])

    def test_runtime_only_change_without_save_blocks_firmware(self):
        self.device.reset_close_setting = lambda feature, target: self.device.close_settings[feature].update(current=target)
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-uncertain')
        self.assertEqual(self.device.writes, 0)

    def test_lost_ack_is_never_replayed_or_automatically_undone(self):
        reset = self.device.reset_close_setting
        def lost(feature, target):
            reset(feature, target)
            raise OSError('secret=not-for-report')
        self.device.reset_close_setting = lost
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-uncertain')
        self.assertEqual(self.device.close_setting_writes, [('sound', 'on')])
        self.assertNotIn('not-for-report', json.dumps(result))
        self.assertEqual(self.device.writes, 0)

    def test_state_drift_before_first_setter_stops_without_writing(self):
        read = self.device.read_close_state
        def drift(ids, nonce):
            if self.device.observations == 1:
                self.device.close_settings['sound'].update(current='off', saved='off')
            return read(ids, nonce)
        self.device.read_close_state = drift
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertEqual(self.device.close_setting_writes, [])

    def test_setting_drift_during_firmware_reread_blocks_writer(self):
        read = self.device.read
        def drift(nonce):
            if self.device.close_setting_writes:
                self.device.close_settings['sound'].update(current='except-shutter', saved='except-shutter')
            return read(nonce)
        self.device.read = drift
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertTrue(result['settings_write_attempted'])
        self.assertEqual(self.device.writes, 0)

    def test_firmware_guard_drift_after_settings_blocks_writer(self):
        reset = self.device.reset_close_setting
        def drift(feature, target):
            reset(feature, target)
            self.device.sectors[2] = b'X' + self.device.sectors[2][1:]
        self.device.reset_close_setting = drift
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'blocked')
        self.assertTrue(result['settings_write_attempted'])
        self.assertEqual(self.device.writes, 0)

    def test_already_hidden_menu_still_normalizes_runtime(self):
        self.device.sectors = list(self.original)
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'settings-prepared')
        self.assertTrue(result['verified'])
        self.assertFalse(result['write_attempted'])
        self.assertEqual(self.device.close_setting_writes, [('sound', 'on')])

    def test_missing_runtime_adapter_is_not_a_no_op_bypass(self):
        self.device.sectors = list(self.original)
        self.device.read_close_state = None
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertFalse(result['write_attempted'])

    def test_dry_run_plans_without_settings_or_firmware_writes(self):
        result = self.close(dry_run=True)
        self.assertEqual(result['status'], 'dry-run')
        self.assertEqual(result['close_preparation']['status'], 'planned')
        self.assertEqual(self.device.close_setting_writes, [])
        self.assertEqual(self.device.writes, 0)

    def test_bad_firmware_guard_prevents_even_setting_adjustments(self):
        self.device.sectors[2] = bytes([1])*512
        result = self.close()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.device.observations, 0)

    def test_backup_restore_that_disables_sound_prepares_only_sound(self):
        plan, _ = inspect_sectors(self.profile, self.original, 'inspect')
        info = create_backup(self.profile, self.original, plan, self.root/'backups')
        backup = load_backup(Path(info['path']), self.profile)
        result = _restore_session(self.profile, self.device, backup, self.root, lambda _: None, features=['sound'])
        self.assertEqual(result['status'], 'restored', result)
        self.assertEqual(self.device.close_setting_writes, [('sound', 'on')])

    def test_blocked_physical_restore_closes_session_and_releases_lock(self):
        plan, _ = inspect_sectors(self.profile, self.original, 'inspect')
        info = create_backup(self.profile, self.original, plan, self.root/'backups')
        self.device.read_close_state = None
        service = PhysicalService(state_dir=self.root, backup_dir=self.root/'backups',
                                  backend_factory=lambda *_args, **_kwargs: self.device)
        service.profile = self.profile
        with patch('a6700_unlock.operations.host_lock_path', return_value=self.root/'shared.lock'):
            result = service.perform('restore', backup_id=info['id'], features=['sound'])
            self.assertEqual(result['status'], 'close-preparation-required')
            self.assertIsNone(service._session_backend)
            self.assertIsNone(service._session_lock)
            from a6700_unlock.operations import device_lock
            with device_lock(self.root):
                pass
        self.assertEqual(self.device.writes, 0)

    def test_cancellation_after_first_prepared_item_preserves_truthful_scope(self):
        def progress(message):
            if '正在将关机快门帘' in message:
                raise KeyboardInterrupt()
        result = self.close(['sound', 'shutter'], progress=progress)
        self.assertEqual(result['status'], 'close-preparation-cancelled')
        self.assertEqual(result['close_preparation']['completed'], ['sound'])
        self.assertTrue(result['settings_write_attempted'])
        self.assertFalse(result['write_attempted'])

    def test_journal_failure_prevents_setting_write(self):
        def save(path, data):
            if path.name.endswith('-intent.json') and path.name.startswith('close-setting-'):
                raise OSError('disk full')
            save_json(path, data)
        with patch('a6700_unlock.operations.save_json', side_effect=save):
            result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertEqual(self.device.close_setting_writes, [])

    def test_report_excludes_unrelated_credentials(self):
        read = self.device.read_close_state
        def with_secret(ids, nonce):
            state = read(ids, nonce)
            state['password'] = 'never-in-report'
            state['features']['ftp']['server'] = 'never-in-report'
            return state
        self.device.read_close_state = with_secret
        result = self.close(['ftp'])
        self.assertTrue(result['verified'])
        self.assertNotIn('never-in-report', json.dumps(result))
        for path in self.root.rglob('*.json'):
            self.assertNotIn('never-in-report', path.read_text())

    def test_reset_side_effect_on_another_selected_setting_blocks_firmware(self):
        self.device.close_settings['shutter'].update(current='off', saved='off')
        reset = self.device.reset_close_setting
        def unexpected(feature, target):
            reset(feature, target)
            self.device.close_settings['shutter'].update(current='on', saved='on')
        self.device.reset_close_setting = unexpected
        result = self.close(['sound', 'shutter'])
        self.assertEqual(result['status'], 'close-preparation-uncertain')
        self.assertEqual(self.device.writes, 0)

    def test_cached_state_cannot_satisfy_a_new_read_request(self):
        old = self.device.read_close_state(['sound'], 'previous-request')
        self.device.read_close_state = lambda *_: old
        result = self.close(['sound'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertEqual(self.device.close_setting_writes, [])
        self.assertEqual(self.device.writes, 0)

    def test_desktop_result_discloses_settings_only_and_invalidates_old_preparation(self):
        from a6700_unlock.desktop.controller import DesktopController
        controller = DesktopController(self.root, environment_provider=lambda: dict(ready=True, checks=[]))
        controller.reset('original')
        controller.check_environment()
        controller.acknowledge_notice(True)
        controller.begin_preparation()
        controller.confirm_preparation(True)
        controller.operate('inspect')
        controller.select_features('none')
        controller.set_feature_selection('sound', True)
        controller.service.backend.close_settings['sound'].update(current='except-shutter', saved='except-shutter')
        state = controller.operate('close')
        self.assertEqual(state['result']['status'], 'settings-prepared')
        self.assertTrue(state['result']['success'])
        self.assertEqual(state['result']['close_preparation']['completed'], ['sound'])
        self.assertTrue(state['result']['aftercare']['restart_required'])
        self.assertFalse(state['compatible'])
        self.assertFalse(state['ready'])

    def test_simulation_keeps_actual_device_write_flags_false(self):
        service = SimulationService(self.root, 'close-active')
        result = service.perform('close', features=['sound'])
        self.assertTrue(result['verified'])
        self.assertTrue(result['simulated_settings_write_attempted'])
        self.assertFalse(result['settings_write_attempted'])
        self.assertFalse(result['write_attempted'])
        self.assertFalse(result['device_access'])

    def test_mixed_close_checks_fl23_before_any_setting_or_firmware_change(self):
        service = PhysicalService(state_dir=self.root, backend_factory=lambda *_args, **_kwargs: self.device)
        service.profile = self.profile
        service.fl23_available = True
        with patch('a6700_unlock.operations.host_lock_path', return_value=self.root/'shared.lock'), \
             patch('a6700_unlock.fl23_package.perform', return_value=dict(status='error', error='slot references FL3')) as package:
            result = service.perform('close', features=['sound', 'fl23'])
        self.assertEqual(result['status'], 'close-preparation-required')
        self.assertEqual(self.device.close_setting_writes, [])
        self.assertEqual(self.device.writes, 0)
        self.assertTrue(package.call_args.kwargs['validate_close'])

    def test_package_no_op_still_rejects_hidden_extended_reference(self):
        from types import SimpleNamespace
        from a6700_unlock.fl23_package import perform
        @contextmanager
        def files(*_):
            yield None
        inspected = (dict(id='fl23', state='disabled'),
                     dict(flags=0, startup='disabled', cp='disabled', names_state='disabled'),
                     [], [], dict(present=False, owned_mask=0), 17)
        with patch('a6700_unlock.fl23_package.service_files', files), \
             patch('a6700_unlock.fl23_package.inspect', return_value=inspected), \
             patch('a6700_unlock.fl23_package.Session', return_value=SimpleNamespace(ap_values=lambda: {'0x2cf036f':'11'})):
            result = perform(object(), 'close', self.root, features=['fl23'])
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['write_attempted'])


if __name__ == '__main__':
    unittest.main()
