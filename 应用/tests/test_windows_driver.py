"""Camera-free Windows driver preparation tests; no SetupAPI or USB writes."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from a6700_unlock.device_protocol import DeviceError
from a6700_unlock import windows_driver as driver


class WindowsDriverTests(unittest.TestCase):
    def test_only_fixed_camera_pids_and_winusb_are_allowed(self):
        with patch.object(driver, 'wdi_path', return_value=Path('wdi-simple.exe')):
            command = driver.driver_arguments(0x0E76, Path('destination'))
        self.assertEqual(command[1:7], ['--vid', '0x054C', '--pid', '0x0E76', '--type', '0'])
        with self.assertRaises(ValueError):
            driver.driver_arguments(0x1234, Path('destination'))

    def test_bind_verifies_result_independently(self):
        completed = SimpleNamespace(returncode=0)
        with patch.object(driver, 'driver_arguments', return_value=['fixed-wdi']), \
             patch.object(driver.subprocess, 'run', return_value=completed) as run, \
             patch.object(driver, 'driver_state', return_value=dict(winusb=True)):
            with tempfile.TemporaryDirectory() as folder:
                driver.bind(0x0E76, folder)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['fixed-wdi'])

    def test_prepare_is_silent_when_both_identities_are_ready(self):
        def state(pid):
            return dict(present=pid == 0x0E76, winusb=True)
        with patch('sys.platform', 'win32'), patch.object(driver, 'driver_state', side_effect=state), \
             patch.object(driver, 'DriverSession', side_effect=AssertionError('unexpected UAC')):
            self.assertIsNone(driver.prepare(Path('unused')))

    def test_prepare_starts_one_driver_only_elevated_session(self):
        def state(pid):
            return dict(present=pid == 0x0E76, winusb=pid == 0x0E76)
        session = Mock(); session.start.return_value = session
        with patch('sys.platform', 'win32'), patch.object(driver, 'driver_state', side_effect=state), \
             patch.object(driver, 'service_staged', return_value=False), \
             patch.object(driver, 'DriverSession', return_value=session) as factory:
            result = driver.prepare(Path('state'))
        self.assertIs(result, session)
        factory.assert_called_once()
        session.start.assert_called_once_with()

    def test_driver_session_accepts_initial_or_service_ready_without_timeout(self):
        for stage in ('initial_ready','service_ready'):
            with tempfile.TemporaryDirectory() as folder:
                session=driver.DriverSession(folder)
                session.status.write_text(json.dumps(dict(stage=stage,pid=0x0336)))
                with patch.object(driver,'_runas',return_value=123),                      patch.object(driver.time,'sleep',side_effect=AssertionError('must not wait')):
                    self.assertIs(session.start(),session)

    def test_driver_session_uses_actual_driver_state_when_status_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            session=driver.DriverSession(folder)
            with patch.object(driver,'_runas',return_value=123),                  patch.object(driver,'driver_state',return_value=dict(winusb=False)),                  patch.object(driver,'service_staged',return_value=True),                  patch.object(driver.time,'sleep',side_effect=AssertionError('must not wait')):
                self.assertIs(session.start(),session)

    def test_worker_binds_msc_then_service_with_one_process(self):
        calls = []
        def state(pid):
            present = pid != 0x047D and (pid != 0x0336 or 0x0336 in calls)
            return dict(present=present, winusb=pid in calls)
        def bind(pid, destination):
            calls.append(pid)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); status = root/'status.json'
            request = root/'request.json'
            request.write_text(json.dumps(dict(schema=1, status_path=str(status),
                stop_path=str(root/'stop'), destination=str(root/'wdi'), wait_seconds=1)))
            with patch.object(driver, 'is_admin', return_value=True), \
                 patch.object(driver, 'driver_state', side_effect=state), \
                 patch.object(driver, 'bind', side_effect=bind):
                self.assertEqual(driver.run_worker(request), 0)
            self.assertEqual(json.loads(status.read_text())['stage'], 'service_ready')
        self.assertEqual(calls, [0x0E76, 0x0336])

    def test_utf16_inf_is_accepted_for_staging_and_driver_store_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); inf=root/'usb_device.inf'
            text='DeviceID   = "VID_054C&PID_0336"\nAddService = WinUSB,0x00000002,WinUSB_ServiceInstall\n'
            inf.write_bytes(b'\xff\xfe'+text.encode('utf-16le'))
            completed=SimpleNamespace(returncode=0)
            with patch.object(driver.subprocess,'run',return_value=completed),                  patch.object(driver,'service_staged',return_value=True):
                self.assertTrue(driver.stage_service(root))
            (root/'oem1.inf').write_bytes(inf.read_bytes())
            with patch.object(driver,'_driver_store',return_value=root),                  patch('sys.platform','win32'):
                self.assertTrue(driver.service_staged())

    def test_stage_service_accepts_only_exact_inf_and_verifies_driver_store(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); inf=root/'usb_device.inf'
            inf.write_text('DeviceID   = "VID_054C&PID_0336"\nAddService = WinUSB,0x00000002,WinUSB_ServiceInstall\n')
            completed=SimpleNamespace(returncode=0)
            with patch.object(driver.subprocess,'run',return_value=completed) as run,                  patch.object(driver,'service_staged',return_value=True):
                self.assertTrue(driver.stage_service(root))
            self.assertEqual(run.call_args.args[0][0],'pnputil.exe')
            inf.write_text('DeviceID   = "VID_054C&PID_9999"')
            with self.assertRaises(DeviceError):driver.stage_service(root)

    def test_worker_requires_elevation_and_never_falls_back_to_main_app(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); status = root/'status.json'; request = root/'request.json'
            request.write_text(json.dumps(dict(schema=1, status_path=str(status),
                stop_path=str(root/'stop'), destination=str(root/'wdi'))))
            with patch.object(driver, 'is_admin', return_value=False), \
                 patch.object(driver, 'bind', side_effect=AssertionError('unexpected installer')):
                self.assertEqual(driver.run_worker(request), 1)
            self.assertEqual(json.loads(status.read_text())['stage'], 'error')

    def test_backend_closes_driver_helper_after_success_or_failure(self):
        from a6700_unlock.usb.backend import UsbBackend
        for error in (None, DeviceError('synthetic auth failure')):
            session = Mock()
            with patch('sys.platform', 'win32'), \
                 patch('a6700_unlock.usb.backend.payloads', return_value={}), \
                 patch('a6700_unlock.windows_driver.prepare', return_value=session), \
                 patch('a6700_unlock.usb.legacy.get_libusb_backend'), \
                 patch('a6700_unlock.usb.legacy.run_authentication',
                       **({'side_effect': error} if error else {'return_value': (Mock(), Mock())})) as authenticate:
                backend = UsbBackend(SimpleNamespace())
                if error:
                    with self.assertRaises(DeviceError): backend.__enter__()
                else:
                    self.assertIs(backend.__enter__(), backend)
                self.assertEqual(authenticate.call_args.args[2], 45.0)
                session.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
