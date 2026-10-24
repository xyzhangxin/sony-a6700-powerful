"""Platform portability guarantees that must hold on every OS.

Binary archive writes must be byte-exact: a parallel Windows port observed
0x0A -> 0x0D 0x0A translation inside .a6700backup files on its (frozen)
runtime, so this pins the property directly on our own writer.
"""
from pathlib import Path
import tempfile
import unittest

from a6700_unlock.backup import _write_new


class PortabilityTests(unittest.TestCase):
    def test_backup_archive_writes_are_byte_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.a6700backup"
            payload = bytes(range(256)) * 4 + b"\x0a" * 64
            _write_new(path, payload)
            self.assertEqual(path.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
