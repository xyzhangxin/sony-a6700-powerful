import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from build_macos_app import stage_wheel


class UpdatePackagingTests(unittest.TestCase):
    def test_runtime_pins_cover_all_signature_dependencies(self):
        pins = json.loads((Path(__file__).resolve().parents[1] / 'desktop/runtime-dependencies.json').read_text())
        self.assertEqual(pins['cryptography']['version'], '46.0.3')
        self.assertEqual(pins['cffi']['version'], '2.1.1')
        self.assertEqual(pins['pycparser']['version'], '3.12')
        self.assertIn('cp313-cp313-macosx_11_0_arm64', pins['cffi']['filename'])
        for name, pin in pins.items():
            self.assertTrue(pin['url'].startswith('https://'), name)
            if 'sha256' in pin:
                self.assertEqual(len(pin['sha256']), 64, name)
            else:
                self.assertTrue(pin['binary_url'].startswith('https://'), name)
                self.assertEqual(len(pin['source_sha256']), 64, name)
                self.assertEqual(len(pin['binary_sha256']), 64, name)

    def test_wheel_stager_extracts_package_and_licenses(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wheel = root / 'demo.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('demo/__init__.py', 'VALUE = 1')
                archive.writestr('_cffi_backend.txt', 'native placeholder')
                archive.writestr('demo-1.0.dist-info/licenses/LICENSE', 'license text')
            resources = root / 'resources'
            stage_wheel(wheel, resources, 'demo')
            self.assertEqual((resources / 'python/demo/__init__.py').read_text(), 'VALUE = 1')
            self.assertEqual((resources / 'runtime-licenses/demo-LICENSE').read_text(), 'license text')

    def test_cffi_top_level_backend_is_included(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wheel = root / 'cffi-demo.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('_cffi_backend.txt', 'native placeholder')
                archive.writestr('cffi-1.0.dist-info/LICENSE', 'license')
            resources = root / 'resources'
            stage_wheel(wheel, resources, 'cffi')
            self.assertEqual((resources / 'python/_cffi_backend.txt').read_text(), 'native placeholder')

    def test_wheel_stager_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wheel = root / 'bad.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('../outside.py', 'bad')
                archive.writestr('demo-1.0.dist-info/LICENSE', 'license')
            with self.assertRaises(ValueError):
                stage_wheel(wheel, root / 'resources', 'demo')


if __name__ == '__main__':
    unittest.main()
