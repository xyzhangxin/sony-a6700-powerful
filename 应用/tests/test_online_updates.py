import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import tempfile
import unittest
import io
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from a6700_unlock.capability_packages import builtin_adapter_set
from a6700_unlock.audio_packages import _digest
from a6700_unlock.catalog import load_catalog
from a6700_unlock.device_profile import load_device_profile
from a6700_unlock.online_updates import (
    HttpResponse, ReviewedRedirectHandler, UpdateClient, UpdateNetworkError,
    UrllibTransport, envelope_etag)
from a6700_unlock.package_updates import LocalUpdateCache


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def stamp(value):
    return value.strftime('%Y-%m-%dT%H:%M:%S.') + f'{value.microsecond // 1000:03d}Z'


def audio_bundle_bytes():
    wav = (b'RIFF' + (40).to_bytes(4, 'little') + b'WAVE' +
           b'fmt ' + (16).to_bytes(4, 'little') +
           b'\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00' +
           b'data' + (4).to_bytes(4, 'little') + b'\x00\x00\x00\x00')
    body = dict(schema='a6700.audio-package/v1', package_id='community-shutters',
                version='1.0.0', release_sequence=1, min_core_api=1, kind='audio-asset',
                title='Community shutters', description='Community shutter sounds.',
                author='Morris', license='CC-BY-4.0',
                sounds=[dict(sound_id='camera-shutter', title='Camera Shutter',
                             description='A short shutter sound.', filename='camera-shutter.wav',
                             bytes=len(wav), sha256=hashlib.sha256(wav).hexdigest(),
                             duration_ms=1, sample_rate=48000, channels=1, bit_depth=16,
                             codec='pcm-wav', tags=['shutter'])])
    manifest = dict(body, digest=_digest(body))
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('package.json', json.dumps(
            manifest, sort_keys=True, separators=(',', ':')))
        archive.writestr('camera-shutter.wav', wav)
    return output.getvalue()


