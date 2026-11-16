import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError, URLError

from a6700_unlock.desktop.app_updates import check_app_update, download_app_update, download_install_signature
from a6700_unlock.desktop.protocol import capabilities, decode_request


def release(version='0.4.10', **overrides):
    return dict({
        'tag_name': f'v{version}',
        'draft': False,
        'prerelease': False,
        'html_url': 'https://example.invalid/a-different-download',
        'assets': [
            {'name': f'Powerful6700-{version}-macOS-arm64.zip', 'state': 'uploaded', 'size': 1024, 'digest': 'sha256:' + 'a' * 64},
            {'name': f'Powerful6700-{version}-Windows-x64.zip', 'state': 'uploaded', 'size': 1024, 'digest': 'sha256:' + 'a' * 64},
        ],
    }, **overrides)


class AppUpdateTests(unittest.TestCase):
    def check(self, current='0.4.3', payload=None, system='darwin', machine='arm64'):
        return check_app_update(current, system=system, machine=machine,
                                fetch=lambda: release() if payload is None else payload)

    def test_newer_release_selects_matching_platform_asset(self):
        for system, machine, suffix in (('darwin', 'arm64', 'macOS-arm64'), ('win32', 'AMD64', 'Windows-x64')):
            filename = f'Powerful6700-0.4.10-{suffix}.zip'
            self.assertEqual(self.check(system=system, machine=machine),
                             {'status': 'available', 'version': '0.4.10', 'filename': filename,
                              'size': 1024, 'sha256': 'a' * 64})

    def test_numeric_comparison_and_stable_supersedes_matching_dev(self):
        for current, status in (('0.4.9', 'available'), ('0.4.10.dev2', 'available'),
                                ('0.4.10', 'up-to-date'), ('0.4.11.dev1', 'up-to-date'),
                                ('0.5.0', 'up-to-date')):
            with self.subTest(current=current):
                self.assertEqual(self.check(current)['status'], status)

    def test_draft_prerelease_missing_asset_or_invalid_tags_never_offer(self):
        candidates = (release(draft=True), release(prerelease=True), release(assets=[]),
                      release(tag_name='v0.04.10'), release(tag_name='v0.4.10-rc1'),
                      release(tag_name='v0.4.10%2Fother'), release(assets=[{'name': 'Powerful6700-0.4.10-Windows-x64.zip', 'state': 'uploaded'}]),
                      ['unexpected'])
        for payload in candidates:
            with self.subTest(payload=payload):
                self.assertEqual(self.check(payload=payload)['status'], 'unavailable')

    def test_offline_and_rate_limit_are_distinct_from_up_to_date(self):
        for failure, status in ((URLError('offline'), 'offline'), (TimeoutError(), 'offline'),
                                (HTTPError('', 429, 'limited', {}, None), 'rate-limited'),
                                (HTTPError('', 404, 'missing', {}, None), 'unavailable')):
            def fetch():
                raise failure
            with self.subTest(status=status):
                result = check_app_update('0.4.3', system='darwin', machine='arm64', fetch=fetch)
                self.assertEqual(result, {'status': status})

    def test_command_has_no_url_or_camera_arguments(self):
        request = decode_request('{"command":"check_app_update","request_id":"r1"}')
        self.assertEqual(request['request_id'], 'r1')
        with self.assertRaises(ValueError):
            decode_request('{"command":"check_app_update","url":"https://example.invalid"}')
        self.assertIn('check_app_update', capabilities('physical')['commands'])
        self.assertNotIn('check_app_update', capabilities('simulation')['commands'])

    def test_download_is_bound_to_offered_asset_and_checks_bytes_before_publish(self):
        data = b'PK test bytes'
        offer = self.check()
        offer.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        seen = []
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            def open_asset(url):
                seen.append(url)
                return BytesIO(data)
            target = download_app_update(offer, directory, lambda _: None, open_asset=open_asset)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(seen, ['https://github.com/xyzhangxin/sony-a6700-powerful/releases/download/v0.4.10/Powerful6700-0.4.10-macOS-arm64.zip'])
            with self.assertRaises(ValueError):
                download_app_update(offer, directory, lambda _: None,
                                    open_asset=lambda _: BytesIO(b'x' * len(data)))
            self.assertEqual(list(directory.iterdir()), [target])
            self.assertEqual(target.read_bytes(), data)

    def test_only_mac_offer_with_matching_detached_signature_enables_install(self):
        release_info = release()
        name = 'Powerful6700-0.4.10-macOS-arm64.zip'
        release_info['assets'].append({'name': name + '.ed25519', 'state': 'uploaded', 'size': 64})
        offer = self.check(payload=release_info)
        self.assertIs(offer.get('installable'), True)
        self.assertNotIn('installable', self.check(payload=release_info, system='win32', machine='AMD64'))
        with TemporaryDirectory() as directory:
            archive = Path(directory) / name
            seen = []
            def fetch(url):
                seen.append(url)
                return BytesIO(b's' * 64)
            download_install_signature(offer, archive, open_asset=fetch)
            self.assertEqual(archive.with_name(name + '.ed25519').read_bytes(), b's' * 64)
            self.assertTrue(seen[0].endswith('/v0.4.10/' + name + '.ed25519'))
            with self.assertRaises(ValueError):
                download_install_signature(offer, archive, open_asset=lambda _: BytesIO(b'partial'))
