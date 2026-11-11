"""User-visible legal notices must work offline without touching a device."""
import contextlib
from importlib.resources import files
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from a6700_unlock.cli import main


class LicensingTests(unittest.TestCase):
    def test_local_license_commands_do_not_invoke_device_operations(self):
        for arguments, expected in [(['license'], 'GPL-3.0-only'),
                                    (['license', '--full'], '17. Interpretation of Sections 15 and 16.'),
                                    (['license', '--third-party'], 'ma1co/Sony-PMCA-RE')]:
            with self.subTest(arguments=arguments), patch('a6700_unlock.cli._device') as device, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(arguments), 0)
                self.assertIn(expected, output.getvalue())
                device.assert_not_called()

    def test_embedded_legal_notices_match_source_distribution(self):
        root = Path(__file__).resolve().parents[1]
        legal = files('a6700_unlock').joinpath('data/legal')
        for original, embedded in [('LICENSE', 'LICENSE.txt'), ('NOTICE', 'NOTICE.txt'),
                                   ('THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md')]:
            self.assertEqual((root/original).read_bytes(), legal.joinpath(embedded).read_bytes())


if __name__ == '__main__':
    unittest.main()