class FakeTransport:
    def __init__(self):
        self.responses = {}
        self.calls = []
    def get(self, url, *, headers, max_bytes, timeout):
        self.calls.append(dict(url=url, headers=headers, max_bytes=max_bytes, timeout=timeout))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class OnlineUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = LocalUpdateCache(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.trusted = {'test-key': base64.b64encode(public).decode()}
        self.adapter = builtin_adapter_set(load_device_profile(load_catalog()))
        self.data = canonical(self.adapter)
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.audio_data = audio_bundle_bytes()
        self.audio_sha = hashlib.sha256(self.audio_data).hexdigest()
        self.now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
        self.transport = FakeTransport()
        self.client = UpdateClient(self.cache, self.trusted, transport=self.transport)
        self.index_url = 'https://powerful6700-api.susukele.com/v1/updates/stable/index.json'
        self.artifact_url = 'https://github.com/xyzhangxin/sony-a6700-powerful/releases/download/v0.4.1/adapter.json'
        self.audio_url = 'https://github.com/xyzhangxin/sony-a6700-powerful/releases/download/v0.4.1/audio.zip'

    def make_index(self, expires_delta=3600, audio=False):
        artifact = dict(id='builtin-adapter', version='0.4.1', url=self.artifact_url,
                        source_url=self.artifact_url, bytes=len(self.data), sha256=self.sha,
                        kind='adapter-set', platform='camera', arch='arm64', min_core_api=1)
        payload = dict(schema_version=1, sequence=1, channel='stable',
                       generated_at=stamp(self.now-timedelta(seconds=60)),
                       expires_at=stamp(self.now+timedelta(seconds=expires_delta)),
                       artifacts=[artifact] + ([dict(
                           id='community-audio', version='1.0.0', url=self.artifact_url,
                           source_url=self.artifact_url, bytes=len(self.audio_data),
                           sha256=self.audio_sha, kind='audio-bundle', platform='any',
                           arch='any', min_core_api=1)] if audio else []), revoked=[])
        raw_payload = canonical(payload)
        envelope = dict(payload=base64.b64encode(raw_payload).decode(),
                        signature=base64.b64encode(self.private.sign(raw_payload)).decode(),
                        key_id='test-key')
        return canonical(envelope)

    def respond_index(self, raw, status=200):
        self.transport.responses[self.index_url] = HttpResponse(status, {'ETag': envelope_etag(raw)}, raw)

    def respond_artifact(self, status=200):
        self.transport.responses[self.artifact_url] = HttpResponse(status, {}, self.data)

    def respond_audio(self, status=200, data=None):
        self.transport.responses[self.audio_url] = HttpResponse(
            status, {}, self.audio_data if data is None else data)

    def test_download_activate_then_etag_304_reuses_verified_cache(self):
        raw = self.make_index()
        self.respond_index(raw)
        self.respond_artifact()
        result = self.client.check('stable', self.now)
        self.assertEqual(result['status'], 'activated')
        self.assertEqual(result['audio_cache_status'], 'active')
        self.assertEqual(result['audio_bundles'], 0)
        self.assertEqual(len(self.transport.calls), 2)
        self.transport.responses[self.index_url] = HttpResponse(304, {'ETag': envelope_etag(raw)}, b'')
        result = self.client.check('stable', self.now)
        self.assertEqual(result['status'], 'not-modified')
        self.assertEqual(result['cache_status'], 'active')
        self.assertEqual(self.transport.calls[-1]['headers']['If-None-Match'], envelope_etag(raw))

    def test_304_without_matching_cache_is_rejected(self):
        self.transport.responses[self.index_url] = HttpResponse(304, {}, b'')
        self.assertEqual(self.client.check('stable', self.now)['status'], 'rejected')

    def test_503_is_unavailable_not_latest_and_keeps_existing_cache(self):
        raw = self.make_index()
        self.respond_index(raw); self.respond_artifact()
        self.client.check('stable', self.now)
        self.transport.responses[self.index_url] = HttpResponse(503, {}, b'{}')
        result = self.client.check('stable', self.now)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['cache_status'], 'active')
        self.assertEqual(result['sequence'], 1)

    def test_network_failure_keeps_offline_cache(self):
        raw = self.make_index()
        self.respond_index(raw); self.respond_artifact()
        self.client.check('stable', self.now)
        self.transport.responses[self.index_url] = UpdateNetworkError('synthetic offline')
        result = self.client.check('stable', self.now)
        self.assertEqual(result['status'], 'offline')
        self.assertEqual(result['cache_status'], 'active')

    def test_artifact_http_error_does_not_activate_index(self):
        raw = self.make_index()
        self.respond_index(raw); self.respond_artifact(503)
        result = self.client.check('stable', self.now)
        self.assertEqual(result['status'], 'artifact-unavailable')
        self.assertFalse(self.cache.indexes.exists())

    def test_audio_bundle_is_downloaded_with_its_own_size_limit_and_cached(self):
        # Use a distinct URL so the adapter and audio artifacts are both fetched.
        raw = self.make_index(audio=True)
        # Replace the audio URL in the signed payload and resign for this test.
        payload = json.loads(base64.b64decode(json.loads(raw)['payload']))
        payload['artifacts'][1]['url'] = self.audio_url
        payload['artifacts'][1]['source_url'] = self.audio_url
        raw_payload = canonical(payload)
        raw = canonical(dict(
            payload=base64.b64encode(raw_payload).decode(),
            signature=base64.b64encode(self.private.sign(raw_payload)).decode(),
            key_id='test-key'))
        self.respond_index(raw)
        self.respond_artifact()
        self.respond_audio()
        result = self.client.check('stable', self.now)
        self.assertEqual(result['status'], 'activated')
        self.assertEqual(result['audio_cache_status'], 'active')
        self.assertEqual(result['audio_bundles'], 1)
        self.assertEqual(self.transport.calls[2]['max_bytes'], 68 * 1024 * 1024)
        active = self.cache.active_audio_bundles('stable', self.trusted, self.now)
        self.assertEqual(active['status'], 'active')
        self.assertEqual(len(active['bundles']), 1)

    def test_expired_response_is_rejected_and_expired_cache_still_allows_304(self):
        self.respond_index(self.make_index(expires_delta=-60))
        self.assertEqual(self.client.check('stable', self.now)['status'], 'rejected')
        raw = self.make_index(expires_delta=60)
        self.respond_index(raw); self.respond_artifact()
        self.client.check('stable', self.now)
        later = self.now + timedelta(hours=2)
        self.transport.responses[self.index_url] = HttpResponse(304, {}, b'')
        result = self.client.check('stable', later)
        self.assertEqual(result['status'], 'not-modified')
        self.assertEqual(result['cache_status'], 'cached-expired')

    def test_redirect_and_service_url_policy(self):
        with self.assertRaises(ValueError):
            UrllibTransport('http://powerful6700-api.susukele.com')
        handler = ReviewedRedirectHandler()
        with self.assertRaises(Exception):
            handler.redirect_request(None, None, 302, 'Found', {}, 'https://evil.example/file')
        request = handler.redirect_request(__import__('urllib.request', fromlist=['Request']).Request(
            'https://github.com/xyzhangxin/sony-a6700-powerful/releases/download/v0.4.1/adapter.json'),
            None, 302, 'Found', {}, 'https://objects.githubusercontent.com/asset?signature=allowed')
        self.assertEqual(request.full_url, 'https://objects.githubusercontent.com/asset?signature=allowed')


if __name__ == '__main__':
    unittest.main()
