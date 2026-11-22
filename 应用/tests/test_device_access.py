import sys
import types
import unittest
from unittest.mock import patch

from a6700_unlock.device_access import UsbDeviceAccess
from a6700_unlock.usb.backend import UsbBackend
from test_operations import sparse_fixture


class FakeBackend:
    def __init__(self, events, sectors=(), fail_open=False):
        self.events = events
        self.sectors = list(sectors)
        self.fail_open = fail_open
        self.progress = None
        self.cleanup_status = None
        self.cleanup_warning = None
        self.closed = False
    def prepare_access(self):
        self.events.append('prepare')
    def open_transport(self):
        self.events.append('open')
        if self.fail_open:
            raise RuntimeError('synthetic auth failure')
        return self
    def __exit__(self, *args):
        self.events.append('exit')
        self.closed = True
    def read(self, nonce):
        return list(self.sectors)


class DeviceAccessTests(unittest.TestCase):
    def test_prepare_open_release_is_ordered_and_single_use(self):
        events = []
        backend = FakeBackend(events)
        access = UsbDeviceAccess(object(), lambda progress, capture: backend)
        progress = []
        prepared = access.prepare_access(progress.append, capture=True)
        self.assertEqual(prepared['stage'], 'access-prepared')
        self.assertFalse(prepared['authenticated'])
        self.assertIs(access.open_transport(progress.append), backend)
        self.assertEqual(events, ['prepare', 'open'])
        access.release_access()
        access.release_access()
        self.assertEqual(events, ['prepare', 'open', 'exit'])
        with self.assertRaises(RuntimeError):
            access.open_transport(progress.append)

    def test_open_requires_prepare_and_failed_open_is_not_closed_as_a_session(self):
        events = []
        backend = FakeBackend(events, fail_open=True)
        access = UsbDeviceAccess(object(), lambda progress: backend)
        with self.assertRaises(RuntimeError):
            access.open_transport(lambda _: None)
        access.prepare_access(lambda _: None)
        with self.assertRaisesRegex(RuntimeError, 'auth failure'):
            access.open_transport(lambda _: None)
        access.release_access()
        self.assertEqual(events, ['prepare', 'open'])
        self.assertFalse(backend.closed)

    def test_real_backend_separates_capture_from_libusb_authentication(self):
        events = []
        target, channel = object(), object()
        legacy = types.SimpleNamespace(
            get_libusb_backend=lambda: events.append('libusb'),
            run_authentication=lambda *args, **kwargs: events.append('auth') or (target, channel))
        with patch('a6700_unlock.usb.backend.payloads', return_value={}), \
             patch('a6700_unlock.usb.backend.sys.platform', 'darwin'), \
             patch('a6700_unlock.usb.backend.capture_macos', side_effect=lambda progress: events.append('capture')), \
             patch('a6700_unlock.usb.legacy', legacy, create=True), \
             patch.dict(sys.modules, {'a6700_unlock.usb.legacy': legacy}):
            backend = UsbBackend(object(), capture=True)
            prepared = backend.prepare_access()
            self.assertTrue(prepared['capture'])
            self.assertEqual(events, ['capture'])
            self.assertIs(backend.open_transport(), backend)
            self.assertEqual(events, ['capture', 'libusb', 'auth'])
            with self.assertRaisesRegex(Exception, '不能重复认证'):
                backend.open_transport()

    def test_physical_service_uses_access_adapter_and_releases_it(self):
        profile, sectors = sparse_fixture()
        events = []
        backend = FakeBackend(events, sectors)
        with patch('a6700_unlock.desktop.physical.load_catalog', return_value=profile.catalog), \
             patch('a6700_unlock.desktop.physical.load_device_profile', return_value=profile), \
             patch('a6700_unlock.operations.default_state_dir', return_value=self.directory), \
             patch('a6700_unlock.operations.host_lock_path', return_value=self.directory / 'host.lock'):
            from a6700_unlock.desktop.physical import PhysicalService
            service = PhysicalService(self.directory, lambda progress: backend)
            try:
                result = service.perform('inspect')
                self.assertEqual(result['status'], 'inspected')
                self.assertEqual(events, ['prepare', 'open'])
            finally:
                service.close_session()
        self.assertEqual(events, ['prepare', 'open', 'exit'])
        self.assertTrue(backend.closed)

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)


if __name__ == '__main__':
    unittest.main()
