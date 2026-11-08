"""Real isolated host locks and disposable holder processes; never a camera."""
import json
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from a6700_unlock.connection_takeover import TakeoverManager, lock_busy, known_active_task
from a6700_unlock.desktop.controller import DesktopController
from a6700_unlock.device_protocol import DeviceBusyError


HOLDER = '''import os,signal,sys,time
f=open(sys.argv[1], 'a+b')
if os.name=='nt':
 import msvcrt
 f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
else:
 import fcntl
 fcntl.flock(f, fcntl.LOCK_EX)
if sys.argv[2]=='ignore': signal.signal(signal.SIGTERM, signal.SIG_IGN)
print('ready', flush=True)
while True: time.sleep(1)
'''


class TakeoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = self.root / '.assistant.lock'
        self.manager = TakeoverManager(self.root / 'state')
        target = patch('a6700_unlock.connection_takeover.host_lock_path', return_value=self.lock)
        target.start(); self.addCleanup(target.stop)

    def holder(self, ignore=False):
        child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', HOLDER,
                                  str(self.lock), 'ignore' if ignore else 'normal'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if child.poll() is None: child.kill()
            child.wait(timeout=5); child.stdout.close(); child.stderr.close()
        self.addCleanup(cleanup)
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        return child

    def prepared(self):
        result = self.manager.prepare(self.lock)
        self.assertTrue(result['available'], result)
        return result['confirmation']

    def test_unheld_existing_lock_is_not_an_occupied_device(self):
        self.lock.touch()
        with patch('a6700_unlock.connection_takeover.os.kill') as kill:
            self.assertFalse(self.manager.prepare(self.lock)['available'])
        kill.assert_not_called()
        self.assertTrue(self.lock.exists())

    def test_inspection_never_terminates_the_holder(self):
        child = self.holder()
        with patch('a6700_unlock.connection_takeover.os.kill') as kill:
            state = self.manager.prepare(self.lock)
        self.assertEqual(state['pid'], child.pid)
        self.assertTrue(state['available']); kill.assert_not_called()
        self.assertIsNone(child.poll()); self.assertTrue(lock_busy(self.lock))

    def test_confirmation_required_and_cannot_be_replayed(self):
        self.holder(); token = self.prepared()
        with patch('a6700_unlock.connection_takeover.os.kill') as kill:
            with self.assertRaises(ValueError): self.manager.execute(token, False)
            with self.assertRaises(ValueError): self.manager.execute(token, True)
        kill.assert_not_called()

    def test_expired_confirmation_never_signals(self):
        self.holder(); token = self.prepared()
        self.manager.pending['expires'] = time.monotonic() - 1
        with patch('a6700_unlock.connection_takeover.os.kill') as kill:
            with self.assertRaises(ValueError): self.manager.execute(token, True)
        kill.assert_not_called()

    def test_changed_process_identity_never_signals(self):
        self.holder(); token = self.prepared()
        changed = dict(self.manager.pending['owner'], started='a different process')
        with patch('a6700_unlock.connection_takeover.unique_owner', return_value=changed), \
                patch('a6700_unlock.connection_takeover.os.kill') as kill:
            with self.assertRaises(ValueError): self.manager.execute(token, True)
        kill.assert_not_called()

    def test_replaced_lock_inode_never_signals_old_holder(self):
        child = self.holder(); token = self.prepared()
        self.lock.rename(self.root / 'old-lock')
        self.lock.touch()
        with patch('a6700_unlock.connection_takeover.os.kill') as kill:
            with self.assertRaises(ValueError): self.manager.execute(token, True)
        kill.assert_not_called(); self.assertIsNone(child.poll())

    def test_known_running_task_cannot_be_forced(self):
        self.holder()
        with patch('a6700_unlock.connection_takeover.known_active_task', return_value=True):
            self.assertFalse(self.manager.prepare(self.lock)['available'])
        token = self.prepared()
        with patch('a6700_unlock.connection_takeover.known_active_task', return_value=True), \
                patch('a6700_unlock.connection_takeover.os.kill') as kill:
            with self.assertRaises(ValueError): self.manager.execute(token, True)
        kill.assert_not_called()

    def test_unknown_or_multiple_owners_have_no_force_button(self):
        self.holder()
        with patch('a6700_unlock.connection_takeover.unique_owner', return_value=None):
            self.assertFalse(self.manager.prepare(self.lock)['available'])

    def test_normal_exit_releases_lock_without_removing_inode_or_escalation(self):
        child = self.holder(); inode = self.lock.stat().st_ino; token = self.prepared()
        result = self.manager.execute(token, True)
        child.wait(timeout=5)
        self.assertTrue(result['released']); self.assertEqual(result['signals'], ['SIGTERM'])
        self.assertFalse(lock_busy(self.lock)); self.assertEqual(self.lock.stat().st_ino, inode)
        journal = list((self.root / 'state/connection-takeovers').iterdir())
        intent = json.loads((journal[0] / 'intent.json').read_text())
        self.assertTrue(intent['user_confirmed']); self.assertEqual(intent['owner']['pid'], child.pid)

    def test_confirmation_can_escalate_only_for_the_same_stuck_holder(self):
        child = self.holder(ignore=True); token = self.prepared()
        result = self.manager.execute(token, True)
        child.wait(timeout=5)
        self.assertEqual(result['signals'], ['SIGTERM', 'SIGKILL'])
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assertTrue(self.lock.exists()); self.assertFalse(lock_busy(self.lock))

    def test_no_takeover_of_an_unrelated_state_directory_lock(self):
        other = self.root / 'device.lock'; other.touch()
        self.assertFalse(self.manager.prepare(other)['available'])

    def test_optional_debug_registration_is_not_required_for_other_users(self):
        child = self.holder()
        registration = self.root / 'bench.json'
        registration.write_text('{broken configuration')
        registration.chmod(0o600)
        with patch('a6700_unlock.connection_takeover.urllib.request.build_opener') as opener:
            self.assertFalse(known_active_task(self.lock, dict(pid=child.pid)))
        opener.assert_not_called()
        self.assertTrue(self.manager.prepare(self.lock)['available'])

    def test_registration_never_sends_tokens_to_nonlocal_addresses(self):
        child = self.holder()
        registration = self.root / 'bench.json'
        registration.write_text(json.dumps(dict(pid=child.pid, url='http://192.0.2.1:8767', token='test-only')))
        registration.chmod(0o600)
        with patch('a6700_unlock.connection_takeover.urllib.request.build_opener') as opener:
            self.assertFalse(known_active_task(self.lock, dict(pid=child.pid)))
        opener.assert_not_called()

    def test_physical_bridge_reports_busy_without_claiming_camera_access(self):
        from a6700_unlock.desktop import physical_bridge
        c = DesktopController(None, mode='physical', physical_state_dir=self.root / 'state',
                              environment_provider=lambda: dict(ready=True, checks=[], device_access=False))
        c.check_environment(); c.acknowledge_notice(True); c.begin_preparation(); c.confirm_preparation(True)
        output = io.StringIO()
        takeover = dict(available=True, confirmation='test-only', owner='synthetic', pid=42, detail='synthetic busy')
        with patch.object(c.service, 'perform', side_effect=DeviceBusyError('synthetic busy', self.lock)), \
                patch.object(c.takeover_manager, 'prepare', return_value=takeover), \
                patch.object(physical_bridge, 'DesktopController', return_value=c), \
                patch('sys.argv', ['test']), patch('sys.stdin', io.StringIO('{"command":"inspect"}\n')), \
                patch('sys.stdout', output):
            physical_bridge.main()
        snapshots = [json.loads(line)['state'] for line in output.getvalue().splitlines()
                     if json.loads(line)['type'] == 'state']
        result = snapshots[-1]
        self.assertEqual(result['result']['status'], 'connection-busy')
        self.assertEqual(result['result']['takeover'], takeover)
        self.assertFalse(result['device_access']); self.assertFalse(result['write_attempted'])

    def test_controller_never_uses_takeover_as_a_firmware_write_or_cached_inspection(self):
        with tempfile.TemporaryDirectory() as folder:
            c = DesktopController(None, mode='physical', physical_state_dir=folder)
            c.result = dict(status='connection-busy', takeover=dict(available=True))
            with self.assertRaises(ValueError): c.reserve_takeover('test', False)
            c.reserve_takeover('test', True)
            with self.assertRaises(ValueError): c.reserve_takeover('test', True)
            with patch.object(c.takeover_manager, 'execute', return_value=dict(released=True)):
                snapshot = c.run_takeover(lambda _: None)
            self.assertFalse(snapshot['compatible']); self.assertFalse(snapshot['ready'])
            self.assertFalse(snapshot['device_access']); self.assertFalse(snapshot['write_attempted'])
            self.assertEqual(snapshot['result']['status'], 'connection-released')
            with self.assertRaises(ValueError): c.reserve('unlock')


