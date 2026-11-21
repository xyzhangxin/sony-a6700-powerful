"""Built-in FL23 diagnostics and desktop transport; no real camera access."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import types
import contextlib
from unittest.mock import patch
import zipfile

from a6700_unlock import diagnostics
from a6700_unlock.desktop import physical_bridge
from a6700_unlock.desktop.controller import DesktopController


class DiagnosticsTests(unittest.TestCase):
    def test_assets_are_present_and_hash_verified(self):
        manifest = diagnostics.load_assets(diagnostics.asset_root())
        self.assertTrue(manifest['expected_runtime'])
        for name in ('boot_log.elf', 'cp_exception.elf', 'runtime.elf', 'operation_control.elf'):
            self.assertTrue((diagnostics.asset_root() / name).is_file())
        self.assertFalse(diagnostics.load_assets(diagnostics.asset_root())['operation_recorder']['hardware_verified'])

    def test_invalid_assets_stop_before_api_or_camera_access(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('a6700_unlock.diagnostics.load_assets', side_effect=ValueError('bad asset')),                     patch('a6700_unlock.diagnostics.current_api') as api:
                with self.assertRaisesRegex(ValueError, 'bad asset'):
                    diagnostics.export(camera=True, state_dir=Path(folder) / 'state',
                                       output_dir=Path(folder) / 'out', progress=lambda _: None)
            api.assert_not_called()

    def test_local_export_contains_only_allowlisted_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / 'state' / 'runs' / ('looks-' + 'a' * 32)
            run.mkdir(parents=True)
            (run / 'result.json').write_text(json.dumps(dict(
                action='inspect', status='inspected', verified=True,
                device_access=True, write_attempted=False,
                profile_sha256='b' * 64,
                plan=dict(features=[dict(id='fl23', state='enabled'),
                                    dict(id='unknown-feature', state='enabled')]),
                final_features=[dict(id='creative_look_names', state='enabled')],
                secret='must-not-be-exported')))
            result = diagnostics.export(state_dir=root / 'state', output_dir=root / 'out',
                                        progress=lambda _: None)
            self.assertEqual(result['status'], 'collected')
            self.assertFalse(result['camera'])
            self.assertEqual(result['record_count'], 1)
            path = Path(result['path'])
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {'report.json', '说明.txt'})
                report = json.loads(archive.read('report.json'))
            self.assertFalse(report['automatic_reproduction'])
            self.assertNotIn('camera', report)
            record = report['local_install']['records'][0]
            self.assertEqual(record['action'], 'inspect')
            self.assertEqual(record['profile_sha256'], 'b' * 64)
            self.assertNotIn('secret', record)
            self.assertEqual(record['observed_features'], [dict(id='fl23', state='enabled')])

    def test_filtered_logs_redact_network_paths_and_secrets(self):
        raw = (b'panic PC: 0x1234 path=/private/user/log.txt ip=192.168.1.20 mac=00:11:22:33:44:55\n'
               b'password=do-not-export panic\nordinary line\n')
        lines = diagnostics.relevant_log(raw)
        self.assertEqual(len(lines), 1)
        self.assertIn('panic', lines[0])
        self.assertIn('[PATH]', lines[0])
        self.assertIn('[IP]', lines[0])
        self.assertIn('[MAC]', lines[0])
        self.assertNotIn('password', '\n'.join(lines))

    def test_authentication_deadline_is_safe_in_guided_watcher_thread(self):
        result = []
        def worker():
            try:
                with diagnostics.authentication_deadline():
                    result.append('entered')
            except Exception as exc:
                result.append(exc)
        thread = __import__('threading').Thread(target=worker)
        thread.start(); thread.join(timeout=2)
        self.assertEqual(result, ['entered'])

    def test_load_unfinished_recorder_skips_attempted_stop_and_returns_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'state' / 'runs'
            old = root / ('fl23-diagnostic-' + 'a' * 32)
            new = root / ('fl23-diagnostic-' + 'b' * 32)
            old.mkdir(parents=True); new.mkdir(parents=True)
            nonce_old, nonce_new = bytes(range(16)), bytes(range(16, 32))
            (old / 'operation-start.request').write_bytes(b'A67P' + b'\0' * 4 + nonce_old + b'\0' * 16)
            (old / 'result.json').write_text(json.dumps(dict(status='recording',
                operation_id='old', mapping='0x1000', mapping_bytes=4096,
                boot_id='01234567-89ab-cdef-8123-456789abcdef')))
            (old / 'operation-stop.request').write_bytes(b'\0' * 8 + nonce_old + b'\0' * 24)
            (new / 'operation-start.request').write_bytes(b'A67P' + b'\0' * 4 + nonce_new + b'\0' * 16)
            (new / 'result.json').write_text(json.dumps(dict(status='recording',
                operation_id='new', mapping='0x2000', mapping_bytes=8192,
                boot_id='01234567-89ab-cdef-8123-456789abcdef')))
            recorder = diagnostics.load_unfinished_recorder(Path(folder) / 'state')
        self.assertEqual(recorder['operation_id'], 'new')
        self.assertEqual(recorder['nonce'], nonce_new.hex())
        self.assertEqual(recorder['mapping'], '0x2000')

    def test_latest_attempted_stop_does_not_resurrect_older_shared_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'state' / 'runs'
            old = root / ('fl23-diagnostic-' + 'a' * 32)
            new = root / ('fl23-diagnostic-' + 'b' * 32)
            old.mkdir(parents=True); new.mkdir(parents=True)
            nonce_old, nonce_new = bytes(range(16)), bytes(range(16, 32))
            for run, nonce, operation, mapping in ((old, nonce_old, 'old', '0x1000'),
                                                   (new, nonce_new, 'new', '0x2000')):
                (run / 'operation-start.request').write_bytes(b'A67P' + b'\0' * 4 + nonce + b'\0' * 16)
                (run / 'result.json').write_text(json.dumps(dict(status='recording',
                    operation_id=operation, mapping=mapping, mapping_bytes=4096,
                    boot_id='01234567-89ab-cdef-8123-456789abcdef')))
            (new / 'operation-stop.request').write_bytes(b'A67P' + b'\0' * 4 + nonce_new + b'\0' * 16)
            older_time = old / 'operation-start.request'
            newer_time = new / 'operation-start.request'
            older_time.touch()
            newer_time.touch()
            recorder = diagnostics.load_unfinished_recorder(Path(folder) / 'state')
        self.assertIsNone(recorder)

    def test_operation_history_is_in_zip_and_supports_missing_stop_after_reboot(self):
        module = diagnostics.load_parser(diagnostics.asset_root() / 'diagnostic_operation_control.py')
        nonce = bytes(range(16))
        raw = (b'START ' + bytes(16).hex().encode() + b'\nT1 SEL=1\nSTOP ' + bytes(16).hex().encode() +
               b'\nSTART ' + nonce.hex().encode() + b'\nT9 SEL=17\nT10 SEL=18\n')
        history = module.session_history(raw, nonce)
        self.assertFalse(history['stop_observed'])
        self.assertFalse(history['ready_observed'])  # legacy history remains readable
        self.assertEqual([row['selection'] for row in history['records']], [17, 18])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.zip'
            diagnostics.export_report(path, dict(camera=dict(operation_log=history)))
            with zipfile.ZipFile(path) as archive:
                self.assertIn('operation-history.txt', archive.namelist())
                text = archive.read('operation-history.txt').decode()
                self.assertIn('T10 SEL=18', text)
                self.assertNotIn('T1 SEL=1', text)

    def test_operation_history_parses_ready_initial_selection_and_sampling_error(self):
        module = diagnostics.load_parser(diagnostics.asset_root() / 'diagnostic_operation_control.py')
        nonce = bytes(range(16))
        ready = (b'START ' + nonce.hex().encode() + b'\nT1 READY SEL=0\n'
                 b'T2 SEL=17\nT3 SEL=18\nT4 ERR=-22\nSTOP ' + nonce.hex().encode() + b'\n')
        history = module.session_history(ready, nonce)
        self.assertTrue(history['ready_observed'])
        self.assertEqual(history['ready_selection'], 0)
        self.assertEqual([row['selection'] for row in history['records']], [0, 17, 18])
        self.assertEqual(history['sampling_errors'], [dict(elapsed_seconds=4, code=-22)])
        failed = b'START ' + nonce.hex().encode() + b'\nT1 ERR=-22\nSTOP ' + nonce.hex().encode() + b'\n'
        failure = module.session_history(failed, nonce)
        self.assertFalse(failure['ready_observed'])
        self.assertEqual(failure['records'], [])
        self.assertEqual(failure['sampling_errors'][0]['code'], -22)

    def test_operation_history_parses_multifield_live_and_saved_state(self):
        module = diagnostics.load_parser(diagnostics.asset_root() / 'diagnostic_operation_control.py')
        nonce = bytes(range(16))
        raw = (b'START ' + nonce.hex().encode() + b'\n'
               b'T1 READY REC=17 LIVE=17 SEL=0 MR=00000000000000000000000000\n'
               b'T2 STATE REC=18 LIVE=18 SEL=0 MR=12000000000000000000000000\n'
               b'T3 STATE REC=18 LIVE=18 SEL=18 MR=12000000000000000000000000\n'
               b'STOP ' + nonce.hex().encode() + b'\n')
        history = module.session_history(raw, nonce)
        self.assertTrue(history['ready_observed'])
        self.assertEqual(history['ready_selection'], 17)
        self.assertEqual(history['ready_state']['saved'], 0)
        self.assertEqual([row['selection'] for row in history['records']], [17, 18, 18])
        self.assertEqual([row['record'] for row in history['records']], [17, 18, 18])
        self.assertEqual([row['live'] for row in history['records']], [17, 18, 18])
        self.assertEqual([row['saved'] for row in history['records']], [0, 0, 18])
        self.assertEqual([row['mr'] for row in history['records']],
                         ['0' * 26, '12' + '0' * 24, '12' + '0' * 24])

    def test_operation_log_survives_later_camera_probe_failure(self):
        module = types.SimpleNamespace()
        nonce = bytes(range(16))
        module.request = lambda *args: (str(args[1]) + '-request').encode() if len(args) > 1 else b'boot-request'
        def parse(raw, config):
            if config == b'1-request':
                return dict(native_status=0, status='recovered', recovered_after_reboot=True,
                            history=b'persisted', history_bytes=9, history_sha256='a' * 64)
            if config == b'2-request':
                return dict(native_status=0, status='cleaned', cleanup_status=0)
            return dict(records=[], relevant_lines=[], fault_attribution='unproven')
        module.parse = parse
        module.session_history = lambda raw, value: dict(
            session_found=True, start_observed=True, stop_observed=False,
            records=[dict(elapsed_seconds=10, selection=18)], lines=['START test', 'T10 SEL=18'])
        backend = types.SimpleNamespace(cleanup_status=None, _fl23_transport_uncertain=False,
                                        assets=dict(reader=b'reader'))
        @contextlib.contextmanager
        def device_lock(state):
            yield
        api = types.SimpleNamespace(device_lock=device_lock, load_catalog=lambda: object(),
            load_device_profile=lambda catalog: types.SimpleNamespace(entries=[]),
            save_bytes=lambda path, raw: Path(path).write_bytes(raw),
            request=lambda *args: b'base-request', response=lambda *args: dict(status=0, sectors=[]),
            inspect_sectors=lambda *args: (dict(compatible=True, features=[]), None),
            Package=lambda: (_ for _ in ()).throw(RuntimeError('synthetic later failure')))
        with tempfile.TemporaryDirectory() as folder, \
                patch('a6700_unlock.diagnostics.backend_scope') as backend_scope, \
                patch('a6700_unlock.diagnostics.service_scope') as service_scope, \
                patch('a6700_unlock.diagnostics.load_parser', return_value=module):
            backend_scope.return_value.__enter__.return_value = backend
            service_scope.return_value.__enter__.return_value = lambda binary, config: b'reply'
            run = Path(folder) / 'state' / 'runs' / ('fl23-diagnostic-' + 'a' * 32)
            run.mkdir(parents=True)
            recorder = dict(nonce=nonce.hex(), mapping='0x44000000', mapping_bytes=131072,
                            boot_id='01234567-89ab-cdef-8123-456789abcdef')
            report = diagnostics.capture_camera(api, diagnostics.asset_root(), {'expected_runtime': []},
                Path(folder) / 'state', run, progress=lambda _: None, recorder=recorder)
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(recorder['stop_attempted'])
        self.assertIn('synthetic later failure', report['_error'])
        self.assertEqual(report['operation_log']['records'][0]['selection'], 18)
        self.assertTrue(report['operation_log']['recovered_after_reboot'])
        self.assertTrue(report['operation_log']['history_cleanup_verified'])

    def test_camera_error_is_returned_locally_but_not_exported(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('a6700_unlock.diagnostics.load_assets', return_value={'expected_runtime': []}), \
                    patch('a6700_unlock.diagnostics.current_api') as api, \
                    patch('a6700_unlock.diagnostics.capture_camera', return_value=dict(
                        status='incomplete', device_access=False, error_type='DeviceError',
                        _error='USB 认证失败（测试阶段）')):
                api.return_value.make_directory = lambda path: Path(path).mkdir(parents=True)
                result = diagnostics.export(camera=True, state_dir=Path(folder) / 'state',
                                            output_dir=Path(folder) / 'out', progress=lambda _: None)
            self.assertEqual(result['status'], 'incomplete')
            self.assertFalse(result['recorder_stop_attempted'])
            self.assertIn('USB 认证失败', result['error'])
            with zipfile.ZipFile(result['path']) as archive:
                report = json.loads(archive.read('report.json'))
            self.assertNotIn('_error', report['camera'])
            self.assertNotIn('USB 认证失败', json.dumps(report))

    def test_start_operation_log_restores_service_and_returns_mapping_identity(self):
        module = types.SimpleNamespace()
        module.request = lambda nonce, action: b'start-request'
        module.parse = lambda raw, config: dict(native_status=0, status='recording',
            mapping='0x44000000', mapping_bytes=131072,
            boot_id='01234567-89ab-cdef-8123-456789abcdef', sampling_ready=True)
        backend = types.SimpleNamespace(cleanup_status=None, assets=dict(reader=b'reader'))
        @contextlib.contextmanager
        def device_lock(state):
            yield
        @contextlib.contextmanager
        def backend_scope(api, profile, progress):
            yield backend
        @contextlib.contextmanager
        def service_scope(api, backend, run, report):
            yield lambda binary, config: b'reply'
            report['service_restore_verified'] = True
        api = types.SimpleNamespace(device_lock=device_lock, load_catalog=lambda: object(),
            load_device_profile=lambda catalog: types.SimpleNamespace(entries=[]),
            make_directory=lambda path: Path(path).mkdir(parents=True),
            save_bytes=lambda path, raw: Path(path).write_bytes(raw),
            request=lambda *args: b'base-request', response=lambda *args: dict(status=0, sectors=[]),
            inspect_sectors=lambda *args: (dict(compatible=True, features=[]), None))
        with tempfile.TemporaryDirectory() as folder, \
                patch('a6700_unlock.diagnostics.current_api', return_value=api), \
                patch('a6700_unlock.diagnostics.backend_scope', side_effect=backend_scope), \
                patch('a6700_unlock.diagnostics.service_scope', side_effect=service_scope), \
                patch('a6700_unlock.diagnostics.load_parser', return_value=module):
            recorder = diagnostics.start_operation_log(state_dir=Path(folder) / 'state',
                operation_id='11111111-1111-4111-8111-111111111111', progress=lambda _: None)
            run = Path(folder) / 'state' / 'runs' / recorder['run']
            result = json.loads((run / 'result.json').read_text())
        self.assertEqual(recorder['mapping'], '0x44000000')
        self.assertTrue(recorder['sampling_ready'])
        self.assertEqual(result['status'], 'recording')
        self.assertTrue(result['service_restore_verified'])
        self.assertFalse(result['firmware_writes'])

    def test_start_operation_log_keeps_service_running_when_optional_probe_degrades(self):
        module = types.SimpleNamespace()
        module.request = lambda nonce, action: b'start-request'
        module.parse = lambda raw, config: dict(native_status=0, status='recording',
            mapping='0x44000000', mapping_bytes=131072, sampling_ready=False,
            sampling_degraded=True, sampling_error=-22,
            boot_id='01234567-89ab-cdef-8123-456789abcdef')
        backend = types.SimpleNamespace(cleanup_status=None, assets=dict(reader=b'reader'))
        @contextlib.contextmanager
        def device_lock(state):
            yield
        @contextlib.contextmanager
        def backend_scope(api, profile, progress):
            yield backend
        @contextlib.contextmanager
        def service_scope(api, backend, run, report):
            yield lambda binary, config: b'reply'
            report['service_restore_verified'] = True
        api = types.SimpleNamespace(device_lock=device_lock, load_catalog=lambda: object(),
            load_device_profile=lambda catalog: types.SimpleNamespace(entries=[]),
            make_directory=lambda path: Path(path).mkdir(parents=True),
            save_bytes=lambda path, raw: Path(path).write_bytes(raw),
            request=lambda *args: b'base-request', response=lambda *args: dict(status=0, sectors=[]),
            inspect_sectors=lambda *args: (dict(compatible=True, features=[]), None))
        with tempfile.TemporaryDirectory() as folder, \
                patch('a6700_unlock.diagnostics.current_api', return_value=api), \
                patch('a6700_unlock.diagnostics.backend_scope', side_effect=backend_scope), \
                patch('a6700_unlock.diagnostics.service_scope', side_effect=service_scope), \
                patch('a6700_unlock.diagnostics.load_parser', return_value=module):
            recorder = diagnostics.start_operation_log(state_dir=Path(folder) / 'state',
                operation_id='11111111-1111-4111-8111-111111111111',
                progress=lambda _: None, backend=backend)
            run = next((Path(folder) / 'state' / 'runs').glob('fl23-diagnostic-*'))
            result = json.loads((run / 'result.json').read_text())
        self.assertFalse(recorder['sampling_ready'])
        self.assertTrue(recorder['sampling_degraded'])
        self.assertEqual(recorder['sampling_error'], -22)
        self.assertEqual(result['status'], 'recording')

    def test_start_operation_log_with_existing_backend_does_not_lock_or_authenticate_again(self):
        module = types.SimpleNamespace(request=lambda nonce, action: b'start-request',
            parse=lambda raw, config: dict(native_status=0, status='recording', mapping='0x44000000',
                mapping_bytes=131072, boot_id='01234567-89ab-cdef-8123-456789abcdef'))
        backend = types.SimpleNamespace(cleanup_status=None, assets=dict(reader=b'reader'))
        @contextlib.contextmanager
        def service_scope(api, backend, run, report):
            yield lambda binary, config: b'reply'
            report['service_restore_verified'] = True
        api = types.SimpleNamespace(load_catalog=lambda: object(),
            load_device_profile=lambda catalog: types.SimpleNamespace(entries=[]),
            make_directory=lambda path: Path(path).mkdir(parents=True),
            save_bytes=lambda path, raw: Path(path).write_bytes(raw),
            request=lambda *args: b'base-request', response=lambda *args: dict(status=0, sectors=[]),
            inspect_sectors=lambda *args: (dict(compatible=True, features=[]), None),
            device_lock=lambda state: (_ for _ in ()).throw(AssertionError('must reuse caller lock')))
        with tempfile.TemporaryDirectory() as folder, \
                patch('a6700_unlock.diagnostics.current_api', return_value=api), \
                patch('a6700_unlock.diagnostics.backend_scope', side_effect=AssertionError('must reuse backend')), \
                patch('a6700_unlock.diagnostics.service_scope', side_effect=service_scope), \
                patch('a6700_unlock.diagnostics.load_parser', return_value=module):
            recorder = diagnostics.start_operation_log(state_dir=Path(folder) / 'state',
                operation_id='operation', progress=lambda _: None, backend=backend)
        self.assertEqual(recorder['mapping'], '0x44000000')

    def test_capture_camera_with_existing_backend_reuses_caller_session(self):
        module = types.SimpleNamespace(
            request=lambda *args: (str(args[1]) + '-request').encode())
        def parse(raw, config):
            if config == b'1-request':
                return dict(native_status=0, status='recovered', recovered_after_reboot=True,
                            history=b'persisted', history_bytes=9, history_sha256='a' * 64)
            return dict(native_status=0, status='cleaned', cleanup_status=0)
        module.parse = parse
        module.session_history = lambda raw, nonce: dict(
            session_found=True, start_observed=True, stop_observed=False,
            records=[dict(elapsed_seconds=1, selection=18)], lines=['START', 'T1 SEL=18'])
        backend = types.SimpleNamespace(cleanup_status=None, _fl23_transport_uncertain=False,
                                        assets=dict(reader=b'reader'))
        @contextlib.contextmanager
        def service_scope(api, backend, run, report):
            yield lambda binary, config: b'reply'
            report['service_restore_verified'] = True
        api = types.SimpleNamespace(load_catalog=lambda: object(),
            load_device_profile=lambda catalog: types.SimpleNamespace(entries=[]),
            make_directory=lambda path: Path(path).mkdir(parents=True),
            save_bytes=lambda path, raw: Path(path).write_bytes(raw),
            request=lambda *args: b'base-request', response=lambda *args: dict(status=0, sectors=[]),
            inspect_sectors=lambda *args: (dict(compatible=True, features=[]), None),
            Package=lambda: (_ for _ in ()).throw(AssertionError('later probe should not run')),
            device_lock=lambda state: (_ for _ in ()).throw(AssertionError('must reuse caller lock')))
        with tempfile.TemporaryDirectory() as folder, \
                patch('a6700_unlock.diagnostics.current_api', return_value=api), \
                patch('a6700_unlock.diagnostics.backend_scope', side_effect=AssertionError('must reuse backend')), \
                patch('a6700_unlock.diagnostics.service_scope', side_effect=service_scope), \
                patch('a6700_unlock.diagnostics.load_parser', return_value=module):
            run = Path(folder) / 'run'
            run.mkdir(parents=True)
            report = diagnostics.capture_camera(api, diagnostics.asset_root(),
                {'expected_runtime': []}, Path(folder) / 'state',
                run, progress=lambda _: None,
                recorder=dict(nonce=bytes(range(16)).hex(), mapping='0x44000000',
                              mapping_bytes=131072, boot_id='01234567-89ab-cdef-8123-456789abcdef'),
                backend=backend)
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(report['operation_log']['history_cleanup_verified'])

    def bridge(self, camera):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            output = io.StringIO()
            requests = json.dumps(dict(command='diagnostic', camera=camera)) + '\n' + json.dumps(dict(command='shutdown')) + '\n'
            with patch.object(controller, 'end_session', wraps=controller.end_session) as end_session, \
                    patch('a6700_unlock.diagnostics.export', return_value=dict(
                        status='collected', path='/tmp/synthetic.zip', camera=camera,
                        service_recovery_required=False, record_count=0)) as export, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', io.StringIO(requests)), \
                    patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            result = next(row['result'] for row in messages if row['type'] == 'diagnostic-result')
            self.assertEqual(result['status'], 'collected')
            self.assertEqual(export.call_args.kwargs['camera'], camera)
            # Shutdown always closes the session; camera diagnostics close it once more before collection.
            self.assertEqual(end_session.call_count, 2 if camera else 1)
            self.assertNotIn('write_attempted', result)

    def test_bridge_camera_diagnostic_closes_existing_session_first(self):
        self.bridge(True)

    def test_bridge_local_diagnostic_does_not_touch_camera_session(self):
        self.bridge(False)


if __name__ == '__main__':
    unittest.main()
