"""Public source packaging rejects known internal firmware copies.

Uses synthetic blocks only; never reads private archive files or real firmware.
"""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_source_builder', ROOT/'scripts/build_source.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PublicDistributionTests(unittest.TestCase):
    def test_retired_sample_path_is_rejected_even_if_contents_are_changed(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder)/'sample.c'
            sample.write_text('/* synthetic placeholder */\n')
            for relative in builder.INTERNAL_SAMPLE_PATHS:
                with self.subTest(relative=relative), self.assertRaisesRegex(RuntimeError, 'Internal firmware sample'):
                    builder.validate_public_file(sample, Path(relative))

    def test_renamed_decimal_and_hex_known_blocks_are_rejected(self):
        block = bytes(range(256)) * 2
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder)/'renamed.c'
            with patch.object(builder, 'KNOWN_FIRMWARE_BLOCKS', {hashlib.sha256(block).hexdigest()}):
                for kind, size, values in (('unsigned char', '512', ','.join(map(str, block))),
                                           ('uint8_t', '0x200', ','.join(hex(v) for v in block))):
                    sample.write_text(f'static const {kind} unrelated_name[{size}] = {{{values}}};\n')
                    with self.assertRaisesRegex(RuntimeError, 'Known firmware block'):
                        builder.validate_public_file(sample, Path('src/renamed.c'))

    def test_synthetic_or_uninitialized_buffers_are_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder)/'synthetic.c'
            sample.write_text('unsigned char buf[512];\nstatic const unsigned char data[512] = {'
                              + ','.join(['0'] * 512) + '};\n')
            builder.validate_public_file(sample, Path('tests/synthetic.c'))


if __name__ == '__main__':
    unittest.main()
