import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
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
from a6700_unlock.package_updates import LocalUpdateCache, UpdateError, verify_index_envelope


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def timestamp(value):
    return value.strftime('%Y-%m-%dT%H:%M:%S.') + f'{value.microsecond // 1000:03d}Z'


def pcm_wav():
    payload = b'\x00\x00' * 2
    return (b'RIFF' + (len(payload) + 36).to_bytes(4, 'little') + b'WAVE' +
            b'fmt ' + (16).to_bytes(4, 'little') +
            b'\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00' +
            b'data' + len(payload).to_bytes(4, 'little') + payload)


def audio_bundle_bytes():
    wav = pcm_wav()
    manifest_body = dict(
        schema='a6700.audio-package/v1', package_id='community-shutters',
        version='1.0.0', release_sequence=1, min_core_api=1, kind='audio-asset',
        title='Community shutters', description='Community shutter sounds.',
        author='Morris', license='CC-BY-4.0',
        sounds=[dict(sound_id='camera-shutter', title='Camera Shutter',
                     description='A short shutter sound.', filename='camera-shutter.wav',
                     bytes=len(wav), sha256=hashlib.sha256(wav).hexdigest(),
                     duration_ms=1, sample_rate=48000, channels=1, bit_depth=16,
                     codec='pcm-wav', tags=['shutter'])])
    manifest = dict(manifest_body, digest=_digest(manifest_body))
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('package.json', json.dumps(
            manifest, sort_keys=True, separators=(',', ':')))
        archive.writestr('camera-shutter.wav', wav)
    return output.getvalue()


