"""USB adapter tests with mocked endpoints/authentication, no device access."""
import contextlib
import importlib.util
import io
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from a6700_unlock.catalog import load_catalog
from a6700_unlock.cli import main
from a6700_unlock.device_profile import load_device_profile
from a6700_unlock.device_protocol import DeviceError, RESPONSE
from a6700_unlock.usb.backend import UsbBackend


class MenuTests(unittest.TestCase):
    def test_menu_maps_backup_and_restore_to_shared_workflow(self):
        backup = dict(id='one.a6700backup', name='one.a6700backup',
                      path='/tmp/one.a6700backup', created_at='2026-10-19T00:00:00Z',
                      size=100, sector_count=4, total_bytes=2048,
                      bundle_state='disabled', features=[])
        with patch('sys.stdin.isatty',return_value=True),patch('builtins.input',side_effect=['1','4','1','0']),\
             patch('a6700_unlock.cli.list_backups',return_value=[backup]),\
             patch('a6700_unlock.cli._device',return_value=0) as device,contextlib.redirect_stdout(io.StringIO()) as text:
            self.assertEqual(main(['menu']),0)
            self.assertEqual([c.args[0].action for c in device.call_args_list],['backup','restore'])
            self.assertEqual(device.call_args_list[1].args[0].backup,Path('/tmp/one.a6700backup'))
            self.assertIn('功能状态备份',text.getvalue())
    def test_menu_reports_missing_backup_without_device_access(self):
        with patch('sys.stdin.isatty',return_value=True),patch('builtins.input',side_effect=['4','0']),\
             patch('a6700_unlock.cli.list_backups',return_value=[]),\
             patch('a6700_unlock.cli._device') as device,contextlib.redirect_stdout(io.StringIO()) as text:
            self.assertEqual(main(['menu']),0)
            device.assert_not_called()
            self.assertIn('还没有功能状态备份',text.getvalue())
    def test_cli_passes_feature_selection_and_all_missing_to_shared_execute(self):
        from argparse import Namespace
        from a6700_unlock.catalog import load_catalog
        from a6700_unlock.cli import _device
        base = dict(state_dir=None, backup_dir=None, backup=None, no_capture=True,
                    dry_run=False, json=False, confirm=[])
        for extra, expected in [({"action": "unlock", "feature": ["ftp"], "all_missing": False}, ["ftp"]),
                                ({"action": "unlock", "feature": None, "all_missing": True}, "missing")]:
            with patch('a6700_unlock.cli.preparation.guide', return_value={'ready': True}), \
                 patch('a6700_unlock.usb.backend.UsbBackend'), \
                 patch('a6700_unlock.cli.execute', return_value={'status': 'completed'}) as execute:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = _device(Namespace(**base, **extra), load_catalog())
            self.assertEqual(result, 0)
            self.assertEqual(execute.call_args.args[-1], expected)
        with self.assertRaises(DeviceError):
            _device(Namespace(**base, action='unlock', feature=['ftp'], all_missing=True), load_catalog())

    def test_menu_maps_unlock_and_close_to_shared_workflow(self):
        with patch('sys.stdin.isatty',return_value=True),patch('builtins.input',side_effect=['2','3','0']),\
             patch('a6700_unlock.cli._device',return_value=0) as device,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['menu']),0)
            self.assertEqual([c.args[0].action for c in device.call_args_list],['unlock','close'])


