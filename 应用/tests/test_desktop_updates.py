import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import base64
from datetime import datetime, timezone
import hashlib
import zipfile

from a6700_unlock.audio_packages import (
    _digest, inspect_pcm_wav, normalize_camera_clip,
)
from a6700_unlock.audio_import import build_audio_import_plan
from a6700_unlock.capability_packages import _digest, builtin_adapter_set
from a6700_unlock.catalog import load_catalog
from a6700_unlock.desktop import physical_bridge
from a6700_unlock.desktop.controller import DesktopController
from a6700_unlock.desktop.protocol import capabilities
from a6700_unlock.desktop.update_service import DesktopUpdateService
from a6700_unlock.desktop.physical import PhysicalService
from a6700_unlock.device_profile import load_device_profile
from a6700_unlock.package_updates import verify_index_envelope


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def stamp(value):
    return value.strftime('%Y-%m-%dT%H:%M:%S.') + f'{value.microsecond // 1000:03d}Z'


def pcm_wav():
    payload = b'\x00\x00' * 2
    return (b'RIFF' + (len(payload) + 36).to_bytes(4, 'little') + b'WAVE' +
            b'fmt ' + (16).to_bytes(4, 'little') +
            b'\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00' +
            b'data' + len(payload).to_bytes(4, 'little') + payload)


def audio_bundle_bytes():
    wav = pcm_wav()
    wav2 = pcm_wav()[:-1] + b'\x01'
    wav3 = pcm_wav()[:-1] + b'\x02'
    body = dict(schema='a6700.audio-package/v1', package_id='community-shutters',
                version='1.0.0', release_sequence=1, min_core_api=1, kind='audio-asset',
                title='Community shutters', description='Community shutter sounds.',
                author='Morris', license='CC-BY-4.0',
                sounds=[dict(sound_id='camera-shutter', title='Camera Shutter',
                             description='A short shutter sound.', filename='camera-shutter.wav',
                             bytes=len(wav), sha256=hashlib.sha256(wav).hexdigest(),
                             duration_ms=1, sample_rate=48000, channels=1, bit_depth=16,
                             codec='pcm-wav', tags=['shutter']),
                        dict(sound_id='camera-shutter-2', title='Camera Shutter 2',
                             description='A second shutter sound.', filename='camera-shutter-2.wav',
                             bytes=len(wav2), sha256=hashlib.sha256(wav2).hexdigest(),
                             duration_ms=1, sample_rate=48000, channels=1, bit_depth=16,
                             codec='pcm-wav', tags=['shutter']),
                        dict(sound_id='camera-shutter-3', title='Camera Shutter 3',
                             description='A third shutter sound.', filename='camera-shutter-3.wav',
                             bytes=len(wav3), sha256=hashlib.sha256(wav3).hexdigest(),
                             duration_ms=1, sample_rate=48000, channels=1, bit_depth=16,
                             codec='pcm-wav', tags=['shutter'])])
    manifest = dict(body, digest=_digest(body))
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('package.json', json.dumps(
            manifest, sort_keys=True, separators=(',', ':')))
        archive.writestr('camera-shutter.wav', wav)
        archive.writestr('camera-shutter-2.wav', wav2)
        archive.writestr('camera-shutter-3.wav', wav3)
    return output.getvalue()