class PackageUpdateTests(unittest.TestCase):
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

    def make_index(self, sequence=1, revoked=None, generated_delta=-60, expires_delta=3600,
                   audio=True,
                   url='https://github.com/xyzhangxin/sony-a6700-powerful/releases/download/v0.4.1/adapter.json'):
        artifact = dict(id='builtin-adapter', version='0.4.1', url=url, source_url=url,
                        bytes=len(self.data), sha256=self.sha, kind='adapter-set',
                        platform='camera', arch='arm64', min_core_api=1)
        artifacts = [artifact]
        if audio:
            artifacts.append(dict(id='community-audio', version='1.0.0', url=url,
                                  source_url=url, bytes=len(self.audio_data),
                                  sha256=self.audio_sha, kind='audio-bundle',
                                  platform='any', arch='any', min_core_api=1))
        payload = dict(schema_version=1, sequence=sequence, channel='stable',
                       generated_at=timestamp(self.now + timedelta(seconds=generated_delta)),
                       expires_at=timestamp(self.now + timedelta(seconds=expires_delta)),
                       artifacts=artifacts, revoked=revoked or [])
        raw_payload = canonical(payload)
        envelope = dict(payload=base64.b64encode(raw_payload).decode(),
                        signature=base64.b64encode(self.private.sign(raw_payload)).decode(),
                        key_id='test-key')
        return canonical(envelope)

    def verify(self, raw, now=None):
        return verify_index_envelope(raw, self.trusted, 'stable', now or self.now)

    def activate(self, raw, now=None, data=None, audio_data=None, include_audio=True):
        index = self.verify(raw, now)
        artifacts = {'builtin-adapter': self.data if data is None else data}
        if include_audio and any(row['id'] == 'community-audio' for row in index['artifacts']):
            artifacts['community-audio'] = self.audio_data if audio_data is None else audio_data
        return self.cache.activate_index(index, self.trusted, now or self.now, artifacts)

    def test_valid_index_activates_atomically_and_same_sequence_is_idempotent(self):
        raw = self.make_index()
        self.assertEqual(self.activate(raw)['status'], 'activated')
        self.assertEqual(self.activate(raw)['status'], 'unchanged')
        active = self.cache.active_adapter_set('stable', self.trusted, self.now)
        self.assertEqual(active['status'], 'active')
        self.assertEqual(active['adapter_set']['digest'], self.adapter['digest'])
        self.assertEqual(active['sequence'], 1)

    def test_unknown_signature_and_tampered_payload_do_not_mutate_cache(self):
        with self.assertRaises(UpdateError):
            verify_index_envelope(self.make_index(), {}, 'stable', self.now)
        payload = canonical(dict(schema_version=1, sequence=2, channel='stable',
            generated_at=timestamp(self.now), expires_at=timestamp(self.now + timedelta(hours=1)),
            artifacts=[], revoked=[]))
        envelope = dict(payload=base64.b64encode(payload).decode(),
                        signature=base64.b64encode(self.private.sign(payload + b'x')).decode(),
                        key_id='test-key')
        with self.assertRaises(UpdateError):
            self.verify(canonical(envelope))
        self.assertFalse(self.cache.indexes.exists())

    def test_old_sequence_and_same_sequence_different_content_are_rejected(self):
        self.activate(self.make_index(sequence=2))
        with self.assertRaises(UpdateError):
            self.activate(self.make_index(sequence=1))
        with self.assertRaises(UpdateError):
            self.activate(self.make_index(sequence=2, revoked=[self.adapter['digest']]))
        active = self.cache.active_adapter_set('stable', self.trusted, self.now, for_write=False)
        self.assertEqual(active['sequence'], 2)

    def test_expired_index_cannot_activate_but_existing_cache_remains_offline_usable(self):
        self.activate(self.make_index(expires_delta=60))
        later = self.now + timedelta(hours=2)
        active = self.cache.active_adapter_set('stable', self.trusted, later)
        self.assertEqual(active['status'], 'cached-expired')
        with self.assertRaises(UpdateError):
            self.verify(self.make_index(sequence=2, expires_delta=-60), later)

    def test_revoked_adapter_stops_new_writes_but_not_diagnostics(self):
        self.activate(self.make_index(revoked=[self.adapter['digest']]))
        with self.assertRaises(UpdateError):
            self.cache.active_adapter_set('stable', self.trusted, self.now)
        diagnostic = self.cache.active_adapter_set('stable', self.trusted, self.now, for_write=False)
        self.assertEqual(diagnostic['adapter_set']['digest'], self.adapter['digest'])

    def test_bad_artifact_bytes_and_later_cache_corruption_are_rejected(self):
        with self.assertRaises(UpdateError):
            self.activate(self.make_index(), data=b'not-the-adapter')
        self.assertFalse(self.cache.indexes.exists())
        self.activate(self.make_index())
        path = self.cache.artifacts / self.sha
        path.write_bytes(b'corrupted')
        with self.assertRaises(UpdateError):
            self.cache.active_adapter_set('stable', self.trusted, self.now)

    def test_symlinked_cache_artifact_is_not_accepted(self):
        self.activate(self.make_index())
        path = self.cache.artifacts / self.sha
        path.unlink()
        path.symlink_to(self.cache.root / 'outside.json')
        with self.assertRaises(UpdateError):
            self.cache.active_adapter_set('stable', self.trusted, self.now)

    def test_duplicate_json_keys_and_unreviewed_url_are_rejected(self):
        duplicate = b'{"payload":"eA==","payload":"eA==","signature":"eA==","key_id":"test-key"}'
        with self.assertRaises(UpdateError):
            self.verify(duplicate)
        with self.assertRaises(UpdateError):
            self.verify(self.make_index(url='https://example.com/adapter.json'))
        with self.assertRaises(UpdateError):
            verify_index_envelope(b'[' * 13 + b'0' + b']' * 13,
                                  self.trusted, 'stable', self.now)
        with self.assertRaises(UpdateError):
            verify_index_envelope(b'{"payload":NaN,"signature":"eA==","key_id":"test-key"}',
                                  self.trusted, 'stable', self.now)

    def test_activation_reverifies_the_exact_envelope(self):
        index = self.verify(self.make_index())
        index['raw_envelope'] = b'{}'
        with self.assertRaises(UpdateError):
            self.cache.activate_index(index, self.trusted, self.now,
                                      {'builtin-adapter': self.data})
        self.assertFalse(self.cache.indexes.exists())

    def test_signed_audio_bundle_is_cached_and_active_without_write_authority(self):
        self.assertEqual(self.activate(self.make_index())['status'], 'activated')
        active = self.cache.active_audio_bundles('stable', self.trusted, self.now)
        self.assertEqual(active['status'], 'active')
        self.assertEqual(len(active['bundles']), 1)
        bundle = active['bundles'][0]['bundle']
        self.assertEqual(bundle['manifest']['package_id'], 'community-shutters')
        self.assertEqual(bundle['entries']['camera-shutter.wav']['bytes'], 48)
        adapter = self.cache.active_adapter_set('stable', self.trusted, self.now)
        self.assertEqual(adapter['adapter_set']['digest'], self.adapter['digest'])

    def test_missing_or_invalid_audio_bundle_blocks_index_activation(self):
        index = self.verify(self.make_index())
        with self.assertRaises(UpdateError):
            self.cache.activate_index(index, self.trusted, self.now,
                                      {'builtin-adapter': self.data})
        self.assertFalse(self.cache.indexes.exists())
        with self.assertRaises(UpdateError):
            self.activate(self.make_index(), audio_data=b'not-an-audio-bundle')
        self.assertFalse(self.cache.indexes.exists())

    def test_audio_bundle_must_be_platform_independent(self):
        raw = self.make_index()
        # Rebuild only the signed payload after changing the audio artifact platform.
        # This must fail before activation because the signed index semantics are invalid.
        payload = json.loads(base64.b64decode(json.loads(raw)['payload']))
        payload['artifacts'][1]['platform'] = 'camera'
        raw_payload = canonical(payload)
        envelope = dict(payload=base64.b64encode(raw_payload).decode(),
                        signature=base64.b64encode(self.private.sign(raw_payload)).decode(),
                        key_id='test-key')
        with self.assertRaisesRegex(UpdateError, '平台无关'):
            self.verify(canonical(envelope))

    def test_revoked_audio_bundle_stops_new_import_but_remains_diagnostic(self):
        self.activate(self.make_index(revoked=[self.audio_sha]))
        with self.assertRaises(UpdateError):
            self.cache.active_audio_bundles('stable', self.trusted, self.now)
        diagnostic = self.cache.active_audio_bundles(
            'stable', self.trusted, self.now, for_write=False)
        self.assertEqual(len(diagnostic['bundles']), 1)


if __name__ == '__main__':
    unittest.main()
