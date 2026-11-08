"""Windows host boundaries. Temporary records and fake DLL only; no USB access."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from a6700_unlock.host_io import open_exclusive
from a6700_unlock import operations


class WindowsHostTests(unittest.TestCase):
    def test_exclusive_records_are_byte_exact_and_never_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'前态记录.dat'
            data = bytes(range(256))*4
            with os.fdopen(open_exclusive(path), 'wb') as out:
                out.write(data); out.flush(); os.fsync(out.fileno())
            self.assertEqual(path.read_bytes(), data)
            with self.assertRaises(FileExistsError):
                open_exclusive(path)
            self.assertEqual(path.read_bytes(), data)

    def test_localappdata_redirect_keeps_same_host_lock_across_state_directories(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.platform', 'win32'), \
                patch.dict(os.environ, LOCALAPPDATA=folder):
            self.assertEqual(operations.default_state_dir(), Path(folder)/'a6700-unlock')
            self.assertEqual(operations.host_lock_path(), Path(folder)/'sony-a6700-ftp/.assistant.lock')

    @unittest.skipUnless(importlib.util.find_spec('usb'), 'PyUSB required')
    def test_windows_dll_loader_does_not_enumerate_or_fall_back_to_mac_paths(self):
        from a6700_unlock.usb import legacy
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder)/'libusb-1.0.dll'; library.write_bytes(b'synthetic')
            fake = SimpleNamespace(get_library_path=lambda: str(library))
            with patch('sys.platform', 'win32'), patch.dict(sys.modules, libusb_package=fake), \
                 patch('a6700_unlock.bundled.resource', return_value=None), \
                 patch.object(legacy.usb.core, 'find', side_effect=AssertionError('USB enumeration')), \
                 patch.object(legacy.libusb1, 'get_backend', side_effect=AssertionError('USB context')):
                self.assertEqual(legacy.libusb_library_path(), str(library))
                library.unlink()
                with self.assertRaisesRegex(legacy.ProbeError, 'DLL'):
                    legacy.libusb_library_path()

    def test_libwdi_binary_and_source_match_reviewed_pins(self):
        import hashlib,json
        root=Path(__file__).resolve().parents[1]
        pin=json.loads((root/'desktop/runtime-dependencies.json').read_text())['libwdi']
        self.assertEqual(hashlib.sha256((root/'desktop/windows/driver/wdi-simple.exe').read_bytes()).hexdigest(),pin['binary_sha256'])
        source=root/'desktop/windows/driver/libwdi-1.5.0.tar.gz'
        # The source ZIP intentionally carries build instructions and hashes;
        # the corresponding source archive is added to binary deliveries.
        if source.exists():
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),pin['source_sha256'])

    def test_failed_record_flush_blocks_writer_after_directory_setup(self):
        from test_operations import sparse_fixture, Backend
        profile, sectors = sparse_fixture()
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder); backend = Backend(sectors, directory)
            calls = []
            real = operations.save_json
            def save(path, value):
                calls.append(Path(path).name)
                if Path(path).name == 'write-intent.json':
                    raise OSError('synthetic persistent storage failure')
                return real(path, value)
            with patch.object(operations, 'host_lock_path', return_value=directory/'host.lock'), \
                 patch.object(operations, 'save_json', side_effect=save):
                result = operations.execute(profile, backend, 'unlock', directory)
            self.assertIn('write-intent.json', calls)
            self.assertEqual(backend.writes, 0)
            self.assertFalse(result['write_attempted'])
            self.assertEqual(result['status'], 'error')


if __name__ == '__main__':
    unittest.main()