class DesktopUpdateTests(unittest.TestCase):
    def test_unconfigured_service_never_calls_transport(self):
        class RejectTransport:
            def get(self, *args, **kwargs):
                raise AssertionError('network must not be used without a trust key')
        with tempfile.TemporaryDirectory() as folder:
            service = DesktopUpdateService(folder, trusted_keys={}, transport=RejectTransport())
            result = service.check()
        self.assertEqual(result, dict(status='unconfigured', cache_status='unavailable'))

    def test_audio_import_plan_reads_custom_sound_adapter_state(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = builtin_adapter_set(load_device_profile(load_catalog()))
            package = next(row for row in adapter['packages']
                          if row['package_id'] == 'custom_shutter_sound')
            package['available'] = True
            body = dict(adapter)
            body.pop('digest')
            adapter = dict(body, digest=_digest(body))
            local_path = Path(folder) / 'camera-shutter.wav'
            local_path.write_bytes(pcm_wav())
            service = DesktopUpdateService(
                folder, trusted_keys={}, adapter_set=adapter)
            plan = service.plan_audio_import('local', path=str(local_path))
            self.assertTrue(plan['capability']['available'])
            self.assertFalse(plan['capability']['implementation_ready'])
            self.assertFalse(plan['can_execute'])
            self.assertEqual(plan['reasons'][0]['id'],
                             'custom-shutter-sound-import-not-implemented')
            self.assertEqual(plan['evidence']['camera_access'], 'not-run')
            self.assertFalse(plan['evidence']['write_attempted'])

    def test_blocked_import_plan_cannot_reach_physical_executor(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / 'camera-shutter.wav'
            wav.write_bytes(pcm_wav())
            candidate = inspect_pcm_wav(pcm_wav(), 'camera-shutter.wav')
            plan = build_audio_import_plan(candidate, dict(type='local'))
            service = PhysicalService(
                folder, backend_factory=lambda progress: None,
                backup_dir=Path(folder) / 'backups')
            with self.assertRaisesRegex(ValueError, '拒绝进入相机执行'):
                service.execute_audio_import(plan, pcm_wav())
            self.assertFalse(service.connected)
            self.assertFalse(getattr(service, 'write_attempted', False))

    def test_ready_import_prepares_only_confirmed_selected_slot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'selected.wav'
            path.write_bytes(pcm_wav())
            service = DesktopUpdateService(folder, trusted_keys={}, audio_import_ready=True)
            plan = service.plan_audio_import('local', path=str(path), target_slot=10)
            self.assertTrue(plan['can_execute'])
            with self.assertRaises(ValueError):
                service.prepare_audio_import_execution(plan['digest'])
            service.confirm_audio_import(plan['digest'], True)
            loaded, data = service.prepare_audio_import_execution(plan['digest'])
            self.assertEqual(loaded['target']['slot'],10)
            self.assertEqual(data,normalize_camera_clip(pcm_wav()))
            self.assertEqual(loaded['candidate']['duration_ms'],350)
            self.assertEqual(loaded['candidate']['sample_rate'],24000)
            self.assertEqual(loaded['candidate']['bytes'],44+8400*2)
            over_long = Path(folder)/'over350ms.wav'
            import struct
            long_payload = b'\x01\x00' * 12000
            over_long.write_bytes(
                b'RIFF' + (len(long_payload) + 36).to_bytes(4, 'little') + b'WAVEfmt '
                + struct.pack('<IHHIIHH', 16, 1, 1, 24000, 48000, 2, 16)
                + b'data' + len(long_payload).to_bytes(4, 'little') + long_payload)
            with self.assertRaisesRegex(ValueError, '350 毫秒'):
                service.plan_audio_import('local', path=str(over_long), target_slot=3)
            with self.assertRaises(ValueError):
                service.plan_audio_import('local',path=str(path),target_slot=0)

    def test_export_audio_package_creates_reusable_local_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / 'camera-shutter.wav'
            wav.write_bytes(pcm_wav())
            service = DesktopUpdateService(folder, trusted_keys={})
            result = service.export_audio_package(
                str(wav), package_id='local-shutters', version='1.0.0',
                title='Local shutters', description='Local shutter sounds.',
                author='Morris', license='CC-BY-4.0', sound_title='Camera Shutter')
            self.assertEqual(result['status'], 'exported')
            self.assertEqual(result['package_id'], 'local-shutters')
            self.assertEqual(result['sound_count'], 1)
            self.assertEqual(result['bundle_bytes'], Path(result['export_path']).stat().st_size)
            repeat = service.export_audio_package(
                str(wav), package_id='local-shutters', version='1.0.0',
                title='Local shutters', description='Local shutter sounds.',
                author='Morris', license='CC-BY-4.0', sound_title='Camera Shutter')
            self.assertEqual(result, repeat)
            with self.assertRaisesRegex(ValueError, '同名音频包导出已存在'):
                service.export_audio_package(
                    str(wav), package_id='local-shutters', version='1.0.0',
                    title='Changed shutters', description='Local shutter sounds.',
                    author='Morris', license='CC-BY-4.0', sound_title='Camera Shutter')

    def test_unconfigured_audio_catalog_is_empty_and_never_touches_transport(self):
        class RejectTransport:
            def get(self, *args, **kwargs):
                raise AssertionError('audio catalog must not use network')
        with tempfile.TemporaryDirectory() as folder:
            service = DesktopUpdateService(folder, trusted_keys={}, transport=RejectTransport())
            result = service.audio_catalog()
        self.assertEqual(result, dict(status='unconfigured', packages=[]))

    def test_update_command_is_physical_only(self):
        self.assertIn('check_updates', capabilities('physical')['commands'])
        self.assertNotIn('check_updates', capabilities('simulation')['commands'])
        self.assertIn('list_audio', capabilities('physical')['commands'])
        self.assertNotIn('list_audio', capabilities('simulation')['commands'])
        self.assertIn('preview_audio', capabilities('physical')['commands'])
        self.assertNotIn('preview_audio', capabilities('simulation')['commands'])
        self.assertIn('export_audio_package', capabilities('physical')['commands'])
        self.assertNotIn('export_audio_package', capabilities('simulation')['commands'])
        self.assertIn('plan_audio_import', capabilities('physical')['commands'])
        self.assertNotIn('plan_audio_import', capabilities('simulation')['commands'])
        self.assertIn('plan_audio_import_batch', capabilities('physical')['commands'])
        self.assertNotIn('plan_audio_import_batch', capabilities('simulation')['commands'])
        self.assertIn('confirm_audio_import', capabilities('physical')['commands'])
        self.assertNotIn('confirm_audio_import', capabilities('simulation')['commands'])
        self.assertIn('gate_audio_import', capabilities('physical')['commands'])
        self.assertNotIn('gate_audio_import', capabilities('simulation')['commands'])
        self.assertIn('confirm_audio_import_batch', capabilities('physical')['commands'])
        self.assertNotIn('confirm_audio_import_batch', capabilities('simulation')['commands'])
        self.assertIn('gate_audio_import_batch', capabilities('physical')['commands'])
        self.assertNotIn('gate_audio_import_batch', capabilities('simulation')['commands'])

    def test_audio_catalog_lists_verified_cache_without_paths(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        with tempfile.TemporaryDirectory() as folder:
            private = Ed25519PrivateKey.generate()
            public = private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            keys = {'test-key': base64.b64encode(public).decode()}
            service = DesktopUpdateService(folder, trusted_keys=keys)
            adapter = builtin_adapter_set(load_device_profile(load_catalog()))
            adapter_data = canonical(adapter)
            audio_data = audio_bundle_bytes()
            url = ('https://github.com/xyzhangxin/sony-a6700-powerful/releases/download/'
                   'v0.4.1/adapter.json')
            now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
            artifacts = [
                dict(id='builtin-adapter', version='0.4.1', url=url, source_url=url,
                     bytes=len(adapter_data), sha256=hashlib.sha256(adapter_data).hexdigest(),
                     kind='adapter-set', platform='camera', arch='arm64', min_core_api=1),
                dict(id='community-audio', version='1.0.0', url=url, source_url=url,
                     bytes=len(audio_data), sha256=hashlib.sha256(audio_data).hexdigest(),
                     kind='audio-bundle', platform='any', arch='any', min_core_api=1),
            ]
            payload = dict(schema_version=1, sequence=1, channel='stable',
                           generated_at=stamp(now), expires_at=stamp(now.fromtimestamp(
                               now.timestamp() + 3600, tz=timezone.utc)),
                           artifacts=artifacts, revoked=[])
            raw_payload = canonical(payload)
            envelope = canonical(dict(
                payload=base64.b64encode(raw_payload).decode(),
                signature=base64.b64encode(private.sign(raw_payload)).decode(),
                key_id='test-key'))
            index = verify_index_envelope(envelope, keys, 'stable', now)
            service.cache.activate_index(index, keys, now, {
                'builtin-adapter': adapter_data, 'community-audio': audio_data})
            result = service.audio_catalog(now)
            self.assertEqual(result['status'], 'active')
            self.assertEqual(len(result['packages']), 1)
            package = result['packages'][0]
            self.assertEqual(package['package_id'], 'community-shutters')
            self.assertEqual(package['sounds'][0]['sound_id'], 'camera-shutter')
            self.assertNotIn('path', json.dumps(result))
            self.assertNotIn('filename', json.dumps(result))
            preview = service.audio_preview('community-shutters', 'camera-shutter', now)
            self.assertEqual(preview['status'], 'ready')
            preview_path = Path(preview['path'])
            self.assertEqual(preview_path.parent, Path(folder) / 'audio-previews')
            self.assertEqual(preview_path.stat().st_size, 48)
            self.assertTrue(preview_path.read_bytes().startswith(b'RIFF'))
            with self.assertRaises(ValueError):
                service.audio_preview('community-shutters', 'missing', now)
            market_plan = service.plan_audio_import(
                'market', target_slot=2, package_id='community-shutters',
                sound_id='camera-shutter', now=now)
            self.assertEqual(market_plan['status'], 'blocked')
            self.assertFalse(market_plan['can_execute'])
            self.assertEqual(market_plan['source']['type'], 'market')
            self.assertEqual(market_plan['source']['sound_id'], 'camera-shutter')
            self.assertEqual(market_plan['target']['slot'], 2)
            batch = service.plan_audio_import_batch('community-shutters', now=now)
            self.assertEqual(batch['slot_count'], 3)
            self.assertEqual([plan['target']['slot'] for plan in batch['plans']], [1, 2, 3])
            self.assertEqual([plan['candidate']['sound_id'] if 'sound_id' in plan['candidate'] else
                              plan['candidate']['filename'] for plan in batch['plans']],
                             ['camera-shutter.wav', 'camera-shutter-2.wav',
                              'camera-shutter-3.wav'])
            batch_path = Path(folder) / 'audio-import-batches' / (batch['digest'] + '.json')
            self.assertTrue(batch_path.is_file())
            for plan in batch['plans']:
                subplan_path = Path(folder) / 'audio-import-plans' / (
                    plan['digest'] + '.json')
                self.assertTrue(subplan_path.is_file())
            declined_batch = service.confirm_audio_import_batch(batch['digest'], False)
            self.assertEqual(declined_batch['status'], 'cancelled')
            confirmed_batch = service.confirm_audio_import_batch(batch['digest'], True)
            self.assertEqual(confirmed_batch['status'], 'blocked')
            self.assertEqual(confirmed_batch['slot_count'], batch['slot_count'])
            self.assertEqual(confirmed_batch['camera_access'], 'not-run')
            self.assertFalse(confirmed_batch['write_attempted'])
            batch_confirmation_path = Path(folder) / 'audio-import-batch-confirmations' / (
                batch['digest'] + '.json')
            self.assertTrue(batch_confirmation_path.is_file())
            batch_gate = service.gate_audio_import_batch(batch['digest'])
            self.assertEqual(batch_gate['status'], 'blocked')
            self.assertEqual(batch_gate['batch_digest'], batch['digest'])
            self.assertEqual(len(batch_gate['results']), batch['slot_count'])
            self.assertEqual(batch_gate['camera_access'], 'not-run')
            self.assertFalse(batch_gate['write_attempted'])
            local_path = Path(folder) / 'local-shutter.wav'
            local_path.write_bytes(pcm_wav())
            local_plan = service.plan_audio_import('local', path=str(local_path), now=now)
            self.assertEqual(local_plan['status'], 'blocked')
            self.assertEqual(local_plan['source']['type'], 'local')
            self.assertEqual(local_plan['target']['slot'], 1)
            self.assertEqual(local_plan['candidate']['filename'], 'local-shutter.wav')
            zh_plan = service.plan_audio_import(
                'local', target_slot=5, path=str(local_path),
                filename='清脆快门', now=now)
            self.assertEqual(zh_plan['candidate']['filename'], '清脆快门')
            self.assertEqual(zh_plan['target']['slot'], 5)
            self.assertFalse(local_plan['evidence']['write_attempted'])
            candidate_path = Path(folder) / 'audio-candidates' / (
                local_plan['candidate']['sha256'] + '.wav')
            self.assertTrue(candidate_path.is_file())
            self.assertEqual(service.load_audio_candidate(
                local_plan['candidate']['sha256']), normalize_camera_clip(pcm_wav()))
            plan_path = Path(folder) / 'audio-import-plans' / (local_plan['digest'] + '.json')
            self.assertTrue(plan_path.is_file())
            declined = service.confirm_audio_import(local_plan['digest'], False)
            self.assertEqual(declined['status'], 'cancelled')
            confirmed = service.confirm_audio_import(local_plan['digest'], True)
            self.assertEqual(confirmed['status'], 'blocked')
            self.assertEqual(confirmed['camera_access'], 'not-run')
            self.assertFalse(confirmed['write_attempted'])
            confirmation_path = Path(folder) / 'audio-import-confirmations' / (
                local_plan['digest'] + '.json')
            self.assertTrue(confirmation_path.is_file())
            gate = service.gate_audio_import(local_plan['digest'])
            self.assertEqual(gate['status'], 'blocked')
            self.assertEqual(gate['plan_digest'], local_plan['digest'])
            self.assertEqual(gate['camera_access'], 'not-run')
            self.assertFalse(gate['write_attempted'])
            with self.assertRaisesRegex(ValueError, '能力或导入执行器未就绪'):
                service.prepare_audio_import_execution(local_plan['digest'])
            with self.assertRaises(ValueError):
                service.confirm_audio_import('0' * 64, True)
            with self.assertRaises(ValueError):
                service.plan_audio_import('local', target_slot=11,
                                          path=str(local_path), now=now)
            local_path.unlink()
            self.assertEqual(service.confirm_audio_import(
                local_plan['digest'], True)['status'], 'blocked')
            candidate_path.write_bytes(b'corrupted')
            with self.assertRaises(ValueError):
                service.confirm_audio_import(local_plan['digest'], True)
            candidate_path.unlink()
            candidate_path.symlink_to(plan_path)
            with self.assertRaises(ValueError):
                service.load_audio_candidate(local_plan['candidate']['sha256'])

    def test_audio_inventory_entry_does_not_require_general_inspect(self):
        # Preparation-page audio navigation uses the same independent command
        # as the audio dialog; a failed general profile must not be bypassed
        # or silently retried to obtain this capability-specific observation.
        class ImmediateThread:
            def __init__(self, target, args=(), **kwargs):
                self.target, self.args = target, args
            def start(self): self.target(*self.args)
            def is_alive(self): return False
            def join(self, **kwargs): pass
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            before = controller.snapshot()
            requests = json.dumps(dict(command='refresh_audio_slots', request_id='audio-entry')) + '\n'
            output = io.StringIO()
            result = dict(status='observed', slots=[], device_access=True,
                          persistent_write_attempted=False, cleanup_status='restored')
            with patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                 patch.object(physical_bridge, 'Thread', ImmediateThread), \
                 patch.object(controller.service, 'read_audio_slots', return_value=result) as read, \
                 patch.object(controller.service, 'perform', side_effect=AssertionError('general inspect called')) as general, \
                 patch('a6700_unlock.desktop.update_service.load_trust_keys', return_value={}), \
                 patch('sys.argv', ['test', '--state-dir', folder]), \
                 patch('sys.stdin', io.StringIO(requests)), patch('sys.stdout', output):
                physical_bridge.main()
            read.assert_called_once_with()
            general.assert_not_called()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            observed = next(row for row in messages if row['type'] == 'audio-slots-result')
            self.assertEqual(observed['result'], result)
            self.assertEqual(controller.snapshot()['phase'], before['phase'])
            self.assertEqual(controller.snapshot()['compatible'], before['compatible'])

    def test_bridge_reports_unconfigured_without_camera_or_network(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            requests = (json.dumps(dict(command='check_updates', request_id='req-update')) + '\n'
                        + json.dumps(dict(command='list_audio', request_id='req-audio')) + '\n'
                        + json.dumps(dict(command='preview_audio', request_id='req-preview',
                                          package_id='community', sound_id='missing')) + '\n'
                        + json.dumps(dict(command='export_audio_package',
                                          request_id='req-export', path='/no/such/file.wav',
                                          package_id='local', version='1.0.0',
                                          title='Local', description='Local sound.',
                                          author='Morris', license='CC-BY-4.0')) + '\n'
                        + json.dumps(dict(command='plan_audio_import', request_id='req-plan',
                                          source='local', slot=1,
                                          path='/no/such/file.wav')) + '\n'
                        + json.dumps(dict(command='plan_audio_import_batch',
                                          request_id='req-batch',
                                          package_id='community')) + '\n'
                        + json.dumps(dict(command='confirm_audio_import', request_id='req-confirm',
                                          plan_digest='0' * 64, confirmed=True)) + '\n'
                        + json.dumps(dict(command='gate_audio_import', request_id='req-gate',
                                          plan_digest='0' * 64)) + '\n'
                        + json.dumps(dict(command='confirm_audio_import_batch',
                                          request_id='req-batch-confirm',
                                          batch_digest='0' * 64, confirmed=True)) + '\n'
                        + json.dumps(dict(command='gate_audio_import_batch',
                                          request_id='req-batch-gate',
                                          batch_digest='0' * 64)) + '\n'
                        + json.dumps(dict(command='shutdown')) + '\n')
            output = io.StringIO()
            with patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                 patch('a6700_unlock.desktop.update_service.load_trust_keys', return_value={}), \
                 patch('sys.argv', ['test', '--state-dir', folder]), \
                 patch('sys.stdin', io.StringIO(requests)), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            result = next(row for row in messages if row['type'] == 'update-result')
            self.assertEqual(result['result']['status'], 'unconfigured')
            audio = next(row for row in messages if row['type'] == 'audio-result')
            self.assertEqual(audio['result']['status'], 'unconfigured')
            self.assertEqual(audio['result']['packages'], [])
            self.assertEqual([row['slot'] for row in audio['result']['slots']],
                             list(range(1, 11)))
            self.assertTrue(all(row['status'] == 'unknown'
                                for row in audio['result']['slots']))
            preview = next(row for row in messages if row['type'] == 'audio-preview-result')
            self.assertEqual(preview['result']['status'], 'unconfigured')
            exported = next(row for row in messages if row['type'] == 'audio-export-result')
            self.assertEqual(exported['result']['status'], 'error')
            plan = next(row for row in messages if row['type'] == 'audio-import-plan-result')
            self.assertEqual(plan['result']['status'], 'error')
            batch = next(row for row in messages
                         if row['type'] == 'audio-import-batch-result')
            self.assertEqual(batch['result']['status'], 'error')
            confirmation = next(row for row in messages
                                if row['type'] == 'audio-import-confirmation-result')
            self.assertEqual(confirmation['result']['status'], 'error')
            gate = next(row for row in messages if row['type'] == 'audio-import-gate-result')
            self.assertEqual(gate['result']['status'], 'error')
            batch_confirmation = next(row for row in messages
                                      if row['type'] == 'audio-import-batch-confirmation-result')
            self.assertEqual(batch_confirmation['result']['status'], 'error')
            batch_gate = next(row for row in messages
                              if row['type'] == 'audio-import-batch-gate-result')
            self.assertEqual(batch_gate['result']['status'], 'error')
            self.assertEqual(result['request_id'], 'req-update')
            self.assertEqual(result['operation_id'], controller.operation_id)
            self.assertFalse(controller.device_access)
            self.assertFalse(controller.write_attempted)


if __name__ == '__main__':
    unittest.main()
