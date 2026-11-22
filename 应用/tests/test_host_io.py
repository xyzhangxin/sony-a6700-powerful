import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from a6700_unlock import host_io
from a6700_unlock.device_protocol import DeviceBusyError


class HostIoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_platform_paths_keep_existing_macos_locations(self):
        with patch.object(host_io.sys, 'platform', 'darwin'), \
             patch('pathlib.Path.home', return_value=self.root):
            self.assertEqual(host_io.default_state_dir(),
                             self.root / 'Library/Application Support/a6700-unlock')
            self.assertEqual(host_io.host_lock_path(),
                             self.root / 'Library/Application Support/sony-a6700-ftp/.assistant.lock')

    def test_windows_paths_use_local_app_data(self):
        with patch.object(host_io.sys, 'platform', 'win32'), \
             patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)}):
            self.assertEqual(host_io.default_state_dir(), self.root / 'a6700-unlock')
            self.assertEqual(host_io.host_lock_path(), self.root / 'sony-a6700-ftp/.assistant.lock')

    def test_exclusive_create_never_overwrites_an_existing_record(self):
        target = self.root / 'record.json'
        fd = host_io.open_exclusive(target)
        with os.fdopen(fd, 'w') as output:
            output.write('first')
        with self.assertRaises(FileExistsError):
            host_io.open_exclusive(target)
        self.assertEqual(target.read_text(encoding='utf-8'), 'first')

    def test_lock_is_exclusive_and_released_without_unlinking_inode(self):
        path = self.root / 'device.lock'
        first = path.open('a+b')
        self.addCleanup(first.close)
        with host_io.exclusive_lock(first):
            with path.open('a+b') as second:
                with self.assertRaises(DeviceBusyError):
                    with host_io.exclusive_lock(second):
                        pass
        with path.open('a+b') as second:
            with host_io.exclusive_lock(second):
                self.assertTrue(path.exists())


if __name__ == '__main__':
    unittest.main()
