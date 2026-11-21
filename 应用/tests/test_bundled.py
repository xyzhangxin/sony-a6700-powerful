"""Portable resource selection and GUI capture authorization; no device access."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from a6700_unlock import bundled
from a6700_unlock.device_protocol import DeviceError
from a6700_unlock.usb.backend import capture_macos, _local_capture_helper, close_capture_session, _CaptureSession


class BundledTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.addCleanup(close_capture_session)
        self.root=Path(self.folder.name)
        self.items={}
        for name in ['capture','libusb']:
            path=self.root/name;path.write_bytes(name.encode())
            self.items[name]=dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.save()
        patcher=patch.object(bundled,'resource_root',return_value=self.root)
        patcher.start();self.addCleanup(patcher.stop)

    def save(self):
        (self.root/'bundle-manifest.json').write_text(json.dumps(dict(schema=1,resources=self.items)), encoding='utf-8')

    def test_corrupt_or_missing_bundle_never_falls_back_to_developer_library(self):
        self.assertEqual(bundled.resource('libusb'),self.root/'libusb')
        (self.root/'libusb').write_bytes(b'corrupted')
        with self.assertRaises(DeviceError):bundled.resource('libusb')
        (self.root/'bundle-manifest.json').unlink()
        with self.assertRaises(DeviceError):bundled.resource('capture')

    def test_bundle_rejects_absolute_escape_and_symlink_resources(self):
        for value in ['/usr/lib/anything','../outside']:
            self.items['libusb']['path']=value;self.save()
            with self.assertRaises(DeviceError):bundled.resource('libusb')
        (self.root/'alias').symlink_to(self.root/'capture')
        self.items['capture']['path']='alias';self.save()
        with self.assertRaises(DeviceError):bundled.resource('capture')

    def test_source_install_uses_existing_backend_without_bundle(self):
        with patch.object(bundled,'resource_root',return_value=None):
            self.assertIsNone(bundled.resource('capture'))
            with patch('a6700_unlock.usb.backend._local_capture_helper', return_value=None), \
                 patch('a6700_unlock.usb.backend.installed_capture_available_in_source_mode',
                       return_value=False), \
                 patch('a6700_unlock.usb.backend._capture_macos_source') as source:
                capture_macos(lambda _:None)
                source.assert_called_once()

    def test_gui_capture_uses_fixed_helper_and_system_authorization_without_compiler(self):
        with patch('a6700_unlock.usb.backend._local_capture_helper', return_value=None), \
             patch('a6700_unlock.usb.backend.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run, \
             patch('a6700_unlock.usb.backend._CaptureSession._connect'), \
             patch('a6700_unlock.usb.backend._CaptureSession.capture') as capture, \
             patch('a6700_unlock.usb.backend.shutil.which',side_effect=AssertionError('no build tool needed')):
            capture_macos(lambda _:None)
            capture_macos(lambda _:None)
        args=run.call_args.args[0]
        self.assertEqual(args[0],'/usr/bin/osascript')
        self.assertIn('quoted form',args[2])
        self.assertIn('administrator privileges',args[2])
        self.assertNotIn('>/dev/null 2>&1 &',args[2])
        self.assertEqual(args[-3],str(self.root/'capture'))
        self.assertEqual(run.call_count,1)
        self.assertEqual(capture.call_count,2)

    def test_cancelled_authorization_does_not_compile_retry_or_disclose_raw_paths(self):
        with patch('a6700_unlock.usb.backend._local_capture_helper', return_value=None), \
             patch('a6700_unlock.usb.backend.subprocess.run',return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaises(DeviceError):capture_macos(lambda _:None)
        self.assertEqual(run.call_count,1)

    def test_capture_session_reuses_one_socket_for_capture_and_shutdown(self):
        finished = threading.Event()
        def authorized(args, **kwargs):
            path = args[-2]
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path); server.listen(1); server.settimeout(5)
            def serve():
                try:
                    client, _ = server.accept()
                    stream = client.makefile('rwb')
                    stream.write(b'READY\n'); stream.flush()
                    self.assertEqual(stream.readline(), b'capture\n')
                    stream.write(b'OK\n'); stream.flush()
                    self.assertEqual(stream.readline(), b'capture\n')
                    stream.write(b'OK\n'); stream.flush()
                    self.assertEqual(stream.readline(), b'stop\n')
                    stream.write(b'OK\n'); stream.flush()
                    stream.close(); client.close()
                finally:
                    server.close(); finished.set()
            threading.Thread(target=serve, daemon=True).start()
            return SimpleNamespace(returncode=0)
        with patch('a6700_unlock.usb.backend.subprocess.run', side_effect=authorized) as run:
            session = _CaptureSession(self.root/'capture', lambda _: None)
            self.addCleanup(session.close)
            session.capture(); session.capture(); session.close()
        self.assertTrue(finished.wait(5))
        self.assertEqual(run.call_count, 1)

    def test_capture_helper_socket_permits_directory_owner_to_connect(self):
        source = Path(__file__).resolve().parents[1] / 'src/a6700_unlock/native/mac_capture.m'
        text = source.read_text(encoding='utf-8')
        self.assertIn('chmod(path, 0666)', text)
        self.assertIn('getpeereid(client, &peer, &group)', text)
        self.assertIn('caller\'s 0700 directory', text)

    def test_old_installed_helper_cannot_restore_removed_reset_behavior(self):
        import stat
        local = Mock()
        local.stat.return_value = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o755)
        local.read_bytes.return_value = b'old-reset-helper'
        with patch('a6700_unlock.usb.backend.LOCAL_CAPTURE_HELPER', local):
            self.assertIsNone(_local_capture_helper())
        local.read_bytes.return_value = (self.root/'capture').read_bytes()
        with patch('a6700_unlock.usb.backend.LOCAL_CAPTURE_HELPER', local):
            self.assertIs(_local_capture_helper(), local)

    def test_wdi_uses_verified_resource_and_rejects_corruption(self):
        path=self.root/'wdi-simple.exe';path.write_bytes(b'wdi')
        self.items['wdi']=dict(path='wdi-simple.exe',sha256=hashlib.sha256(b'wdi').hexdigest());self.save()
        self.assertEqual(bundled.resource('wdi'),path)
        path.write_bytes(b'corrupted')
        with self.assertRaises(DeviceError):bundled.resource('wdi')

    @unittest.skipUnless(importlib.util.find_spec('usb'),'PyUSB required for library selection check')
    def test_bundled_usb_selects_verified_library(self):
        from a6700_unlock.usb import legacy
        with patch.object(legacy.libusb1,'get_backend',return_value=object()) as load:
            legacy.get_libusb_backend()
            self.assertEqual(load.call_args.kwargs['find_library']('usb-1.0'),str(self.root/'libusb'))
        (self.root/'libusb').write_bytes(b'corrupted')
        with patch.object(legacy.libusb1,'get_backend') as load:
            with self.assertRaises(DeviceError):legacy.get_libusb_backend()
            load.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('usb'),'PyUSB required for library probe check')
    def test_startup_probe_loads_verified_library_without_initializing_usb_context(self):
        from a6700_unlock.usb import legacy
        from a6700_unlock.doctor import diagnose
        from a6700_unlock.catalog import load_catalog
        from a6700_unlock.device_profile import load_device_profile
        library = Mock()
        # The probe's subject is the bundled library itself, not platform
        # gating; pin darwin so the fake bundle decides every check on any OS.
        with patch.object(legacy.ctypes, 'CDLL', return_value=library) as load, \
             patch('sys.platform', 'darwin'), \
             patch.object(legacy.libusb1, 'get_backend', side_effect=AssertionError('early context')), \
             patch.object(legacy.usb.core, 'find', side_effect=AssertionError('enumeration')):
            report = diagnose(load_device_profile(load_catalog()))
        self.assertTrue(report['ready'])
        load.assert_called_once_with(str(self.root/'libusb'))
        library.libusb_init.assert_not_called()
        library.libusb_get_device_list.assert_not_called()
        (self.root/'libusb').write_bytes(b'corrupt')
        with patch.object(legacy.ctypes, 'CDLL') as load:
            report = diagnose(load_device_profile(load_catalog()))
        self.assertFalse(report['ready'])
        load.assert_not_called()


def load_tests(loader, tests, pattern):
    # Distinct platform socket capabilities, not skipped or silently passing tests.
    suite = loader.loadTestsFromTestCase(BundledTests)
    if hasattr(socket, 'AF_UNIX'):
        return suite
    return unittest.TestSuite(
        test for test in suite
        if test._testMethodName != 'test_capture_session_reuses_one_socket_for_capture_and_shutdown'
    )


if __name__=='__main__':unittest.main()
