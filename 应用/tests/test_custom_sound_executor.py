import unittest
from unittest.mock import Mock, patch
from tempfile import TemporaryDirectory
from pathlib import Path
import base64
import json
from dataclasses import asdict, replace
from a6700_unlock.package_installation import PackageBinding

from a6700_unlock.audio_import import (
    AudioImportTransactionError, build_audio_import_plan,
    confirm_audio_import_plan, gate_audio_import_execution,
    simulate_audio_import_transaction)
from a6700_unlock.audio_packages import inspect_pcm_wav
from a6700_unlock.custom_sound_executor import (
    CustomSoundExecutorError, SlotRecordBackend, UsbSlotRecordTransport,
    prepare_slot_update, execute_slot_update, restore_slot_update,
    load_slot_recovery, bind_slot_recovery, execute_slot_recovery)
from a6700_unlock.custom_sound_native import (
    custom_sound_request, custom_sound_response, load_custom_sound_payload)
from a6700_unlock.custom_sound_records import build_slot_record
from a6700_unlock.device_protocol import DeviceError


def pcm_wav():
    payload = b"\x01\x00" * 2
    return (b"RIFF" + (len(payload) + 36).to_bytes(4, "little") + b"WAVE" +
            b"fmt " + (16).to_bytes(4, "little") +
            b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00" +
            b"data" + len(payload).to_bytes(4, "little") + payload)


class MemoryTransport:
    def __init__(self, readback=True, activate=True):
        self.records = {}
        self.readback_result = readback
        self.activate_result = activate
        self.activated = []
        self.discarded = []

    def write_slot(self, slot, record):
        self.records[slot] = record

    def read_slot(self, slot):
        if not self.readback_result:
            return b"BAD"
        return self.records.get(slot, b"")

    def activate_slot(self, slot, generation):
        if not self.activate_result:
            raise AudioImportTransactionError("activate failed")
        self.activated.append((slot, generation))

    def discard_slot(self, slot, generation):
        self.discarded.append((slot, generation))


