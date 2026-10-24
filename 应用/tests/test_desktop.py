"""Desktop state/transport acceptance without a screen, camera, or USB driver."""
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest
from unittest.mock import patch

import a6700_unlock
from a6700_unlock.desktop.controller import DesktopController, physical_environment
from a6700_unlock.desktop.physical import PhysicalService
from a6700_unlock.preparation import STEP_IDS
from test_operations import sparse_fixture
from platform_lock import lock_file
from platform_lock import lock_file


ENV = dict(ready=True, checks=[dict(id='synthetic', name='Test', ok=True,
                                   detail='Synthetic environment', remediation='')], device_access=False)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = DesktopController(self.temp.name, environment_provider=lambda: ENV)

    def test_physical_diagnostics_are_normalized_for_native_snapshot_decoder(self):
        diagnostic = dict(ready=True, device_access=False, checks=[
            dict(id='payload-integrity', name='载荷', ok=True, detail={'reader': 42}, remediation=''),
            dict(id='future-component', name='组件', ok=False, detail={'reason': 'missing'}, remediation='重新安装'),
            dict(id='python', name='Python', ok=True, detail='3.13', remediation=''),
        ])
        with patch('a6700_unlock.doctor.diagnose', return_value=diagnostic):
            environment = physical_environment()
        self.assertEqual(environment['checks'][0]['detail'], '清单、源码与载荷哈希一致')
        self.assertEqual(environment['checks'][1]['detail'], '检查未通过')
        self.assertEqual(environment['checks'][2]['detail'], '3.13')
        self.assertTrue(all(isinstance(row['detail'], str) for row in environment['checks']))
        self.assertEqual(diagnostic['checks'][0]['detail'], {'reader': 42})
        self.assertFalse(environment['device_access'])

    def prepare(self, scenario='enabled'):
        self.controller.reset(scenario)
        self.controller.check_environment()
        self.controller.acknowledge_notice(True)
        self.controller.begin_preparation()
        for ident in STEP_IDS:
            self.controller.confirm(ident, True)

    def inspect(self, scenario='enabled'):
        self.prepare(scenario)
        return self.controller.operate('inspect')

    def resume(self):
        # Reconnect without resetting the simulated device's actual state.
        self.controller.restart_preparation()
        self.controller.confirm_preparation(True)
        self.controller.operate('inspect')
        self.controller.select_features('all')

    def test_environment_and_all_manual_steps_gate_device_preflight(self):
        with self.assertRaises(ValueError):
            self.controller.reserve('inspect')
        self.controller.check_environment(); self.controller.acknowledge_notice(True)
        self.controller.begin_preparation()
        for ident in STEP_IDS[:-1]:
            self.controller.confirm(ident, True)
        with self.assertRaises(ValueError):
            self.controller.reserve('inspect')
        self.controller.confirm(STEP_IDS[-1], True)
        self.assertTrue(self.controller.snapshot()['ready'])
        with self.assertRaises(ValueError):
            self.controller.reserve('unlock')

    def test_single_preparation_declaration_is_explicit_gated_and_revocable(self):
        with self.assertRaises(ValueError): self.controller.confirm_preparation(True)
        self.controller.check_environment()
        with self.assertRaises(ValueError): self.controller.confirm_preparation(True)
        self.controller.acknowledge_notice(True)
        self.controller.begin_preparation()
        for value in ('yes', 1, None):
            with self.assertRaises(ValueError): self.controller.confirm_preparation(value)
        state = self.controller.confirm_preparation(True)
        self.assertTrue(state['ready'])
        self.assertEqual({row['id'] for row in state['steps'] if row['confirmed']}, set(STEP_IDS))
        self.assertFalse(state['device_access'])
        self.controller.confirm_preparation(False)
        with self.assertRaises(ValueError): self.controller.reserve('inspect')
        self.controller.confirm_preparation(True)
        self.controller.reserve('inspect')
        with self.assertRaises(ValueError): self.controller.confirm_preparation(True)
        with self.assertRaises(ValueError): self.controller.restart_preparation()
        self.controller.cancel_operation(); self.controller.run_reserved()
        self.assertEqual(self.controller.service.backend.writes, 0)

    def test_reconnect_navigation_invalidates_selection_gate_but_keeps_reading_acknowledgement(self):
        self.inspect('partial')
        state = self.controller.restart_preparation()
        self.assertEqual(state['phase'], 'preparation')
        self.assertTrue(state['notice_acknowledged'])
        self.assertFalse(state['ready']); self.assertFalse(state['compatible'])
        self.assertEqual(state['features'], [])
        self.assertIsNone(state['inspect_summary'])
        with self.assertRaises(ValueError): self.controller.reserve('unlock')
        self.controller.confirm_preparation(True)
        with self.assertRaises(ValueError): self.controller.reserve('unlock')
        self.assertEqual(self.controller.operate('inspect')['phase'], 'home')

    def test_select_closable_features_and_dependency_metadata_match_catalog(self):
        self.inspect('partial')
        state = self.controller.select_features('enabled')
        self.assertEqual({f['id'] for f in state['features'] if f['selected']}, {'ftp'})
        catalog = self.controller.service.profile.catalog.data['features']
        for row in state['features']:
            expected = next(f for f in catalog if f['id'] == row['id']).get('requires', [])
            self.assertEqual(row['requires'], expected)

    def test_simulated_environment_failure_cannot_be_confirmed_away(self):
        self.controller.reset('environment-missing')
        state = self.controller.check_environment()
        self.assertFalse(state['environment']['ready'])
        self.controller.acknowledge_notice(True)
        with self.assertRaises(ValueError):
            self.controller.begin_preparation()
        self.assertEqual(self.controller.service.backend.writes, 0)

    def test_normal_simulation_never_uses_usb_backend_or_physical_device_lock(self):
        with patch('a6700_unlock.operations.device_lock', side_effect=AssertionError('physical lock')), \
             patch('a6700_unlock.usb.backend.UsbBackend', side_effect=AssertionError('USB constructed')):
            state = self.inspect('original')
            self.assertEqual(state['phase'], 'home')
            result = self.controller.operate('unlock')
            self.assertTrue(result['result']['success'])
        report = self.controller.last_report
        self.assertFalse(report['device_access'])
        self.assertFalse(report['write_attempted'])
        self.assertTrue(report['simulated_write_attempted'])
        self.assertEqual(report['plan']['hardware_validation'], 'synthetic-only')

    def test_inspect_shows_summary_and_defaults_to_missing_features(self):
        state = self.inspect('original')
        self.assertIsNotNone(state['inspect_summary'])
        self.assertEqual(state['inspect_summary']['read_sectors'], len(self.controller.service.profile.entries))
        self.assertEqual({row['id']: row['selected'] for row in state['features']},
                         {'ftp': True, 'shutter': True, 'exposure': True, 'sound': True, 'pixel_shift': False, 'dynamic_active': False})
        state = self.controller.acknowledge_check()
        self.assertIsNone(state['inspect_summary'])
        self.assertEqual(state['phase'], 'home')
        self.inspect('enabled')
        self.controller.acknowledge_check()
        self.assertEqual({row['id']: row['selected'] for row in self.controller.snapshot()['features']},
                         {'ftp': False, 'shutter': False, 'exposure': False, 'sound': False, 'pixel_shift': False, 'dynamic_active': False})

    def test_close_unlock_and_repeat_reuse_real_transaction_and_planner(self):
        state = self.inspect()
        self.assertEqual({row['id']: row['selected'] for row in state['features']},
                         {'ftp': False, 'shutter': False, 'exposure': False, 'sound': False, 'pixel_shift': False, 'dynamic_active': False})
        self.controller.acknowledge_check()
        self.controller.select_features('all')
        self.assertEqual(self.controller.operate('close')['result']['status'], 'completed')
        self.resume()
        self.assertEqual(self.controller.operate('unlock')['result']['status'], 'completed')
        writes = self.controller.service.backend.writes
        self.resume()
        self.assertEqual(self.controller.operate('unlock')['result']['status'], 'no-op')
        self.assertEqual(self.controller.service.backend.writes, writes)
        self.assertEqual({f['state'] for f in self.controller.snapshot()['features'] if f['available']}, {'enabled'})

    def test_partial_state_only_completes_missing_synthetic_fields(self):
        state = self.inspect('partial')
        self.assertEqual({f['id']: f['state'] for f in state['features']}, {'ftp': 'enabled', 'shutter': 'disabled', 'exposure': 'disabled', 'sound': 'disabled', 'pixel_shift': 'disabled', 'dynamic_active': 'disabled'})
        self.controller.operate('unlock')
        self.assertEqual(self.controller.last_report['plan']['changed_bytes'], 6)

    def test_success_stays_on_result_and_requires_fresh_preparation_and_inspection(self):
        self.inspect('original')
        self.controller.select_features('none')
        self.controller.set_feature_selection('ftp', True)
        state = self.controller.operate('unlock')
        self.assertEqual(state['phase'], 'result')
        self.assertTrue(state['result']['success'])
        self.assertFalse(state['compatible']); self.assertFalse(state['ready'])
        guide = state['result']['aftercare']
        self.assertTrue(guide['simulation']); self.assertTrue(guide['restart_required'])
        self.assertEqual([row['id'] for row in guide['features']], ['ftp'])
        self.assertIn('服务器设置', guide['features'][0]['expected'])
        self.assertEqual([row['id'] for row in guide['steps']],
                         ['off', 'usb-out', 'battery-out', 'wait', 'battery-in', 'on'])
        self.assertIn('10 秒', guide['steps'][3]['title'])
        self.assertIn('USB', guide['steps'][1]['detail'])
        for action in ('unlock', 'close', 'inspect'):
            with self.assertRaises(ValueError): self.controller.reserve(action)
        with self.assertRaises(ValueError): self.controller.home()
        with self.assertRaises(ValueError): self.controller.select_features('all')
        writes = self.controller.service.backend.writes
        state = self.controller.restart_preparation()
        self.assertIsNone(state['result'])
        self.assertEqual(self.controller.service.backend.writes, writes)
        self.controller.confirm_preparation(True)
        with self.assertRaises(ValueError): self.controller.reserve('unlock')
        state = self.controller.operate('inspect')
        self.assertEqual(next(f['state'] for f in state['features'] if f['id'] == 'ftp'), 'enabled')

    def test_aftercare_distinguishes_skipped_features_no_op_and_close(self):
        self.inspect('partial')
        self.controller.select_features('all')
        guide = self.controller.operate('unlock')['result']['aftercare']
        checks = {row['id']: row for row in guide['features']}
        self.assertEqual(set(checks), {'ftp', 'shutter', 'exposure', 'sound'})
        self.assertEqual(list(checks), ['ftp', 'shutter', 'exposure', 'sound'])
        self.assertIn('未重复写入', checks['ftp']['outcome'])
        self.assertIn('回读通过', checks['sound']['outcome'])
        self.resume()
        writes = self.controller.service.backend.writes
        state = self.controller.operate('unlock')
        self.assertEqual(state['result']['status'], 'no-op')
        self.assertFalse(state['result']['aftercare']['restart_required'])
        self.assertTrue(all('未重复写入' in f['outcome'] for f in state['result']['aftercare']['features']))
        self.assertEqual(self.controller.service.backend.writes, writes)
        self.resume()
        self.controller.select_features('none')
        self.controller.set_feature_selection('sound', True)
        state = self.controller.operate('close')
        checks = state['result']['aftercare']['features']
        self.assertEqual(len(checks), 1)
        self.assertIn('仅保留原有', checks[0]['expected'])
        self.assertIn('已关闭', checks[0]['outcome'])
        self.assertNotIn('试听', checks[0]['note'])

    def test_incompatible_or_missing_device_does_not_enable_operations(self):
        for scenario, status in [('incompatible', 'blocked'), ('disconnected', 'error')]:
            with self.subTest(scenario=scenario):
                state = self.inspect(scenario)
                self.assertFalse(state['compatible'])
                self.assertEqual(state['result']['status'], status)
                self.assertEqual(self.controller.service.backend.writes, 0)
                self.assertFalse(state['ready'])

    def test_communication_verification_and_rollback_results_are_distinct(self):
        for scenario, expected in [('communication', 'uncertain'), ('verification', 'verification-failed'), ('rollback', 'rolled-back')]:
            with self.subTest(scenario=scenario):
                self.inspect(scenario)
                state = self.controller.operate('unlock')
                self.assertEqual(state['result']['status'], expected)
                self.assertIsNone(state['result']['aftercare'])
                self.assertFalse(state['compatible'])
                self.assertFalse(state['ready'])
                self.assertEqual(self.controller.service.backend.writes, 1)

    def test_disconnect_and_reconnect_invalidate_previous_preparation_and_compatibility(self):
        self.inspect()
        state = self.controller.disconnect()
        self.assertFalse(state['ready']); self.assertFalse(state['compatible'])
        self.assertEqual(state['features'], [])
        state = self.controller.reconnect()
        self.assertFalse(state['ready']); self.assertFalse(state['compatible'])
        with self.assertRaises(ValueError):
            self.controller.reserve('unlock')

    def test_switching_scenario_invalidates_old_state(self):
        self.inspect()
        self.controller.reset('original')
        state = self.controller.snapshot()
        self.assertFalse(state['checked']); self.assertFalse(state['ready'])
        self.assertFalse(state['compatible'])
        self.assertIsNone(self.controller.last_report)

    def test_busy_state_rejects_duplicate_or_reset_and_cancel_does_not_write(self):
        self.inspect('original')
        self.controller.reserve('unlock')
        with self.assertRaises(ValueError): self.controller.reserve('close')
        with self.assertRaises(ValueError): self.controller.reset('enabled')
        self.controller.cancel_operation()
        state = self.controller.run_reserved()
        self.assertEqual(state['result']['status'], 'cancelled')
        self.assertEqual(self.controller.service.backend.writes, 0)
        self.assertFalse(state['busy'])
        self.assertFalse(state['compatible'])

    def test_cancel_after_simulated_write_is_uncertain_and_never_retries(self):
        self.inspect('original')
        def cancel_after_write(message):
            if '独立回读修改目标' in message:
                self.controller.cancel_operation()
        state = self.controller.operate('unlock', cancel_after_write)
        self.assertEqual(state['result']['status'], 'uncertain')
        self.assertEqual(self.controller.service.backend.writes, 1)
        self.assertFalse(state['ready'])
        with self.assertRaises(ValueError): self.controller.reserve('unlock')

    def test_simulated_backup_and_restore_roundtrip(self):
        self.inspect('original')
        state = self.controller.operate('backup')
        self.assertEqual(state['result']['status'], 'backup-completed')
        self.assertTrue(state['result']['success'])
        backup_id = state['result']['backup_id']
        self.assertTrue(backup_id.endswith('.a6700backup'))
        self.assertTrue(Path(state['result']['backup_path']).is_file())
        self.assertEqual([row['id'] for row in state['backups']], [backup_id])
        self.controller.home()
        self.assertEqual(self.controller.operate('unlock')['result']['status'], 'completed')
        self.resume()
        state = self.controller.operate('restore', backup_id=backup_id)
        self.assertEqual(state['result']['status'], 'restored')
        self.assertTrue(state['result']['success'])
        self.assertEqual({row['id']: row['state'] for row in state['features']},
                         {'ftp': 'disabled', 'shutter': 'disabled', 'exposure': 'disabled', 'sound': 'disabled', 'pixel_shift': 'disabled', 'dynamic_active': 'disabled'})
        self.assertEqual(self.controller.last_report['backup_id'], backup_id)
        self.assertFalse(self.controller.last_report['device_access'])
        self.assertFalse(self.controller.last_report['write_attempted'])
        self.assertTrue(self.controller.last_report['simulated_write_attempted'])

    def test_feature_selection_and_select_missing_control_unlock_scope(self):
        self.inspect('original')
        state = self.controller.snapshot()
        self.assertEqual({row['id']: row['selected'] for row in state['features']},
                         {'ftp': True, 'shutter': True, 'exposure': True, 'sound': True, 'pixel_shift': False, 'dynamic_active': False})
        state = self.controller.select_features('none')
        self.assertEqual({row['id']: row['selected'] for row in state['features']},
                         {'ftp': False, 'shutter': False, 'exposure': False, 'sound': False, 'pixel_shift': False, 'dynamic_active': False})
        with self.assertRaises(ValueError):
            self.controller.reserve('unlock')
        state = self.controller.select_features('missing')
        self.assertEqual({row['id']: row['selected'] for row in state['features']},
                         {'ftp': True, 'shutter': True, 'exposure': True, 'sound': True, 'pixel_shift': False, 'dynamic_active': False})
        state = self.controller.set_feature_selection('shutter', False)
        self.assertEqual({row['id']: row['selected'] for row in state['features']},
                         {'ftp': True, 'shutter': False, 'exposure': True, 'sound': True, 'pixel_shift': False, 'dynamic_active': False})
        result = self.controller.operate('unlock')
        self.assertEqual(result['result']['status'], 'completed')
        self.assertEqual(result['result']['success'], True)
        self.assertEqual({row['id']: row['state'] for row in result['features']},
                         {'ftp': 'enabled', 'shutter': 'disabled', 'exposure': 'enabled', 'sound': 'enabled', 'pixel_shift': 'disabled', 'dynamic_active': 'disabled'})
        self.assertEqual(self.controller.last_report['plan']['selected_features'], ['ftp', 'exposure', 'sound'])
        self.resume()
        self.controller.select_features('all')
        self.assertEqual({row['id']: row['selected'] for row in self.controller.snapshot()['features']},
                         {'ftp': True, 'shutter': True, 'exposure': True, 'sound': True, 'pixel_shift': False, 'dynamic_active': False})

    def test_restore_selection_only_restores_selected_features(self):
        self.inspect('original')
        state = self.controller.operate('backup')
        backup_id = state['result']['backup_id']
        self.controller.home()
        self.controller.operate('unlock')
        self.resume()
        self.controller.select_features('none')
        self.controller.set_feature_selection('ftp', True)
        state = self.controller.operate('restore', backup_id=backup_id)
        self.assertEqual(state['result']['status'], 'restored')
        self.assertEqual({row['id']: row['state'] for row in state['features']},
                         {'ftp': 'disabled', 'shutter': 'enabled', 'exposure': 'enabled', 'sound': 'enabled', 'pixel_shift': 'disabled', 'dynamic_active': 'disabled'})
        self.assertEqual(self.controller.last_report['plan']['selected_features'], ['ftp'])

    def test_parked_features_cannot_be_selected_or_injected(self):
        self.inspect('original')
        for feature in ('pixel_shift', 'dynamic_active'):
            with self.assertRaises(ValueError):
                self.controller.set_feature_selection(feature, True)
            self.controller.selected_features.add(feature)
            with self.assertRaisesRegex(ValueError, '暂未开放'):
                self.controller.reserve('unlock')
            self.controller.selected_features.discard(feature)
        for mode in ('all', 'missing', 'enabled'):
            state = self.controller.select_features(mode)
            self.assertFalse(any(f['selected'] for f in state['features'] if not f['available']))

    def test_sound_can_be_selected_without_exposure(self):
        self.inspect('original')
        self.controller.select_features('none')
        self.controller.set_feature_selection('sound', True)
        state = self.controller.operate('unlock')
        self.assertEqual(state['result']['status'], 'completed')
        self.assertEqual(self.controller.last_report['plan']['selected_features'], ['sound'])
        self.assertTrue(self.controller.last_report['simulated_write_attempted'])
        states = {row['id']: row['state'] for row in state['features']}
        self.assertEqual(states['sound'], 'enabled')
        self.assertEqual(states['exposure'], 'disabled')
        self.assertEqual(states['ftp'], 'disabled')
        self.assertEqual(states['shutter'], 'disabled')

    def test_unknown_commands_and_unselected_restore_are_rejected(self):
        self.inspect()
        for action in ('full-backup', 'real-write', '/dev/nflasha5'):
            with self.assertRaises(ValueError): self.controller.reserve(action)
        with self.assertRaises(ValueError): self.controller.reserve('restore')

    def test_json_worker_handles_preparation_and_rejects_physical_commands(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(a6700_unlock.__file__).resolve().parent.parent)
        commands = [dict(command='reset', scenario='partial'), dict(command='acknowledge', value=True), dict(command='prepare')]
        commands += [dict(command='confirm', id=ident, value=True) for ident in STEP_IDS]
        commands += [dict(command='real-write', backend='usb', path='/dev/nflasha5')]
        result = subprocess.run([sys.executable, '-m', 'a6700_unlock.desktop.bridge'],
            input=''.join(json.dumps(command)+'\n' for command in commands),
            env=env, cwd=self.temp.name, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        states = [event['state'] for event in messages if event['type'] == 'state']
        self.assertTrue(states[-1]['ready'])
        self.assertTrue(all(not state['device_access'] and not state['write_attempted'] for state in states))
        self.assertEqual(messages[-1]['type'], 'error')

    def test_json_worker_remains_responsive_to_cancel_during_progress(self):
        env = dict(os.environ, PYTHONPATH=str(Path(a6700_unlock.__file__).resolve().parent.parent))
        worker = subprocess.Popen([sys.executable, '-u', '-m', 'a6700_unlock.desktop.bridge'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=self.temp.name)
        events = Queue()
        def read_events():
            for line in worker.stdout:
                events.put(json.loads(line))
        Thread(target=read_events, daemon=True).start()
        def send(command, **extra):
            worker.stdin.write(json.dumps(dict(command=command, **extra)) + '\n')
            worker.stdin.flush()
        def until(predicate):
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                try:
                    event = events.get(timeout=max(0.01, deadline - time.monotonic()))
                except Empty:
                    break
                if predicate(event):
                    return event
            self.fail('Timed out waiting for simulator state')
        try:
            send('reset', scenario='original'); send('acknowledge', value=True); send('prepare')
            for ident in STEP_IDS:
                send('confirm', id=ident, value=True)
            send('inspect')
            until(lambda e: e['type'] == 'state' and e['state']['phase'] == 'home')
            send('unlock')
            running = until(lambda e: e['type'] == 'state' and e['state']['busy'])
            self.assertEqual(running['state']['phase'], 'running')
            until(lambda e: e['type'] == 'progress')
            send('cancel')
            result = until(lambda e: e['type'] == 'state' and e['state']['phase'] == 'result')['state']
            self.assertEqual(result['result']['status'], 'cancelled')
            self.assertFalse(result['busy']); self.assertFalse(result['ready'])
            self.assertTrue(result['simulation'])
            self.assertFalse(result['device_access']); self.assertFalse(result['write_attempted'])
        finally:
            worker.stdin.close()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.terminate(); worker.wait(timeout=5)
            worker.stdout.close(); worker.stderr.close()

    def test_notice_acknowledgement_is_required_beyond_successful_startup_checks(self):
        state = self.controller.check_environment()
        self.assertEqual(state['phase'], 'notice')
        self.assertFalse(state['notice_acknowledged'])
        with self.assertRaises(ValueError): self.controller.begin_preparation()
        for invalid in (1, 'true', None):
            with self.assertRaises(ValueError): self.controller.acknowledge_notice(invalid)
        self.controller.acknowledge_notice(True)
        self.assertEqual(self.controller.begin_preparation()['phase'], 'preparation')
        for ident in STEP_IDS: self.controller.confirm(ident, True)
        self.assertTrue(self.controller.snapshot()['ready'])
        self.controller.cancel_operation()
        self.controller.acknowledge_notice(False)
        self.assertFalse(self.controller.snapshot()['ready'])
        with self.assertRaises(ValueError): self.controller.reserve('inspect')
        self.assertEqual(self.controller.service.backend.writes, 0)

    def test_reset_and_reconnect_cannot_bypass_unread_notice_or_failed_startup(self):
        self.prepare()
        self.controller.reset('original')
        self.controller.check_environment()
        self.assertFalse(self.controller.snapshot()['notice_acknowledged'])
        self.controller.disconnect()
        self.assertEqual(self.controller.reconnect()['phase'], 'notice')
        self.controller.acknowledge_notice(True)
        self.controller.reset('environment-missing')
        self.controller.check_environment()
        self.controller.acknowledge_notice(True)
        self.assertEqual(self.controller.reconnect()['phase'], 'notice')
        with self.assertRaises(ValueError): self.controller.begin_preparation()
        self.assertFalse(self.controller.snapshot()['device_access'])


class PhysicalDesktopTests(unittest.TestCase):
    class Backend:
        def __init__(self, sectors):
            self.sectors = list(sectors)
            self.closed = False
            self.cleanup_warning = None
            self.cleanup_status = None
        def __enter__(self): return self
        def __exit__(self, *args): self.closed = True
        def read(self, nonce): return list(self.sectors)
        def write(self, before, after, nonce):
            self.sectors = list(after)
            return dict(status=0, error=None, touched=sum(a != b for a, b in zip(before, after)),
                        rolled_back=False, failed_index=None, sectors=[])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.profile, self.sectors = sparse_fixture()
        self.backend = None
        lock_patch = patch('a6700_unlock.operations.host_lock_path',
                           return_value=self.directory / 'host/.assistant.lock')
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        catalog_patch = patch('a6700_unlock.desktop.physical.load_catalog',
                              return_value=self.profile.catalog)
        profile_patch = patch('a6700_unlock.desktop.physical.load_device_profile',
                              return_value=self.profile)
        catalog_patch.start(); profile_patch.start()
        self.addCleanup(catalog_patch.stop); self.addCleanup(profile_patch.stop)

    def physical_controller(self):
        def backend_factory(progress):
            self.backend = self.Backend(self.sectors)
            return self.backend
        controller = DesktopController(
            None,
            environment_provider=lambda: ENV,
            mode='physical',
            physical_state_dir=self.directory,
            physical_backend_factory=backend_factory,
            physical_backup_dir=self.directory / 'backups',
        )
        self.addCleanup(controller.service.close_session)
        return controller

    def prepare(self, controller):
        controller.check_environment()
        controller.acknowledge_notice(True)
        controller.begin_preparation()
        for ident in STEP_IDS:
            controller.confirm(ident, True)

    def test_physical_mode_reports_real_access_and_write(self):
        controller = self.physical_controller()
        self.prepare(controller)
        state = controller.operate('inspect')
        self.assertTrue(state['compatible'])
        self.assertFalse(state['simulation'])
        self.assertTrue(state['device_access'])
        self.assertFalse(state['write_attempted'])
        result = controller.operate('unlock')
        self.assertEqual(result['result']['status'], 'completed')
        self.assertFalse(result['simulation'])
        self.assertTrue(result['device_access'])
        self.assertTrue(result['write_attempted'])
        self.assertTrue(self.backend.closed)
        self.assertTrue(result['result']['success'])
        self.assertFalse(result['result']['aftercare']['simulation'])
        self.assertFalse(result['connected']); self.assertFalse(result['compatible'])
        self.assertIsNone(controller.service._session_backend)
        self.assertIsNone(controller.service._session_lock)
        controller.service.close_session()
        self.assertTrue(self.backend.closed)

    def test_unlock_keeps_shared_lock_through_write_readback_and_exit(self):
        controller = self.physical_controller()
        self.prepare(controller)
        controller.operate('inspect')
        backend = self.backend
        calls = []
        def assert_locked(label):
            calls.append(label)
            with (self.directory/'host/.assistant.lock').open('a+') as file:
                with self.assertRaises(OSError):
                    lock_file(file)
        read, write = backend.read, backend.write
        def locked_read(*args):
            assert_locked('read')
            return read(*args)
        def locked_write(*args):
            assert_locked('write')
            return write(*args)
        def locked_exit(*args):
            assert_locked('exit')
            backend.closed = True
        backend.read, backend.write, backend.__exit__ = locked_read, locked_write, locked_exit
        self.assertTrue(controller.operate('unlock')['result']['success'])
        self.assertEqual(calls, ['read', 'write', 'read', 'exit'])
        with (self.directory/'host/.assistant.lock').open('a+') as file:
            lock_file(file)

    def test_service_exit_failure_preserves_verified_result_and_releases_lock(self):
        controller = self.physical_controller()
        self.prepare(controller)
        controller.operate('inspect')
        def fail_exit(*args): raise RuntimeError('synthetic exit failure')
        self.backend.__exit__ = fail_exit
        state = controller.operate('unlock')
        self.assertTrue(state['result']['success'])
        self.assertEqual(state['result']['status'], 'completed')
        self.assertIn('synthetic exit failure', state['result']['cleanup_warning'])
        self.assertFalse(state['ready']); self.assertFalse(state['connected'])
        self.assertIsNone(controller.service._session_backend)
        record = json.loads((Path(controller.last_report['run_directory'])/'session-exit.json').read_text())
        self.assertEqual(record['cleanup_status'], 'unconfirmed')
        with (self.directory/'host/.assistant.lock').open('a+') as file:
            lock_file(file)

    def test_hidden_backup_directory_is_not_scanned_during_startup_or_inspect(self):
        controller = self.physical_controller()
        with patch.object(controller.service, 'list_backups', side_effect=AssertionError('unrelated directory scan')):
            self.assertEqual(controller.snapshot()['backups'], [])
            self.prepare(controller)
            self.assertTrue(controller.operate('inspect')['compatible'])
            self.assertEqual(controller.snapshot()['backups'], [])

    def test_physical_service_reuses_capture_after_first_authorization(self):
        flags = []
        def factory(progress, capture=True):
            self.backend = self.Backend(self.sectors)
            self.backend.captured = True
            flags.append(capture)
            return self.backend
        controller = DesktopController(
            None, environment_provider=lambda: ENV, mode='physical',
            physical_state_dir=self.directory, physical_backend_factory=factory,
            physical_backup_dir=self.directory / 'backups')
        self.prepare(controller)
        self.assertEqual(controller.operate('inspect')['phase'], 'home')
        self.assertEqual(flags, [True])
        backend = controller.service._session_backend
        controller.operate('backup')
        self.assertEqual(flags, [True])
        self.assertIs(controller.service._session_backend, backend)
        controller.home()
        controller.operate('inspect')
        self.assertEqual(flags, [True])
        controller.service.close_session()
        self.assertTrue(backend.closed)

    def test_shared_lock_covers_authentication_and_reused_session_until_close(self):
        lock_path = self.directory / 'host/.assistant.lock'
        def assert_locked():
            with lock_path.open('a+') as file:
                with self.assertRaises(OSError):
                    lock_file(file)
        controller = self.physical_controller()
        factory = controller.service.backend_factory
        def locked_factory(*args, **kwargs):
            assert_locked()
            return factory(*args)
        controller.service.backend_factory = locked_factory
        self.prepare(controller)
        self.assertTrue(controller.operate('inspect')['compatible'])
        assert_locked()
        self.assertTrue(controller.operate('inspect')['compatible'])
        controller.service.close_session()
        with lock_path.open('a+') as file:
            lock_file(file)

    def test_busy_lock_prevents_capture_and_authentication(self):
        lock_path = self.directory / 'host/.assistant.lock'
        lock_path.parent.mkdir(parents=True)
        controller = self.physical_controller()
        self.prepare(controller)
        with lock_path.open('a+') as file:
            lock_file(file)
            with self.assertRaisesRegex(Exception, '已有相机工具'):
                controller.operate('inspect')
        self.assertIsNone(self.backend)

    def test_failed_authentication_is_not_automatically_retried_and_releases_lock(self):
        controller = self.physical_controller()
        attempts = []
        def factory(*args, **kwargs):
            attempts.append(1)
            raise RuntimeError('synthetic authentication failure')
        controller.service.backend_factory = factory
        self.prepare(controller)
        with self.assertRaisesRegex(RuntimeError, 'authentication failure'):
            controller.operate('inspect')
        self.assertEqual(len(attempts), 1)
        with (self.directory/'host/.assistant.lock').open('a+') as file:
            lock_file(file)

    def test_disconnected_session_is_closed_without_resending_or_resetting(self):
        controller = self.physical_controller()
        self.prepare(controller)
        controller.operate('inspect')
        from unittest.mock import Mock
        backend = self.backend
        backend.read = Mock(side_effect=RuntimeError('device disconnected'))
        result = controller.operate('unlock')
        self.assertEqual(result['result']['status'], 'error')
        self.assertFalse(result['write_attempted'])
        backend.read.assert_called_once()
        self.assertTrue(backend.closed)
        self.assertIsNone(controller.service._session_backend)
        self.assertIsNone(controller.service._session_lock)

    def test_physical_backup_creates_archive_without_sending_writer(self):
        controller = self.physical_controller()
        self.prepare(controller)
        self.assertTrue(controller.operate('inspect')['compatible'])
        state = controller.operate('backup')
        self.assertEqual(state['result']['status'], 'backup-completed')
        self.assertTrue(state['result']['success'])
        self.assertTrue(state['device_access'])
        self.assertFalse(state['write_attempted'])
        path = Path(state['result']['backup_path'])
        self.assertTrue(path.is_file())
        self.assertEqual([row['id'] for row in state['backups']], [path.name])

    def test_physical_mode_has_no_simulation_scenarios_or_connection_commands(self):
        controller = self.physical_controller()
        with self.assertRaises(ValueError):
            controller.reset('enabled')
        with self.assertRaises(ValueError):
            controller.disconnect()
        with self.assertRaises(ValueError):
            controller.reconnect()
        state = controller.snapshot()
        self.assertEqual(state['scenarios'], [])
        self.assertFalse(state['simulation'])


if __name__ == '__main__':
    unittest.main()