class WindowsBusyTests(TakeoverTests):
    # Windows deliberately offers no PID takeover. Real one-byte OS locks are
    # tested in disposable processes; no camera or privileged helper is used.
    def test_busy_is_manual_only_and_cannot_signal_holder(self):
        child=self.holder();inode=self.lock.stat().st_ino
        with patch('a6700_unlock.connection_takeover.os.kill') as kill, \
             patch('a6700_unlock.connection_takeover.subprocess.run') as command:
            result=self.manager.prepare(self.lock)
            self.assertFalse(result['available']);self.assertEqual(result['confirmation'],'')
            self.assertIn('Windows',result['detail'])
            with self.assertRaises(ValueError):self.manager.execute('forged',True)
        kill.assert_not_called();command.assert_not_called()
        self.assertIsNone(child.poll());self.assertTrue(lock_busy(self.lock))
        self.assertEqual(self.lock.stat().st_ino,inode)
        child.terminate();child.wait(timeout=5)
        self.assertFalse(lock_busy(self.lock))
        self.assertFalse(self.manager.prepare(self.lock)['available'])
    def test_application_mutex_raises_busy_with_shared_path(self):
        from a6700_unlock.operations import _flock
        self.holder()
        with self.lock.open('a+b') as handle:
            with self.assertRaises(DeviceBusyError) as error:_flock(handle)
        self.assertEqual(error.exception.lock_path,self.lock)


def load_tests(loader, tests, pattern):
    # Distinct platform capabilities, not skipped or silently passing tests.
    if sys.platform!='win32':return loader.loadTestsFromTestCase(TakeoverTests)
    names=('test_unheld_existing_lock_is_not_an_occupied_device',
           'test_no_takeover_of_an_unrelated_state_directory_lock',
           'test_physical_bridge_reports_busy_without_claiming_camera_access',
           'test_controller_never_uses_takeover_as_a_firmware_write_or_cached_inspection',
           'test_busy_is_manual_only_and_cannot_signal_holder',
           'test_application_mutex_raises_busy_with_shared_path')
    return unittest.TestSuite(WindowsBusyTests(name) for name in names)


if __name__ == '__main__': unittest.main()
