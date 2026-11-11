"""Reject stale or damaged native resources before constructing a USB session."""
from importlib.resources import files
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from a6700_unlock.catalog import load_catalog
from a6700_unlock.device_profile import load_device_profile
from a6700_unlock.device_protocol import DeviceError
from a6700_unlock.usb.backend import payloads


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        original = files('a6700_unlock')
        for name in ('data/native/manifest.json', 'data/native/reader.elf',
                     'data/native/writer.elf', 'native/transaction.c'):
            destination = self.root/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(original.joinpath(name).read_bytes())
        self.profile = load_device_profile(load_catalog())

    def test_native_source_edit_without_rebuild_is_rejected(self):
        source = self.root/'native/transaction.c'
        source.write_bytes(source.read_bytes() + b'\n/* edited without a rebuild */\n')
        with patch('a6700_unlock.usb.backend.files', return_value=self.root):
            with self.assertRaisesRegex(DeviceError, 'source changed'):
                payloads(self.profile)

    def test_corrupted_binary_is_rejected_even_with_matching_source(self):
        binary = self.root/'data/native/reader.elf'
        data = bytearray(binary.read_bytes())
        data[-1] ^= 1
        binary.write_bytes(data)
        with patch('a6700_unlock.usb.backend.files', return_value=self.root):
            with self.assertRaisesRegex(DeviceError, 'payload hash mismatch'):
                payloads(self.profile)


if __name__ == '__main__':
    unittest.main()
