"""Fixed-path native reads and independent USB slot observations, no device IO."""
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import nullcontext, contextmanager
from unittest.mock import Mock, patch, MagicMock

from a6700_unlock.custom_sound_native import (
    custom_sound_read_request, custom_sound_read_response, load_custom_sound_reader)
from a6700_unlock.custom_sound_executor import read_slot_records, UsbSlotRecordTransport
from a6700_unlock.custom_sound_slots import slot_inventory
from a6700_unlock.custom_sound_records import MAX_RECORD_BYTES
from a6700_unlock.device_protocol import DeviceError
from test_custom_sound_native import AVAILABLE, Machine
from test_custom_sound_executor import pcm_wav
from a6700_unlock.package_installation import PackageBinding


class InventoryProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for target, value in (
                ('a6700_unlock.operations.default_state_dir', lambda: root),
                ('a6700_unlock.custom_sound_executor.preserved_slot_backend',
                 lambda backend, directory: nullcontext(backend))):
            replaced = patch(target, value)
            replaced.start()
            self.addCleanup(replaced.stop)

    def test_explicit_refresh_reads_ten_slots_and_releases_session(self):
        from a6700_unlock.desktop.physical import PhysicalService
        backend = Mock(cleanup_status='restored', cleanup_warning=None)
        backend.read_custom_sound_slot.return_value = None
        service = PhysicalService(backend_factory=lambda: backend)
        backend.read_custom_sound_binding.return_value = PackageBinding(
            "custom_shutter_sound", "ten-slot", "b"*64, service.profile.digest, "c"*64, "ILCE-6700", "2.00")
        lock = MagicMock()
        with patch('a6700_unlock.operations.device_lock', return_value=lock), \
             patch('a6700_unlock.device_access.UsbDeviceAccess') as access, \
             patch('a6700_unlock.close_setting.verify_pending_settings'):
            access.return_value.backend.read_custom_sound_identity.return_value = "c"*64
            access.return_value.open_transport.return_value = backend
            result = service.read_audio_slots()
        self.assertEqual(result['status'], 'observed')
        self.assertEqual([s['slot'] for s in result['slots']], list(range(1,11)))
        self.assertTrue(all(s['status'] == 'empty' for s in result['slots']))
        self.assertEqual(backend.read_custom_sound_slot.call_count, 10)
        backend.run_custom_sound_import.assert_not_called()
        access.return_value.release_access.assert_called_once()
        lock.__exit__.assert_called_once()
        self.assertFalse(service.connected)

    def test_refresh_failure_releases_session_without_returning_partial_slots(self):
        from a6700_unlock.desktop.physical import PhysicalService
        backend = Mock(cleanup_status='restored', cleanup_warning=None)
        backend.read_custom_sound_slot.side_effect = [None, DeviceError('interrupted')]
        service = PhysicalService(backend_factory=lambda: backend)
        backend.read_custom_sound_binding.return_value = PackageBinding(
            "custom_shutter_sound", "ten-slot", "b"*64, service.profile.digest, "c"*64, "ILCE-6700", "2.00")
        lock = MagicMock()
        with patch('a6700_unlock.operations.device_lock', return_value=lock), \
             patch('a6700_unlock.device_access.UsbDeviceAccess') as access, \
             patch('a6700_unlock.close_setting.verify_pending_settings'):
            access.return_value.backend.read_custom_sound_identity.return_value = "c"*64
            access.return_value.open_transport.return_value = backend
            with self.assertRaises(DeviceError): service.read_audio_slots()
        access.return_value.release_access.assert_called_once()
        lock.__exit__.assert_called_once()
        self.assertFalse(service.connected)

    def test_missing_reader_asset_fails_before_lock_or_usb(self):
        from a6700_unlock.desktop.physical import PhysicalService
        service = PhysicalService()
        with patch('a6700_unlock.custom_sound_native.load_custom_sound_reader',
                   side_effect=DeviceError('missing')), \
             patch('a6700_unlock.operations.device_lock') as lock, \
             patch('a6700_unlock.device_access.UsbDeviceAccess') as access:
            with self.assertRaises(DeviceError): service.read_audio_slots()
        lock.assert_not_called()
        access.assert_not_called()

    def test_default_bool_and_out_of_range_are_rejected(self):
        for slot in (0, 11, True, '1', -1):
            with self.subTest(slot=slot), self.assertRaises(DeviceError):
                custom_sound_read_request(slot, bytes(16))

    def test_reply_must_match_nonce_slot_and_extent(self):
        reply = struct.pack('<4sI16sIIIII', b'CSNR', 1, bytes(16), 0, 10, 0, 0, 0)
        self.assertIsNone(custom_sound_read_response(reply, bytes(16), 10))
        for data, nonce, slot in ((reply, bytes([1])*16, 10), (reply, bytes(16), 1),
                                  (reply+b'x', bytes(16), 10)):
            with self.assertRaises(DeviceError): custom_sound_read_response(data, nonce, slot)

    def test_readback_uses_camera_not_last_submitted_bytes(self):
        backend = Mock(cleanup_status='restored', cleanup_warning=None)
        backend.read_custom_sound_slot.return_value = b'changed-on-camera'
        transport = UsbSlotRecordTransport(backend, {'target': {'slot': 3}})
        transport.saved[3] = b'last-submitted'
        self.assertEqual(transport.read_slot(3), b'changed-on-camera')
        self.assertEqual(backend.read_custom_sound_slot.call_args.args[0], 3)

    def test_partial_inventory_is_not_published_as_empty(self):
        backend = Mock(cleanup_status='restored', cleanup_warning=None)
        backend.read_custom_sound_slot.side_effect = [None, DeviceError('read failed')]
        with self.assertRaises(DeviceError): read_slot_records(backend)
        self.assertEqual(backend.read_custom_sound_slot.call_count, 2)
        self.assertTrue(all(s['status']=='unknown' for s in slot_inventory()))

    def test_physical_import_does_not_report_success_after_wrong_readback(self):
        from a6700_unlock.desktop.physical import PhysicalService
        from a6700_unlock.audio_import import build_audio_import_plan
        from a6700_unlock.audio_packages import inspect_pcm_wav
        wav = pcm_wav()
        plan = build_audio_import_plan(inspect_pcm_wav(wav, 'test.wav'),
            dict(type='local'), target_slot=10, capability_available=True,
            implementation_ready=True)
        for observed in (b'wrong', None, DeviceError('read interrupted')):
            with self.subTest(observed=observed):
                backend = Mock(cleanup_status='restored', cleanup_warning=None)
                backend.read_custom_sound_slot.side_effect = [None, None, observed]
                backend.run_custom_sound_import.return_value = dict(status=0)
                service = PhysicalService(backend_factory=lambda: backend)
                backend.read_custom_sound_binding.return_value = PackageBinding(
                    "custom_shutter_sound", "ten-slot", "b"*64, service.profile.digest, "c"*64, "ILCE-6700", "2.00")
                service.close_session = Mock()
                with TemporaryDirectory() as journal, \
                     patch('a6700_unlock.package_installation._journal_directory', return_value=Path(journal)), \
                     patch('a6700_unlock.operations.device_lock', return_value=MagicMock()), \
                     patch('a6700_unlock.device_access.UsbDeviceAccess') as access, \
                     patch('a6700_unlock.close_setting.verify_pending_settings'):
                    access.return_value.backend.read_custom_sound_identity.return_value = "c"*64
                    access.return_value.open_transport.return_value = backend
                    result = service.execute_audio_import(plan, wav)
                self.assertEqual(result['status'], 'uncertain')
                self.assertFalse(result['independent_readback'])
                self.assertEqual(result['generation'], 0xA0000001)
                backend.run_custom_sound_import.assert_called_once()
                service.close_session.assert_called_once()

    def test_physical_import_requires_stable_typed_device_binding(self):
        from dataclasses import replace
        from a6700_unlock.desktop.physical import PhysicalService
        from a6700_unlock.custom_sound_executor import CustomSoundExecutorError
        from a6700_unlock.audio_import import build_audio_import_plan
        from a6700_unlock.audio_packages import inspect_pcm_wav
        wav = pcm_wav()
        plan = build_audio_import_plan(inspect_pcm_wav(wav, 'test.wav'),
            dict(type='local'), target_slot=2, capability_available=True, implementation_ready=True)
        for mode in ('missing', 'untyped', 'changed'):
            backend = Mock(cleanup_status='restored', cleanup_warning=None)
            service = PhysicalService(backend_factory=lambda: backend)
            binding = PackageBinding('custom_shutter_sound', 'ten-slot', 'b'*64,
                                     service.profile.digest, 'c'*64, 'ILCE-6700', '2.00')
            if mode == 'missing':
                backend.read_custom_sound_binding = None
            elif mode == 'untyped':
                backend.read_custom_sound_binding.return_value = {'compatible': True}
            else:
                backend.read_custom_sound_binding.side_effect = [binding,
                    replace(binding, device_identity_sha256='d'*64)]
                backend.read_custom_sound_slot.return_value = None
            with patch('a6700_unlock.operations.device_lock', return_value=MagicMock()), \
                 patch('a6700_unlock.device_access.UsbDeviceAccess') as access, \
                 patch('a6700_unlock.close_setting.verify_pending_settings'):
                access.return_value.backend.read_custom_sound_identity.return_value = "c"*64
                access.return_value.open_transport.return_value = backend
                with self.assertRaises((ValueError, CustomSoundExecutorError)):
                    service.execute_audio_import(plan, wav)
            backend.run_custom_sound_import.assert_not_called()
            access.return_value.release_access.assert_called_once()

    def test_service_restore_failure_reopens_verified_import_journal(self):
        from a6700_unlock.desktop.physical import PhysicalService
        from a6700_unlock.package_installation import pending_package_installation
        from a6700_unlock.custom_sound_records import build_slot_record
        from a6700_unlock.audio_import import build_audio_import_plan
        from a6700_unlock.audio_packages import inspect_pcm_wav
        wav = pcm_wav()
        plan = build_audio_import_plan(inspect_pcm_wav(wav, 'test.wav'),
            dict(type='local'), target_slot=1, capability_available=True, implementation_ready=True)
        record = build_slot_record(plan['candidate'], wav, 1, 0x10000001)
        backend = Mock(cleanup_status='restored', cleanup_warning=None)
        backend.read_custom_sound_slot.side_effect = [None, None, record]
        backend.run_custom_sound_import.return_value = dict(status=0)
        service = PhysicalService(backend_factory=lambda: backend)
        backend.read_custom_sound_binding.return_value = PackageBinding(
            "custom_shutter_sound", "ten-slot", "b"*64, service.profile.digest, "c"*64, "ILCE-6700", "2.00")
        @contextmanager
        def failed_restore(*args):
            yield backend
            raise DeviceError('service restore failed')
        with TemporaryDirectory() as journal, \
             patch('a6700_unlock.package_installation._journal_directory', return_value=Path(journal)), \
             patch('a6700_unlock.operations.device_lock', return_value=MagicMock()), \
             patch('a6700_unlock.device_access.UsbDeviceAccess') as access, \
             patch('a6700_unlock.custom_sound_executor.preserved_slot_backend', failed_restore), \
             patch('a6700_unlock.close_setting.verify_pending_settings'):
            access.return_value.backend.read_custom_sound_identity.return_value = "c"*64
            access.return_value.open_transport.return_value = backend
            with self.assertRaises(DeviceError): service.execute_audio_import(plan, wav)
            self.assertIsNotNone(pending_package_installation(journal))
        access.return_value.release_access.assert_called_once()

    def test_audio_slots_and_import_reuse_existing_authenticated_session(self):
        from a6700_unlock.desktop.physical import PhysicalService
        from a6700_unlock.custom_sound_records import build_slot_record
        from a6700_unlock.audio_import import build_audio_import_plan
        from a6700_unlock.audio_packages import inspect_pcm_wav

        wav = pcm_wav()
        plan = build_audio_import_plan(inspect_pcm_wav(wav, 'reuse.wav'),
            dict(type='local'), target_slot=4, capability_available=True,
            implementation_ready=True)
        record = build_slot_record(plan['candidate'], wav, 4, 0x40000001)
        backend = Mock(cleanup_status='restored', cleanup_warning=None)
        backend._custom_sound_identity = None
        def capture_identity():
            backend._custom_sound_identity = 'c' * 64
            return 'c' * 64
        backend.read_custom_sound_identity.side_effect = capture_identity
        backend.read_custom_sound_slot.side_effect = [None] * 10 + [None, None, record]
        backend.run_custom_sound_import.return_value = dict(status=0)
        service = PhysicalService(backend_factory=lambda _p=None: backend)
        backend.read_custom_sound_binding.return_value = PackageBinding(
            'custom_shutter_sound', 'ten-slot', 'b' * 64, service.profile.digest,
            'c' * 64, 'ILCE-6700', '2.00')
        lock = MagicMock()
        with TemporaryDirectory() as journal, \
             patch('a6700_unlock.package_installation._journal_directory', return_value=Path(journal)), \
             patch('a6700_unlock.operations.device_lock', return_value=lock) as mock_lock, \
             patch('a6700_unlock.device_access.UsbDeviceAccess') as access, \
             patch('a6700_unlock.close_setting.verify_pending_settings'), \
             patch('a6700_unlock.operations._execute_session', return_value=dict(
                 action='inspect', status='inspected', device_access=True,
                 write_attempted=False, run_directory=str(Path(journal) / 'inspect'),
                 plan=dict(compatible=True, features=[]))):
            access.return_value.backend = backend
            access.return_value.open_transport.return_value = backend
            inspect_result = service.perform('inspect')
            self.assertEqual(inspect_result['status'], 'inspected')
            self.assertTrue(service.connected)

            slots_result = service.read_audio_slots()
            self.assertEqual(slots_result['status'], 'observed')
            self.assertTrue(service.connected)

            import_result = service.execute_audio_import(plan, wav)
            self.assertEqual(import_result['status'], 'completed')
            self.assertTrue(import_result['independent_readback'])
            self.assertTrue(service.connected)
            self.assertEqual(mock_lock.call_count, 1)
            self.assertEqual(access.return_value.open_transport.call_count, 1)
            access.return_value.release_access.assert_not_called()
            service.close_session()
            access.return_value.release_access.assert_called_once()
            lock.__exit__.assert_called_once()