@unittest.skipUnless(importlib.util.find_spec('usb') is not None,'USB mock tests require optional pyusb')
class UsbTests(unittest.TestCase):
    def setUp(self):
        from a6700_unlock.usb import legacy
        self.legacy=legacy
        self.original_observe=legacy.normal_mode_observed
        observe_patch=patch.object(legacy,'normal_mode_observed',return_value=False)
        self.observe=observe_patch.start()
        self.addCleanup(observe_patch.stop)
        self.profile=load_device_profile(load_catalog())

    def test_sequence_exhaustion_fails_before_any_usb_transfer(self):
        device = Mock()
        target = SimpleNamespace(device=device, endpoint_out=2, endpoint_in=0x81, product_id=0x0336)
        channel = self.legacy.SenserChannel(target)
        channel.sequence = 0x10000
        with self.assertRaisesRegex(self.legacy.ProbeError, 'sequence exhausted'):
            channel.send(0x40, b'probe')
        device.write.assert_not_called()

    def test_abandon_unconfirmed_execution_releases_host_without_stop(self):
        backend = UsbBackend(self.profile, capture=False)
        backend.target = SimpleNamespace(device=Mock())
        target = backend.target
        backend.legacy = Mock()
        backend.abandon_transport()
        backend.legacy.close_target.assert_called_once_with(target)
        backend.legacy.vendor_request_out.assert_not_called()
        self.assertIsNone(backend.target)
        self.assertEqual(backend.cleanup_status, 'unconfirmed')

    def test_msc_discovery_does_not_open_endpoints_or_require_mounted_storage(self):
        device = SimpleNamespace(idVendor=0x054c, idProduct=0x0e76)
        with patch.object(self.legacy, 'candidates', return_value=[device]) as scan, \
             patch.object(self.legacy, 'find_bulk_endpoints', side_effect=AssertionError('early endpoint access')):
            self.assertIs(self.legacy.choose_initial_device(None), device)
        scan.assert_called_once_with(require_bulk=False)

    def test_msc_class_is_accepted_without_pin_to_one_product_id(self):
        class Device(SimpleNamespace):
            def __getitem__(self, index):
                return [SimpleNamespace(bInterfaceClass=self.interface_class)]
        msc = Device(idVendor=0x054c, idProduct=0x1234, interface_class=8)
        ptp = Device(idVendor=0x054c, idProduct=0x0e78, interface_class=6)
        power = Device(idVendor=0x054c, idProduct=0x0d68, interface_class=0xff)
        disk = Device(idVendor=0x9999, idProduct=0x1234, interface_class=8)
        with patch.object(self.legacy, 'iter_usb_devices', return_value=[ptp, power, disk, msc]):
            self.assertIs(self.legacy.choose_initial_device(None), msc)
        with patch.object(self.legacy, 'iter_usb_devices', return_value=[ptp, power, disk]):
            with self.assertRaisesRegex(self.legacy.ProbeError, 'no Sony MSC'):
                self.legacy.choose_initial_device(None)

    def test_multiple_msc_cameras_are_not_silently_preferred_by_product_id(self):
        devices = [SimpleNamespace(idVendor=0x054c, idProduct=pid, bus=1, address=i)
                   for i, pid in enumerate((0x0e76, 0x047d))]
        with patch.object(self.legacy, 'candidates', return_value=devices):
            with self.assertRaisesRegex(self.legacy.ProbeError, 'multiple Sony MSC'):
                self.legacy.choose_initial_device(None)

    def test_descriptor_fallback_never_changes_usb_configuration(self):
        class Device:
            def get_active_configuration(self):
                raise self_error('interface busy')
            def __getitem__(self, index):
                return [interface]
            def set_configuration(self):
                raise AssertionError('discovery must not configure USB')
        self_error = self.legacy.USBError
        interface = [SimpleNamespace(bEndpointAddress=a, bmAttributes=2) for a in (0x81, 2)]
        self.assertEqual(self.legacy.find_bulk_endpoints(Device())[1:], (0x81, 2))

    def test_service_discovery_defers_endpoint_access_until_open(self):
        service = SimpleNamespace(idProduct=0x0336)
        with patch.object(self.legacy, 'candidates', return_value=[service]) as scan:
            self.assertIs(self.legacy.wait_for_service_device(0x0e76, 0x0336, 1), service)
        scan.assert_called_once_with(require_bulk=False)

    def test_mac_auth_refreshes_context_after_capture_and_identity_transition(self):
        initial = SimpleNamespace(idProduct=0x0e76)
        service = SimpleNamespace(idProduct=0x0336)
        target = SimpleNamespace(interface=SimpleNamespace(bInterfaceClass=0xff))
        with patch.object(self.legacy.sys, 'platform', 'darwin'), \
             patch.object(self.legacy, 'get_libusb_backend') as contexts, \
             patch.object(self.legacy, 'choose_initial_device', return_value=initial), \
             patch.object(self.legacy, 'wait_for_service_device', return_value=service), \
             patch.object(self.legacy, 'open_target', return_value=target), \
             patch.object(self.legacy, 'authenticate_target'), \
             patch.object(self.legacy, 'close_target'), contextlib.redirect_stdout(io.StringIO()):
            self.legacy.run_authentication(None, 0x0336, 1, False)
        self.assertEqual(len(contexts.call_args_list), 2)
        self.assertTrue(all(c.kwargs == {'fresh': True} for c in contexts.call_args_list))

    def test_mac_auth_retries_initial_discovery_with_fresh_context(self):
        initial = SimpleNamespace(idProduct=0x0e76)
        service = SimpleNamespace(idProduct=0x0336)
        target = SimpleNamespace(interface=SimpleNamespace(bInterfaceClass=0xff))
        with patch.object(self.legacy.sys, 'platform', 'darwin'), \
             patch.object(self.legacy, 'get_libusb_backend') as contexts, \
             patch.object(self.legacy, 'choose_initial_device',
                          side_effect=[self.legacy.ProbeError('no Sony MSC device found'), initial]), \
             patch.object(self.legacy, 'wait_for_service_device', return_value=service), \
             patch.object(self.legacy, 'open_target', return_value=target), \
             patch.object(self.legacy, 'authenticate_target'), \
             patch.object(self.legacy, 'close_target'), \
             patch.object(self.legacy.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.legacy.run_authentication(None, 0x0336, 1, False)
        self.assertEqual(len(contexts.call_args_list), 3)
        self.assertTrue(all(c.kwargs == {'fresh': True} for c in contexts.call_args_list))

    def test_fresh_backend_replaces_old_context_used_by_subsequent_scans(self):
        first, second = object(), object()
        with patch.object(self.legacy, '_session_backend', None), \
             patch.object(self.legacy, '_session_library', None), \
             patch.object(self.legacy, 'libusb_library_path', return_value='synthetic-library'), \
             patch.object(self.legacy.libusb1, 'get_backend', return_value=SimpleNamespace(lib=object())), \
             patch.object(self.legacy, '_OwnedLibUSB', side_effect=[first, second]), \
             patch.object(self.legacy.usb.core, 'find', return_value=[]) as scan:
            self.assertIs(self.legacy.get_libusb_backend(fresh=True), first)
            self.assertIs(self.legacy.get_libusb_backend(fresh=True), second)
            self.assertEqual(list(self.legacy.iter_usb_devices()), [])
            self.assertIs(scan.call_args.kwargs['backend'], second)

    def test_context_rotation_keeps_device_references_alive_and_finalizes_once(self):
        # Real pinned PyUSB object/finalizer, fake C functions: no USB init,
        # discovery or transfers. A device resource manager retains backend.
        import gc
        lib = Mock()
        pointer = iter((101, 102))
        def initialize(out):
            out._obj.value = next(pointer)
            return 0
        lib.libusb_init.side_effect = initialize
        with patch.object(self.legacy, '_session_backend', None), \
             patch.object(self.legacy, '_session_library', 'synthetic-library'), \
             patch.object(self.legacy, 'libusb_library_path', return_value='synthetic-library'), \
             patch.object(self.legacy.libusb1, 'get_backend', return_value=SimpleNamespace(lib=lib)):
            old = self.legacy.libusb1._LibUSB(lib)
            device_resources = SimpleNamespace(backend=old)
            self.legacy._session_backend = old
            del old
            fresh = self.legacy.get_libusb_backend(fresh=True)
            lib.libusb_exit.assert_not_called()
            self.legacy.reset_libusb_backend()
            lib.libusb_exit.assert_not_called()
            del device_resources
            gc.collect()
            self.assertEqual(lib.libusb_exit.call_count, 1)
            self.assertEqual(lib.libusb_exit.call_args.args[0].value, 101)
            del fresh
            gc.collect()
            self.assertEqual(lib.libusb_exit.call_count, 2)
            self.assertEqual(lib.libusb_exit.call_args.args[0].value, 102)

    def test_reset_never_calls_exit_directly_on_referenced_backend(self):
        backend = Mock(ctx=object())
        with patch.object(self.legacy, '_session_backend', backend), \
             patch.object(self.legacy, '_session_library', 'synthetic-library'):
            self.legacy.reset_libusb_backend()
            self.assertIsNone(self.legacy._session_backend)
            self.assertIsNone(self.legacy._session_library)
            backend.lib.libusb_exit.assert_not_called()
            backend.finalize.assert_not_called()

    def test_native_device_is_unrefed_before_its_context_exits(self):
        # PyUSB's native _Device does not retain its backend. Exercise that
        # actual wrapper/finalizer with synthetic C functions, no real USB.
        import gc
        events = []
        lib = Mock()
        def initialize(out):
            out._obj.value = 101
            return 0
        lib.libusb_init.side_effect = initialize
        lib.libusb_ref_device.side_effect = lambda device: device
        lib.libusb_unref_device.side_effect = lambda device: events.append('unref')
        lib.libusb_exit.side_effect = lambda ctx: events.append('exit')
        with patch.object(self.legacy, '_session_backend', None), \
             patch.object(self.legacy, '_session_library', None), \
             patch.object(self.legacy, 'libusb_library_path', return_value='synthetic-library'), \
             patch.object(self.legacy.libusb1, '_lib', lib), \
             patch.object(self.legacy.libusb1, 'get_backend', return_value=SimpleNamespace(lib=lib)):
            backend = self.legacy.get_libusb_backend(fresh=True)
            native = self.legacy.libusb1._Device(17)
            class NativeIterator:
                def __iter__(self):
                    yield native
            with patch.object(self.legacy.libusb1._LibUSB, 'enumerate_devices', new=lambda _backend: NativeIterator()):
                iterator = backend.enumerate_devices()
                found = next(iter(iterator))
                list(iterator)
                self.assertIs(found, native)
                self.legacy.reset_libusb_backend()
                del backend
                gc.collect()
                self.assertEqual(events, [], 'live native device must retain its context')
                native.finalize()
                self.assertEqual(events, ['unref'])
                del native, found, iterator
                gc.collect()
                self.assertEqual(events, ['unref', 'exit'])

    def test_parent_native_device_retains_context_until_unref(self):
        import gc
        lib = Mock()
        def initialize(out):
            out._obj.value = 102
            return 0
        lib.libusb_init.side_effect = initialize
        lib.libusb_ref_device.side_effect = lambda device: device
        with patch.object(self.legacy, '_session_backend', None), \
             patch.object(self.legacy, '_session_library', None), \
             patch.object(self.legacy, 'libusb_library_path', return_value='synthetic-library'), \
             patch.object(self.legacy.libusb1, '_lib', lib), \
             patch.object(self.legacy.libusb1, 'get_backend', return_value=SimpleNamespace(lib=lib)):
            backend = self.legacy.get_libusb_backend(fresh=True)
            native = self.legacy.libusb1._Device(18)
            with patch.object(self.legacy.libusb1._LibUSB, 'get_parent', new=lambda _backend, _device: native):
                parent = backend.get_parent(object())
                self.legacy.reset_libusb_backend()
                del backend
                gc.collect()
                lib.libusb_exit.assert_not_called()
                parent.finalize()
                lib.libusb_unref_device.assert_called_once_with(18)
            del parent, native
            gc.collect()
            lib.libusb_exit.assert_called_once()

    def test_mac_capture_is_one_step_without_reset_preparation(self):
        with patch.object(self.legacy.sys, 'platform', 'darwin'), \
             patch('a6700_unlock.usb.backend.capture_macos') as capture, \
             patch.object(self.legacy, 'get_libusb_backend'), \
             patch.object(self.legacy, 'run_authentication', return_value=(object(), object())) as auth:
            UsbBackend(self.profile).__enter__()
        capture.assert_called_once()
        self.assertIsNone(auth.call_args.args[0])
        self.assertEqual(auth.call_args.args[2], 45.0)

    def test_audio_identity_requires_fresh_unique_msc_before_authentication(self):
        import hashlib
        backend = UsbBackend(self.profile)
        backend.legacy = self.legacy
        backend.access_prepared = True
        camera = SimpleNamespace(idVendor=0x054c, idProduct=0x0e76)
        identity = dict(product='ILCE-6700', serial_number='synthetic-body')
        with patch.object(self.legacy, 'iter_usb_devices', return_value=[camera]) as scan, \
             patch.object(self.legacy, 'device_identity', return_value=identity):
            self.assertEqual(backend.read_custom_sound_identity(),
                             hashlib.sha256(b'synthetic-body').hexdigest())
            scan.assert_called_once_with(fresh=True)
            for bad in ([], [camera, camera], [SimpleNamespace(idVendor=0x054c,idProduct=0x0336)]):
                scan.return_value = bad
                with self.assertRaises(DeviceError): backend.read_custom_sound_identity()
            scan.return_value = [camera]
            identity['serial_number'] = ''
            with self.assertRaises(DeviceError): backend.read_custom_sound_identity()
            backend.target = object()
            scan.reset_mock()
            with self.assertRaises(DeviceError): backend.read_custom_sound_identity()
            scan.assert_not_called()

    def test_normal_mode_requires_unique_msc_and_waits_past_stale_service(self):
        with patch.object(self.legacy,'observed_sony_product_ids',side_effect=[[0x0336],[0x0e76]]), \
             patch.object(self.legacy.time,'sleep'):
            self.assertTrue(self.original_observe())
        with patch.object(self.legacy,'observed_sony_product_ids',return_value=[0x0e76,0x0336]):
            self.assertFalse(self.original_observe())

    def test_mac_exit_observer_reads_independent_iokit_descriptors(self):
        import plistlib
        tree=[dict(IOObjectClass='IOUSBHostDevice',idVendor=0x054c,idProduct=0x0e76,
                   children=[dict(IOObjectClass='IOUSBHostInterface',idVendor=0x054c,idProduct=0x0e76)]),
              dict(IOObjectClass='IOUSBHostDevice',idVendor=0x9999,idProduct=0x0336)]
        with patch.object(self.legacy.sys,'platform','darwin'), \
             patch.object(self.legacy.subprocess,'check_output',return_value=plistlib.dumps(tree)):
            self.assertEqual(self.legacy.observed_sony_product_ids(),[0x0e76])

    def test_service_auth_uses_first_bulk_pair_instead_of_overwriting_with_second(self):
        class Interface(list):
            bInterfaceNumber=0
        interface=Interface([SimpleNamespace(bEndpointAddress=address,bmAttributes=2)
                             for address in (0x81,0x02,0x83,0x04)])
        device=Mock();device.get_active_configuration.return_value=[interface]
        selected,ep_in,ep_out=self.legacy.find_bulk_endpoints(device)
        self.assertIs(selected,interface)
        self.assertEqual((ep_in,ep_out),(0x81,0x02))

    def test_fragmented_response_is_reassembled(self):
        device=Mock()
        frame=struct.pack('<IHHBBBB',5,0x40,1,0,0,0,0)
        device.read.side_effect=[frame,b'abc',b'de']
        device.write.side_effect=lambda ep,data,**kwargs:len(data)
        target=SimpleNamespace(device=device,endpoint_in=0x81,endpoint_out=2,product_id=0x0336)
        channel=self.legacy.SenserChannel(target)
        self.assertEqual(channel.send(0x40,b'x'),(0,b'abcde'))
        self.assertEqual(channel.sequence,2)
    def test_short_write_does_not_read_or_retry(self):
        device=Mock();device.write.return_value=1
        target=SimpleNamespace(device=device,endpoint_in=0x81,endpoint_out=2,product_id=0x0336)
        with self.assertRaises(self.legacy.ProbeError):self.legacy.SenserChannel(target).send(0x40,b'x')
        device.read.assert_not_called();self.assertEqual(device.write.call_count,1)
    def test_native_capture_routes_three_commands_and_stops_on_exit(self):
        nonce=bytes(range(16));count=len(self.profile.entries)
        raw=RESPONSE.pack(b'A67R',1,nonce,0,count,0xffffffff,0,0)+bytes(count*512)
        channel=Mock();channel.send.side_effect=[
            (0,bytes(4)),
            (0,bytes(4)),(0,bytes(4)),(0,bytes(4)+raw),
            (0,bytes(4)),(0,bytes(4)),(0,bytes(4)+raw),
        ]
        target=SimpleNamespace(device=object())
        with patch.object(self.legacy,'get_libusb_backend',return_value=object()),\
             patch.object(self.legacy,'run_authentication',return_value=(target,channel)),\
             patch.object(self.legacy,'vendor_request_out') as stop,patch.object(self.legacy,'close_target') as close:
            with UsbBackend(self.profile,capture=False) as backend:
                self.assertEqual(backend.read(nonce),[bytes(512)]*count)
                self.assertEqual(backend.read(nonce),[bytes(512)]*count)
            stop.assert_called_once_with(target.device,self.legacy.STOP_SENSER,strict=True)
            close.assert_called_once_with(target)
        calls=channel.send.call_args_list
        self.assertEqual(calls[0].args,(0x40,struct.pack('<HH',0x1001,0x4002)+bytes(3)))
        self.assertEqual([struct.unpack_from('<HH',c.args[1])[1] for c in calls],
                         [0x4002,0x4010,0x4007,0x4008,0x4010,0x4007,0x4008])

    def test_mount_failure_stops_before_file_upload_and_releases_service(self):
        channel=Mock();channel.send.return_value=(0,bytes.fromhex('04000010'))
        target=SimpleNamespace(device=object())
        with patch.object(self.legacy,'get_libusb_backend',return_value=object()),\
             patch.object(self.legacy,'run_authentication',return_value=(target,channel)),\
             patch.object(self.legacy,'vendor_request_out') as stop,patch.object(self.legacy,'close_target') as close:
            with self.assertRaisesRegex(DeviceError,'04000010'):
                with UsbBackend(self.profile,capture=False) as backend:
                    backend.read(bytes(16))
        self.assertEqual(channel.send.call_count,1)
        self.assertEqual(struct.unpack_from('<HH',channel.send.call_args.args[1])[1],0x4002)
        self.assertFalse(backend._jiritsu_mounted)
        stop.assert_called_once_with(target.device,self.legacy.STOP_SENSER,strict=True)
        close.assert_called_once_with(target)
    def test_stop_failure_is_reported_and_interface_is_still_released(self):
        from usb.core import USBError
        device=Mock();device.ctrl_transfer.side_effect=USBError('synthetic stop stall')
        target=SimpleNamespace(device=device)
        backend=UsbBackend(self.profile,capture=False)
        backend.legacy=self.legacy;backend.target=target
        with patch.object(self.legacy,'close_target') as close:
            backend.__exit__(None,None,None)
        close.assert_called_once_with(target)
        self.assertIn('无法确认退出',backend.cleanup_warning)
        self.assertIsNone(backend.target)

    def test_stop_status_lost_during_msc_transition_is_independently_observed(self):
        from usb.core import USBError
        self.observe.return_value=True
        target=SimpleNamespace(device=object())
        backend=UsbBackend(self.profile,capture=False)
        backend.legacy=self.legacy;backend.target=target
        with patch.object(self.legacy,'vendor_request_out',side_effect=USBError('disconnected')), \
             patch.object(self.legacy,'close_target'):
            backend.__exit__(None,None,None)
        self.assertEqual(backend.cleanup_status,'msc-observed')
        self.assertIsNone(backend.cleanup_warning)

    def test_release_failure_does_not_replace_primary_operation_error(self):
        target=SimpleNamespace(device=object())
        backend=UsbBackend(self.profile,capture=False)
        backend.legacy=self.legacy;backend.target=target
        with patch.object(self.legacy,'vendor_request_out'),patch.object(self.legacy,'close_target',side_effect=RuntimeError('release')):
            backend.__exit__(RuntimeError,RuntimeError('primary'),None)
        self.assertIn('释放 USB 接口失败',backend.cleanup_warning)
        self.assertIsNone(backend.target)

    def test_authentication_failure_reports_stage_without_raw_diagnostics(self):
        def fail(*args, **kwargs):
            kwargs['progress']('正在进行第二次 USB 认证…')
            print('PRIVATE_PRODUCT_INFO')
            error=RuntimeError('synthetic timeout')
            error.cleanup_warning='退出服务模式未获确认，请拔插 USB'
            raise error
        messages=[]
        backend=UsbBackend(self.profile,progress=messages.append,capture=False)
        with patch.object(self.legacy,'get_libusb_backend',return_value=object()), \
             patch.object(self.legacy,'run_authentication',side_effect=fail),contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaisesRegex(DeviceError,'第二次 USB 认证'):
                backend.__enter__()
        self.assertNotIn('PRIVATE_PRODUCT_INFO',out.getvalue())
        self.assertIn('第二次 USB 认证',messages[-1])
        self.assertIn('退出服务模式',backend.cleanup_warning)

    def test_duplicate_service_devices_are_refused(self):
        cameras=[SimpleNamespace(idProduct=0x0336),SimpleNamespace(idProduct=0x0336)]
        with patch.object(self.legacy,'candidates',return_value=cameras):
            with self.assertRaises(self.legacy.ProbeError):
                self.legacy.wait_for_service_device(0x0e76,0x0336,1)

    def test_backend_error_still_releases_service(self):
        channel=Mock();channel.send.side_effect=RuntimeError('synthetic transfer failure')
        target=SimpleNamespace(device=object())
        with patch.object(self.legacy,'get_libusb_backend',return_value=object()),\
             patch.object(self.legacy,'run_authentication',return_value=(target,channel)),\
             patch.object(self.legacy,'vendor_request_out') as stop,patch.object(self.legacy,'close_target') as close:
            with self.assertRaises(DeviceError):
                with UsbBackend(self.profile,capture=False) as backend:backend.read(bytes(16))
            self.assertEqual(channel.send.call_count,1)
            self.assertTrue(stop.called and close.called)


if __name__=='__main__':unittest.main()