class CustomSoundExecutorTests(unittest.TestCase):
    def setUp(self):
        self.binding = PackageBinding("custom_shutter_sound", "ten-slot-radio-main", "b"*64, "a"*64, "c"*64, "ILCE-6700", "2.00")
        self.wav = pcm_wav()
        self.candidate = inspect_pcm_wav(self.wav, "camera-shutter.wav")
        self.plan = build_audio_import_plan(
            self.candidate, dict(type="local"), target_slot=1,
            capability_available=True, implementation_ready=True)
        self.confirmation = confirm_audio_import_plan(self.plan, True)
        self.authorization = gate_audio_import_execution(
            self.plan, self.confirmation, self.wav,
            preflight=dict(fresh=True, compatible=True, capability_installed=True,
                           profile_sha256="profile", selection=2,
                           slot_generations=[0x10000006, 0x20000003] + [0] * 8,
                           setting=dict(cluster_bytes=16384, free_clusters=100,
                                        directory_exists=False,
                                        existing_record_bytes=[None] * 10)),
            lock=dict(held=True, owner="core"), executor_available=True)

    def test_slot_record_backend_persists_and_activates_exact_record(self):
        transport = MemoryTransport()
        backend = SlotRecordBackend(self.plan, transport)
        result = simulate_audio_import_transaction(
            self.plan, self.wav, backend, 0x10000007, self.authorization)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(transport.activated, [(1, 0x10000007)])
        self.assertTrue(transport.records[1])
        self.assertTrue(result["selection_unchanged"])
        self.assertEqual(result["active_generation"], 0x20000003)

    def test_successful_import_publishes_menu_name_once(self):
        transport = MemoryTransport()
        published = []

        def publish(slot, name):
            published.append((slot, name))
            return 0

        backend = SlotRecordBackend(self.plan, transport, name_publisher=publish)
        result = simulate_audio_import_transaction(
            self.plan, self.wav, backend, 0x10000007, self.authorization)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(published, [(1, "camera-shutter.wav")])

    def test_menu_name_failure_is_saved_not_playing(self):
        transport = MemoryTransport()

        def publish(slot, name):
            return 1

        backend = SlotRecordBackend(self.plan, transport, name_publisher=publish)
        result = simulate_audio_import_transaction(
            self.plan, self.wav, backend, 0x10000007, self.authorization)
        self.assertEqual(result["status"], "saved-not-playing")
        self.assertTrue(transport.activated)

    def test_invalid_name_publisher_rejected(self):
        with self.assertRaises(CustomSoundExecutorError):
            SlotRecordBackend(self.plan, MemoryTransport(), name_publisher=object())

    def test_readback_failure_discards_prepared_resource(self):
        transport = MemoryTransport(readback=False)
        backend = SlotRecordBackend(self.plan, transport)
        result = simulate_audio_import_transaction(
            self.plan, self.wav, backend, 0x10000007, self.authorization)
        self.assertEqual(result["status"], "verification-failed")
        self.assertEqual(transport.discarded, [(1, 0x10000007)])
        self.assertEqual(transport.activated, [])

    def test_backend_rejects_plan_and_transport_drift(self):
        with self.assertRaises(CustomSoundExecutorError):
            SlotRecordBackend(dict(self.plan, target=dict(slot=11)), MemoryTransport())
        with self.assertRaises(CustomSoundExecutorError):
            SlotRecordBackend(self.plan, object())
        backend = SlotRecordBackend(self.plan, MemoryTransport())
        with self.assertRaisesRegex(AudioImportTransactionError, "目标槽"):
            backend.prepare(self.wav, 0x10000007, 2)

    def test_native_request_binds_plan_slot_generation_and_record(self):
        record = build_slot_record(self.candidate, self.wav, 1, 0x10000007)
        nonce = bytes(range(16))
        request = custom_sound_request(self.plan, record, nonce)
        self.assertEqual(request[:4], b"CSND")
        self.assertEqual(request[8:24], nonce)
        self.assertEqual(request[24:56], bytes.fromhex(self.plan["digest"]))
        self.assertEqual(request[56:60], (1).to_bytes(4, "little"))
        self.assertEqual(request[60:64], (0x10000007).to_bytes(4, "little"))
        self.assertEqual(request[64:68], len(record).to_bytes(4, "little"))
        self.assertEqual(request[72:], record)

        with self.assertRaises(DeviceError):
            custom_sound_request(self.plan, record[:-1], nonce)

        response_body = (b"A67R" + (1).to_bytes(4, "little") + nonce +
                         (0).to_bytes(4, "little") + (0).to_bytes(4, "little") +
                         (0xFFFFFFFF).to_bytes(4, "little") +
                         (0).to_bytes(4, "little") + (0).to_bytes(4, "little"))
        self.assertEqual(custom_sound_response(response_body, nonce)["status"], 0)

    def test_packaged_native_payload_manifest_is_exact(self):
        payload = load_custom_sound_payload()
        self.assertTrue(payload.startswith(b"\x7fELF"))

    def test_overwrite_uses_observed_generation_and_retains_preimage(self):
        old = build_slot_record(self.plan['candidate'], self.wav, 1, 0x10000007)
        backend = Mock()
        backend.read_custom_sound_slot.return_value = old
        update = prepare_slot_update(backend, self.plan, self.wav)
        self.assertEqual(update['generation'], 0x10000008)
        self.assertEqual(update['previous'], old)
        backend.run_custom_sound_import.assert_not_called()
        backend.read_custom_sound_slot.return_value = None
        self.assertEqual(prepare_slot_update(backend, self.plan, self.wav)['generation'], 0x10000001)

    def test_corrupt_or_exhausted_target_cannot_be_treated_as_empty(self):
        backend = Mock()
        for old in (b'', b'corrupt', build_slot_record(
                self.plan['candidate'], self.wav, 1, 0x1FFFFFFF)):
            backend.read_custom_sound_slot.return_value = old
            with self.assertRaises(CustomSoundExecutorError):
                prepare_slot_update(backend, self.plan, self.wav)
        backend.run_custom_sound_import.assert_not_called()

    def test_default_target_is_rejected_before_camera_read(self):
        backend = Mock()
        with self.assertRaises(CustomSoundExecutorError):
            prepare_slot_update(backend, dict(target=dict(slot=0)), self.wav)
        backend.read_custom_sound_slot.assert_not_called()

    def test_missing_reader_is_rejected_before_write(self):
        class WriteOnly:
            def run_custom_sound_import(self, *args):
                self.fail('must not write')
        with self.assertRaises(CustomSoundExecutorError):
            UsbSlotRecordTransport(WriteOnly(), self.plan)

    def test_restore_preserves_later_changes_and_verifies_empty_or_old_record(self):
        old = build_slot_record(self.plan['candidate'], self.wav, 1, 0x10000006)
        after = build_slot_record(self.plan['candidate'], self.wav, 1, 0x10000007)
        for before in (None, old):
            backend = Mock()
            backend.read_custom_sound_slot.side_effect = [after, before]
            backend.remove_custom_sound_slot.return_value = dict(status=0)
            backend.run_custom_sound_import.return_value = dict(status=0)
            update = dict(slot=1, previous=before, record=after)
            result = restore_slot_update(backend, self.plan, update)
            self.assertTrue(result['independent_readback'])
            if before is None:
                backend.remove_custom_sound_slot.assert_called_once()
                backend.run_custom_sound_import.assert_not_called()
            else:
                self.assertEqual(backend.run_custom_sound_import.call_args.kwargs['expected'], after)
            backend = Mock()
            backend.read_custom_sound_slot.return_value = b'newer-user-content'
            with self.assertRaises(CustomSoundExecutorError):
                restore_slot_update(backend, self.plan, update)
            backend.run_custom_sound_import.assert_not_called()
            backend.remove_custom_sound_slot.assert_not_called()

    def test_journal_closes_only_after_independent_readback(self):
        from a6700_unlock.package_installation import pending_package_installation
        old = build_slot_record(self.plan['candidate'], self.wav, 1, 0x10000007)
        backend = Mock()
        backend.read_custom_sound_slot.return_value = old
        update = prepare_slot_update(backend, self.plan, self.wav)
        backend.read_custom_sound_slot.side_effect = [old, update['record']]
        def write(*args, **kwargs):
            self.assertEqual(kwargs['expected'], old)
            pending = pending_package_installation(directory)
            self.assertIsNotNone(pending)
            saved = json.loads((pending / 'preimages.json').read_text())
            self.assertEqual(base64.b64decode(saved['slot1']), old)
            return dict(status=0)
        backend.run_custom_sound_import.side_effect = write
        with TemporaryDirectory() as directory, patch(
                'a6700_unlock.package_installation._journal_directory', return_value=Path(directory)):
            result = execute_slot_update(backend, self.plan, update, profile_sha256='a'*64, binding=self.binding)
            self.assertEqual(result['status'], 'verified')
            self.assertTrue(result['independent_readback'])
            self.assertIsNone(pending_package_installation(directory))

    def test_changed_preimage_rejects_write_and_timeout_keeps_journal_open(self):
        from a6700_unlock.package_installation import pending_package_installation
        for timed_out in (False, True):
            backend = Mock()
            backend.read_custom_sound_slot.return_value = None
            update = prepare_slot_update(backend, self.plan, self.wav)
            backend.read_custom_sound_slot.reset_mock()
            backend.read_custom_sound_slot.return_value = None if timed_out else b'changed'
            backend.run_custom_sound_import.side_effect = DeviceError('timeout')
            with TemporaryDirectory() as directory, patch(
                    'a6700_unlock.package_installation._journal_directory', return_value=Path(directory)):
                result = execute_slot_update(backend, self.plan, update, profile_sha256='a'*64, binding=self.binding)
                self.assertEqual(result['status'], 'uncertain' if timed_out else 'not_started')
                self.assertEqual(pending_package_installation(directory) is not None, timed_out)
            self.assertEqual(backend.run_custom_sound_import.call_count, int(timed_out))
            self.assertEqual(backend.read_custom_sound_slot.call_count, 1)

    def test_recovery_material_binds_preimage_resources_and_current_revision(self):
        for before in (None, build_slot_record(self.candidate, self.wav, 1, 0x10000004)):
            backend = Mock()
            backend.read_custom_sound_slot.return_value = before
            update = prepare_slot_update(backend, self.plan, self.wav)
            backend.read_custom_sound_slot.side_effect = [before, update['record']]
            backend.run_custom_sound_import.return_value = dict(status=0)
            with TemporaryDirectory() as directory, patch(
                    'a6700_unlock.package_installation._journal_directory', return_value=Path(directory)):
                result = execute_slot_update(backend, self.plan, update, profile_sha256='a'*64, binding=self.binding)
                run = Path(result['run_directory'])
                recovery = load_slot_recovery(run, profile_sha256='a'*64, binding=self.binding)
                for changed in (replace(self.binding, device_identity_sha256='d'*64),
                                replace(self.binding, package_sha256='e'*64),
                                replace(self.binding, package_version='another-loader')):
                    with self.assertRaises(CustomSoundExecutorError):
                        load_slot_recovery(run, profile_sha256='a'*64, binding=changed)
                    with self.assertRaises(CustomSoundExecutorError):
                        execute_slot_recovery(backend, recovery, binding=changed)
                backend.read_custom_sound_slot.side_effect = None
                backend.read_custom_sound_slot.return_value = update['record']
                self.assertEqual(bind_slot_recovery(backend, recovery),
                                 dict(slot=1, previous=before, record=update['record']))
                backend.read_custom_sound_slot.return_value = before
                self.assertIsNone(bind_slot_recovery(backend, recovery))
                backend.read_custom_sound_slot.return_value = b'later content'
                with self.assertRaises(CustomSoundExecutorError):
                    bind_slot_recovery(backend, recovery)
                with self.assertRaises(CustomSoundExecutorError):
                    load_slot_recovery(run, profile_sha256='b'*64, binding=self.binding)
                with patch('a6700_unlock.custom_sound_native.load_custom_sound_payload', return_value=b'changed'):
                    with self.assertRaises(CustomSoundExecutorError):
                        load_slot_recovery(run, profile_sha256='a'*64, binding=self.binding)
                originals = {name: (run/name).read_bytes() for name in
                             ('preimages.json', 'intent.json', 'result.json')}
                for name, change in (
                    ('preimages.json', lambda data: data.update(slot1=base64.b64encode(b'altered').decode())),
                    ('intent.json', lambda data: data['plan'].update(slot=0)),
                    ('result.json', lambda data: data.update(execution_unconfirmed=True)),
                    ('result.json', lambda data: data.update(status='uncertain'))):
                    data = json.loads(originals[name]); change(data)
                    (run/name).write_text(json.dumps(data))
                    with self.assertRaises(CustomSoundExecutorError):
                        load_slot_recovery(run, profile_sha256='a'*64, binding=self.binding)
                    (run/name).write_bytes(originals[name])
                (run/'service-restore-failure.json').write_text('{}')
                with self.assertRaises(CustomSoundExecutorError):
                    load_slot_recovery(run, profile_sha256='a'*64, binding=self.binding)

    def test_recovery_journals_before_write_and_never_retries_timeout(self):
        import hashlib
        from a6700_unlock.package_installation import pending_package_installation
        after = build_slot_record(self.candidate, self.wav, 1, 0x10000001)
        recovery = dict(plan=self.plan, binding=asdict(self.binding), slot=1, previous=None, generation=0x10000001,
                        after_sha256=hashlib.sha256(after).hexdigest(), source_plan_sha256='a'*64)
        for timeout in (False, True):
            backend = Mock()
            backend.read_custom_sound_slot.side_effect = [after, after, None]
            def remove(*args):
                self.assertIsNotNone(pending_package_installation(directory))
                if timeout:
                    raise DeviceError('timeout')
                return dict(status=0)
            backend.remove_custom_sound_slot.side_effect = remove
            with TemporaryDirectory() as directory, patch(
                    'a6700_unlock.package_installation._journal_directory', return_value=Path(directory)):
                result = execute_slot_recovery(backend, recovery, binding=self.binding)
                self.assertEqual(result['status'], 'uncertain' if timeout else 'rolled_back')
                self.assertEqual(pending_package_installation(directory) is not None, timeout)
                self.assertEqual(result['execution_unconfirmed'], timeout)
            backend.remove_custom_sound_slot.assert_called_once()
            self.assertEqual(backend.read_custom_sound_slot.call_count, 2 if timeout else 3)

    def test_usb_transport_runs_payload_once_and_reads_device(self):
        class Backend:
            def __init__(self, status=0):
                self.status = status
                self.calls = []
                self.reads = []

            def read_custom_sound_slot(self, slot, nonce):
                self.reads.append((slot, nonce))
                return self.calls[-1][1]

            def run_custom_sound_import(self, plan, record, nonce):
                self.calls.append((plan["digest"], record, nonce))
                return dict(status=self.status, error=None if not self.status else "native failure")

        backend = Backend()
        transport = UsbSlotRecordTransport(backend, self.plan)
        executor = SlotRecordBackend(self.plan, transport)
        result = simulate_audio_import_transaction(
            self.plan, self.wav, executor, 0x10000007, self.authorization)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(backend.calls), 1)
        self.assertTrue(backend.calls[0][1])
        self.assertEqual(len(backend.reads), 1)
        self.assertNotEqual(backend.calls[0][2], backend.reads[0][1])

        failing = Backend(status=6)
        transport = UsbSlotRecordTransport(failing, self.plan)
        executor = SlotRecordBackend(self.plan, transport)
        result = simulate_audio_import_transaction(
            self.plan, self.wav, executor, 0x10000007, self.authorization)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(failing.calls), 1)


if __name__ == "__main__":
    unittest.main()