@unittest.skipUnless(AVAILABLE, 'requires unicorn and pyelftools')
class NativeInventoryTests(unittest.TestCase):
    def run_slot(self, slot, files=None, request=None):
        nonce = bytes(range(16))
        vm = Machine(load_custom_sound_reader(), request or custom_sound_read_request(slot, nonce), files)
        raw = vm.run()
        self.assertFalse(vm.fds)
        self.assertEqual(vm.write_count, 0)
        self.assertFalse(vm.renames)
        self.assertFalse({34,35,38} & set(vm.calls))
        return custom_sound_read_response(raw, nonce, slot)

    def test_every_slot_reads_exact_file_without_touching_neighbors(self):
        files = {f'/setting/csnd/slot{i}.bin': bytes([i])*127 for i in range(1,11)}
        for slot in range(1,11):
            with self.subTest(slot=slot):
                self.assertEqual(self.run_slot(slot,files),files[f'/setting/csnd/slot{slot}.bin'])

    def test_missing_and_corrupt_empty_file_remain_distinct(self):
        self.assertIsNone(self.run_slot(10))
        observed = self.run_slot(10, {'/setting/csnd/slot10.bin': b''})
        self.assertEqual(observed, b'')
        self.assertEqual(slot_inventory({10: observed})[-1]['status'], 'invalid')

    def test_oversized_file_is_error_not_empty(self):
        with self.assertRaises(DeviceError):
            self.run_slot(1, {'/setting/csnd/slot1.bin': bytes(MAX_RECORD_BYTES+1)})

    def test_extra_request_bytes_are_rejected(self):
        with self.assertRaises(DeviceError):
            self.run_slot(1, request=custom_sound_read_request(1,bytes(range(16)))+b'x')
