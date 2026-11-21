"""Structured cloud feedback and guided reproduction tests; no network or camera."""
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch

from a6700_unlock import feedback, guided_diagnostics
from a6700_unlock.desktop import physical_bridge
from a6700_unlock.desktop.controller import DesktopController
from a6700_unlock.usb import legacy


RESULT = dict(status='collected', path='/tmp/private-report.zip', operation_id='11111111-1111-4111-8111-111111111111',
              mode='direct', events=[dict(step_id='camera_collect', phase='inspect', status='passed',
                                           source='device', elapsed_ms=20)],
              service_recovery_required=False)


class Response:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()


class FeedbackTests(unittest.TestCase):
    def test_build_feedback_uses_only_structured_allowlist(self):
        report = feedback.build_feedback(RESULT, '选择 FL2 后重启')
        diagnostic = report['diagnostic']
        self.assertEqual(report['consent_version'], 'feedback-v1')
        self.assertEqual(report['description'], '选择 FL2 后重启')
        self.assertEqual(diagnostic['action'], 'diagnose')
        self.assertEqual(diagnostic['transaction_status'], 'verified')
        self.assertEqual(diagnostic['stage_id'], 'direct_collect')
        self.assertEqual(diagnostic['operation_id'], RESULT['operation_id'])
        self.assertNotIn('path', json.dumps(report))
        self.assertNotIn('private-report', json.dumps(report))

    def test_text_and_image_feedback_has_explicit_not_started_diagnostic(self):
        result = dict(status='not_requested', path=None,
                      operation_id='11111111-1111-4111-8111-111111111111',
                      mode='not_requested', events=[])
        report = feedback.build_feedback(result, '仅文字和图片反馈')
        diagnostic = report['diagnostic']
        self.assertEqual(diagnostic['transaction_status'], 'not_started')
        self.assertEqual(diagnostic['error_code'], 'none')
        self.assertEqual(diagnostic['stage_id'], 'not_requested')
        self.assertEqual(diagnostic['events'], [])

    def test_collected_report_is_recovered_for_upload_after_app_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'state'
            sent = state / 'feedback-sent'
            sent.mkdir(parents=True)
            operation_id = '11111111-1111-4111-8111-111111111111'
            (sent / (operation_id + '.json')).write_text(json.dumps(dict(payload=dict(diagnostic=dict(
                transaction_status='verified', operation_id=operation_id, events=[])))), encoding='utf-8')
            downloads = Path(folder) / 'Downloads'
            downloads.mkdir()
            archive_path = downloads / 'FL23-故障报告-test.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('report.json', json.dumps(dict(
                    operation_id=operation_id, collection_mode='guided',
                    camera=dict(status='collected'), local_install=dict(records=[]))))
            recovered = feedback.recover_diagnostic(state, downloads)
            self.assertEqual(recovered['status'], 'collected')
            self.assertEqual(recovered['operation_id'], operation_id)
            self.assertEqual(recovered['path'], str(archive_path))

    def test_failed_upload_reuses_identical_id_token_and_bytes_on_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def timeout(request, timeout):
                calls.append(request)
                raise TimeoutError('synthetic timeout')
            first = feedback.upload_feedback(RESULT, state_dir=folder, opener=timeout)
            self.assertEqual(first['status'], 'unknown')
            pending = list((Path(folder) / 'feedback-pending').glob('*.json'))
            self.assertEqual(len(pending), 1)
            saved = json.loads(pending[0].read_text(encoding='utf-8'))

            def success(request, timeout):
                calls.append(request)
                return Response(201, dict(status='received', expires_at='2026-12-07T00:00:00Z'))
            second = feedback.upload_feedback(RESULT, state_dir=folder, opener=success)
            self.assertEqual(second['status'], 'received')
            self.assertEqual(len(calls), 4)  # three bounded transport attempts plus one manual retry
            self.assertEqual(calls[0].data, calls[-1].data)
            self.assertEqual(calls[0].headers['X-delete-token'], calls[-1].headers['X-delete-token'])
            self.assertEqual(json.loads(calls[-1].data), saved['payload'])
            self.assertFalse(pending[0].exists())
            self.assertEqual(len(list((Path(folder) / 'feedback-sent').glob('*.json'))), 1)

    def test_transport_retry_uses_identical_request_and_succeeds(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def flaky(request, timeout):
                calls.append(request)
                if len(calls) < 3:
                    raise TimeoutError('synthetic handshake timeout')
                return Response(201, dict(status='received', expires_at='2026-12-07T00:00:00Z'))
            with patch('a6700_unlock.feedback.time.sleep'):
                result = feedback.upload_feedback(RESULT, state_dir=folder, opener=flaky)
            self.assertEqual(result['status'], 'received')
            self.assertEqual(len(calls), 3)
            self.assertEqual({call.data for call in calls}, {calls[0].data})
            self.assertEqual({call.headers['X-delete-token'] for call in calls}, {calls[0].headers['X-delete-token']})

    def test_transport_retry_is_bounded_and_preserves_pending_report(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def timeout(request, timeout):
                calls.append(request)
                raise TimeoutError('synthetic timeout')
            with patch('a6700_unlock.feedback.time.sleep'):
                result = feedback.upload_feedback(RESULT, state_dir=folder, opener=timeout)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(list((Path(folder) / 'feedback-pending').glob('*.json'))), 1)

    def test_connection_failure_explains_local_dns_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            def refused(request, timeout):
                raise feedback.urllib.error.URLError(OSError(61, 'Connection refused'))
            result = feedback.upload_feedback(RESULT, state_dir=folder, opener=refused)
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('无法连接云端', result['error'])
        self.assertIn('公网例外', result['error'])

    def test_legacy_start_event_is_repaired_and_request_has_app_user_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = dict(RESULT, events=[dict(step_id='operation_recorder', phase='start',
                                               status='passed', source='device', elapsed_ms=0)])
            def timeout(request, timeout):
                raise TimeoutError('synthetic timeout')
            feedback.upload_feedback(legacy, state_dir=folder, opener=timeout)
            pending = next((Path(folder) / 'feedback-pending').glob('*.json'))
            self.assertEqual(json.loads(pending.read_text(encoding='utf-8'))['payload']['diagnostic']['events'][0]['phase'], 'start')

            requests = []
            def success(request, timeout):
                requests.append(request)
                return Response(201, dict(status='received', expires_at='2026-12-08T00:00:00Z'))
            result = feedback.upload_feedback(legacy, state_dir=folder, opener=success)
            self.assertEqual(result['status'], 'received')
            self.assertTrue(requests[0].headers['User-agent'].startswith('Powerful6700/'))
            payload = json.loads(requests[0].data)
            self.assertEqual(payload['diagnostic']['events'][0]['phase'], 'connect')
            sent = next((Path(folder) / 'feedback-sent').glob('*.json'))
            self.assertEqual(json.loads(sent.read_text(encoding='utf-8'))['payload']['diagnostic']['events'][0]['phase'], 'connect')

    def test_http_429_is_bounded_and_keeps_pending_report(self):
        with tempfile.TemporaryDirectory() as folder:
            error = feedback.urllib.error.HTTPError('https://example', 429, 'limited', {},
                io.BytesIO(b'{"error":{"code":"rate_limited"}}'))
            with patch('urllib.request.urlopen', side_effect=error):
                result = feedback.upload_feedback(RESULT, state_dir=folder)
            self.assertEqual(result['status'], 'rate_limited')
            self.assertEqual(len(list((Path(folder) / 'feedback-pending').glob('*.json'))), 1)
            self.assertEqual(len(list((Path(folder) / 'feedback-pending').glob('*.json'))), 1)


class BridgeGuidedTests(unittest.TestCase):
    def test_startup_publishes_recovered_collected_report(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            output = io.StringIO()
            recovered = dict(status='collected', path='/tmp/recovered.zip', camera=True,
                             operation_id='11111111-1111-4111-8111-111111111111',
                             mode='guided', service_recovery_required=False, events=[])
            with patch('a6700_unlock.feedback.recover_diagnostic', return_value=recovered) as recover, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', io.StringIO(json.dumps(dict(command='shutdown')) + '\n')), \
                    patch('sys.stdout', output):
                physical_bridge.main()
            recover.assert_called_once()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(any(row['type'] == 'diagnostic-result' and
                                row['result']['path'] == '/tmp/recovered.zip' for row in messages))

    def test_text_image_only_upload_needs_no_camera_or_diagnostic_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            output = io.StringIO()
            commands = [dict(command='upload_feedback', description='仅文字图片', images=[]),
                        dict(command='upload_feedback', description='仅文字图片', images=[]),
                        dict(command='shutdown')]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            calls = []
            def fake_upload(result, description, include_artifact=False, images=None):
                calls.append(dict(result=result, include_artifact=include_artifact))
                return dict(status='received', feedback_id='22222222-2222-4222-8222-222222222222')
            with patch('a6700_unlock.feedback.upload_feedback', side_effect=fake_upload), \
                    patch('a6700_unlock.diagnostics.export') as export, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]['result']['status'], 'not_requested')
            self.assertEqual(calls[0]['result']['operation_id'], calls[1]['result']['operation_id'])
            self.assertFalse(calls[0]['include_artifact'])
            self.assertFalse(calls[1]['include_artifact'])
            export.assert_not_called()

    def test_guided_requires_connected_capture_service(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            output = io.StringIO()
            requests = json.dumps(dict(command='guided_diagnostic', action='start')) + '\n' + json.dumps(dict(command='shutdown')) + '\n'
            with patch('a6700_unlock.diagnostics.export') as export,                     patch.object(physical_bridge, 'DesktopController', return_value=controller),                     patch('sys.argv', ['test']), patch('sys.stdin', io.StringIO(requests)),                     patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'error' for row in messages))
            export.assert_not_called()

    def test_degraded_optional_probe_still_enters_user_operation_state(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            requests = json.dumps(dict(command='guided_diagnostic', action='start')) + '\n' + json.dumps(dict(command='shutdown')) + '\n'
            recorder = dict(operation_id='operation', nonce='00' * 16, mapping='0x44000000',
                            mapping_bytes=131072, boot_id='01234567-89ab-cdef-8123-456789abcdef',
                            sampling_ready=False, sampling_degraded=True, sampling_error=-22)
            with patch.object(controller.service, 'start_operation_log', return_value=recorder), \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', io.StringIO(requests)), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            ready = next(row for row in messages if row['type'] == 'guided-state' and row['status'] == 'waiting_user')
            self.assertIn('ERR=-22', ready['error'])
            self.assertIn('仍可继续操作', ready['error'])

    def test_manual_guided_collection_then_structured_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            controller.service._session_backend = object()
            output = io.StringIO()
            commands = [
                dict(command='guided_diagnostic', action='start'),
                dict(command='guided_diagnostic', action='collect'),
                dict(command='upload_feedback', description='选择 FL3 后重启'),
                dict(command='shutdown'),
            ]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            uploaded = {}
            def fake_upload(result, description, include_artifact=False, images=None):
                uploaded.update(result=result, description=description, include_artifact=include_artifact)
                return dict(status='received', feedback_id='22222222-2222-4222-8222-222222222222')
            recorder = dict(operation_id='operation', nonce='00' * 16, mapping='0x44000000',
                            mapping_bytes=131072, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            collected = dict(status='collected', path='/tmp/guided.zip', camera=True,
                             operation_id='operation', mode='guided', service_recovery_required=False)
            with patch.object(controller, 'end_session', wraps=controller.end_session) as end_session,                     patch('a6700_unlock.guided_diagnostics.stable_msc_observed', return_value=True) as wait_msc,                     patch('a6700_unlock.guided_diagnostics.watch', return_value='cancelled'),                     patch.object(controller.service, 'start_operation_log', return_value=recorder) as start_recorder,                     patch.object(controller.service, 'collect_operation_log', return_value=collected) as collect_recorder,                     patch('a6700_unlock.diagnostics.export') as export,                     patch('a6700_unlock.feedback.upload_feedback', side_effect=fake_upload),                     patch.object(physical_bridge, 'DesktopController', return_value=controller),                     patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(end_session.call_count, 2)
            start_recorder.assert_called_once()
            collect_recorder.assert_called_once()
            self.assertEqual(collect_recorder.call_args.kwargs['recorder'], recorder)
            export.assert_not_called()
            wait_msc.assert_not_called()
            self.assertEqual(uploaded['description'], '选择 FL3 后重启')
            self.assertTrue(uploaded['include_artifact'])
            self.assertEqual(uploaded['result']['path'], '/tmp/guided.zip')
            self.assertTrue(any(row['type'] == 'feedback-result' and row['result']['status'] == 'received'
                                for row in messages))

    def test_powered_off_collection_waits_for_retrieval_without_new_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            output = io.StringIO()
            commands = [dict(command='guided_diagnostic', action='start'),
                        dict(command='guided_diagnostic', action='collect'),
                        dict(command='shutdown')]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            recorder = dict(operation_id='operation', nonce='00' * 16, mapping='0x44000000',
                            mapping_bytes=131072, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            with patch('a6700_unlock.guided_diagnostics.stable_msc_observed', return_value=False), \
                    patch('a6700_unlock.guided_diagnostics.watch', return_value='cancelled'), \
                    patch.object(controller.service, 'start_operation_log', return_value=recorder), \
                    patch('a6700_unlock.diagnostics.export') as export, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            waiting = next(row for row in messages if row['type'] == 'guided-state'
                           and row['status'] == 'awaiting_retrieval')
            self.assertIn('不需要重新复现', waiting['error'])
            export.assert_not_called()

    def test_pre_stop_partial_recovers_in_same_action_and_uploads_complete_report(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            commands = [
                dict(command='guided_diagnostic', action='start'),
                dict(command='guided_diagnostic', action='collect'),
                dict(command='upload_feedback', description='选择 FL3 后重启'),
                dict(command='shutdown'),
            ]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            recorder = dict(operation_id='operation', nonce='00' * 16, mapping='0x44000000',
                            mapping_bytes=131072, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            results = [
                dict(status='incomplete', path='/tmp/partial.zip', camera=True,
                     operation_id='operation', mode='guided', service_recovery_required=True),
                dict(status='collected', path='/tmp/complete.zip', camera=True,
                     operation_id='operation', mode='guided', service_recovery_required=False),
            ]
            uploaded = {}
            def fake_upload(result, description, include_artifact=False, images=None):
                uploaded.update(result=result, description=description, include_artifact=include_artifact)
                return dict(status='received', feedback_id='33333333-3333-4333-8333-333333333333')
            with patch('a6700_unlock.guided_diagnostics.stable_msc_observed', return_value=True), \
                    patch('a6700_unlock.guided_diagnostics.watch', return_value='cancelled'), \
                    patch.object(controller.service, 'start_operation_log', return_value=recorder), \
                    patch('a6700_unlock.diagnostics.export', side_effect=results) as export, \
                    patch('a6700_unlock.feedback.upload_feedback', side_effect=fake_upload), \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'collected'
                                for row in messages))
            self.assertEqual(export.call_count, 2)
            self.assertEqual(export.call_args_list[0].kwargs['recorder'], recorder)
            self.assertEqual(export.call_args_list[1].kwargs['recorder'], recorder)
            self.assertEqual(uploaded['result']['path'], '/tmp/complete.zip')

    def test_restart_stops_old_log_then_starts_new_recording_on_same_session(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            commands = [
                dict(command='guided_diagnostic', action='start'),
                dict(command='guided_diagnostic', action='restart'),
                dict(command='shutdown'),
            ]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            old = dict(operation_id='old', nonce='00' * 16, mapping='0x1000',
                       mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            new = dict(operation_id='new', nonce='11' * 16, mapping='0x2000',
                       mapping_bytes=8192, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            collected = dict(status='collected', path='/tmp/old.zip', camera=True,
                             operation_id='old', mode='guided', service_recovery_required=False)
            with patch.object(controller, 'end_session', wraps=controller.end_session), \
                    patch.object(controller.service, 'start_operation_log', side_effect=[old, new]) as start, \
                    patch.object(controller.service, 'collect_operation_log', return_value=collected) as collect, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(start.call_count, 2)
            self.assertEqual(collect.call_count, 1)
            self.assertEqual(collect.call_args.kwargs['recorder'], old)
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'waiting_user'
                                for row in messages))
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'restarting'
                                for row in messages))
            self.assertTrue(any(row['type'] == 'diagnostic-progress' and '旧日志已取回并清理' in row['message']
                                for row in messages))

    def test_restart_reconnect_can_be_cancelled_without_blocking_bridge(self):
        class Commands:
            def __init__(self):
                self.rows = queue.Queue()
            def __iter__(self):
                return self
            def __next__(self):
                row = self.rows.get()
                if row is None:
                    raise StopIteration
                return json.dumps(row) + '\n'
            def send(self, **row):
                self.rows.put(row)

        class Output(io.StringIO):
            def __init__(self):
                super().__init__()
                self.output_lock = threading.Lock()
            def write(self, value):
                with self.output_lock:
                    return super().write(value)
            def getvalue(self):
                with self.output_lock:
                    return super().getvalue()

        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            run = state / 'runs' / ('fl23-diagnostic-' + 'd' * 32)
            run.mkdir(parents=True)
            nonce = bytes(range(16))
            (run / 'operation-start.request').write_bytes(b'A67P' + b'\0' * 4 + nonce + b'\0' * 16)
            (run / 'result.json').write_text(json.dumps(dict(
                status='recording', operation_id='old', mapping='0x1000',
                mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')), encoding='utf-8')
            controller = DesktopController(None, mode='physical', physical_state_dir=state)
            controller.service.connected = False
            commands = Commands()
            output = Output()
            reconnect_entered = threading.Event()
            finish_reconnect = threading.Event()

            def reconnect(action, progress):
                reconnect_entered.set()
                if not finish_reconnect.wait(3):
                    raise TimeoutError('test reconnect was not released')
                controller.service.connected = True
                return dict(status='inspected')

            def messages():
                return [json.loads(line) for line in output.getvalue().splitlines()]

            def wait_for(predicate):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    rows = messages()
                    if predicate(rows):
                        return rows
                    time.sleep(0.01)
                self.fail('bridge output did not reach expected state: %r' % messages())

            with patch.object(controller.service, 'perform', side_effect=reconnect), \
                    patch.object(controller.service, 'start_operation_log') as start, \
                    patch.object(controller.service, 'collect_operation_log') as collect, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test', '--state-dir', str(state)]), \
                    patch('sys.stdin', commands), patch('sys.stdout', output):
                bridge = threading.Thread(target=physical_bridge.main)
                bridge.start()
                try:
                    wait_for(lambda rows: any(row['type'] == 'guided-state' and
                                              row['status'] == 'awaiting_retrieval' for row in rows))
                    commands.send(command='guided_diagnostic', action='restart')
                    self.assertTrue(reconnect_entered.wait(2))
                    commands.send(command='guided_diagnostic', action='cancel')
                    wait_for(lambda rows: any(row['type'] == 'guided-state' and
                                              row['status'] == 'cancelling' for row in rows))
                    self.assertFalse(finish_reconnect.is_set())
                    start.assert_not_called()
                    collect.assert_not_called()
                    finish_reconnect.set()
                    wait_for(lambda rows: any(row['type'] == 'guided-state' and
                                              row['status'] == 'awaiting_retrieval' and
                                              '原日志未清理' in (row.get('error') or '') for row in rows))
                    start.assert_not_called()
                    collect.assert_not_called()
                    commands.send(command='shutdown')
                    bridge.join(3)
                    self.assertFalse(bridge.is_alive())
                finally:
                    finish_reconnect.set()
                    if bridge.is_alive():
                        commands.send(command='shutdown')
                        bridge.join(3)

    def test_restart_does_not_start_new_recorder_when_old_log_is_incomplete(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            commands = [dict(command='guided_diagnostic', action='start'),
                        dict(command='guided_diagnostic', action='restart'),
                        dict(command='shutdown')]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            recorder = dict(operation_id='old', nonce='00' * 16, mapping='0x1000',
                            mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            incomplete = dict(status='incomplete', path='/tmp/old-partial.zip', camera=True,
                              operation_id='old', mode='guided', service_recovery_required=True)
            with patch.object(controller.service, 'start_operation_log', return_value=recorder) as start, \
                    patch.object(controller.service, 'collect_operation_log', return_value=incomplete), \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            start.assert_called_once()
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'awaiting_retrieval' and
                                '未开始新录制' in (row.get('error') or '') for row in messages))

    def test_pre_stop_incomplete_closes_stale_session_and_retries_over_msc(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            commands = [dict(command='guided_diagnostic', action='start'),
                        dict(command='guided_diagnostic', action='collect'),
                        dict(command='shutdown')]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            recorder = dict(operation_id='operation', nonce='00' * 16, mapping='0x1000',
                            mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            incomplete = dict(status='incomplete', path='/tmp/pre-stop.zip', camera=True,
                              operation_id='operation', mode='guided', service_recovery_required=True,
                              recorder_stop_attempted=False)
            collected = dict(status='collected', path='/tmp/msc.zip', camera=True,
                             operation_id='operation', mode='guided', service_recovery_required=False,
                             recorder_stop_attempted=True)
            with patch.object(controller, 'end_session', wraps=controller.end_session) as end_session, \
                    patch.object(controller.service, 'start_operation_log', return_value=recorder), \
                    patch.object(controller.service, 'collect_operation_log', return_value=incomplete), \
                    patch('a6700_unlock.guided_diagnostics.stable_msc_observed', return_value=True) as stable, \
                    patch('a6700_unlock.diagnostics.export', return_value=collected) as export, \
                    patch('a6700_unlock.desktop.physical_bridge.time.sleep'), \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(end_session.call_count, 2)  # fallback plus final shutdown
            stable.assert_called_once()
            export.assert_called_once()
            self.assertTrue(any(row['type'] == 'diagnostic-progress' and '尚未发起停止请求' in row['message']
                                for row in messages))
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'collected'
                                for row in messages))

    def test_attempted_stop_incomplete_never_reconnects_or_replays(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            commands = [dict(command='guided_diagnostic', action='start'),
                        dict(command='guided_diagnostic', action='collect'),
                        dict(command='shutdown')]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            recorder = dict(operation_id='operation', nonce='00' * 16, mapping='0x1000',
                            mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            incomplete = dict(status='incomplete', path='/tmp/uncertain.zip', camera=True,
                              operation_id='operation', mode='guided', service_recovery_required=True,
                              recorder_stop_attempted=True)
            with patch.object(controller, 'end_session', wraps=controller.end_session) as end_session, \
                    patch.object(controller.service, 'start_operation_log', return_value=recorder), \
                    patch.object(controller.service, 'collect_operation_log', return_value=incomplete), \
                    patch('a6700_unlock.guided_diagnostics.stable_msc_observed') as stable, \
                    patch('a6700_unlock.diagnostics.export') as export, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(end_session.call_count, 1)  # only final shutdown
            stable.assert_not_called()
            export.assert_not_called()
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'awaiting_retrieval' and
                                '未自动重连或重试' in (row.get('error') or '') for row in messages))

    def test_exception_after_durable_stop_marker_never_replays(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            commands = [dict(command='guided_diagnostic', action='start'),
                        dict(command='guided_diagnostic', action='collect'),
                        dict(command='shutdown')]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            recorder = dict(operation_id='operation', nonce='00' * 16, mapping='0x1000',
                            mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            def uncertain(*args, **kwargs):
                recorder['stop_attempted'] = True
                raise TimeoutError('synthetic lost stop result')
            with patch.object(controller.service, 'start_operation_log', return_value=recorder), \
                    patch.object(controller.service, 'collect_operation_log', side_effect=uncertain), \
                    patch('a6700_unlock.guided_diagnostics.stable_msc_observed') as stable, \
                    patch('a6700_unlock.diagnostics.export') as export, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            stable.assert_not_called()
            export.assert_not_called()
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'awaiting_retrieval' and
                                '结果未确认' in (row.get('error') or '') for row in messages))

    def test_app_restart_recovers_unfinished_power_cycle_session(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            run = state / 'runs' / ('fl23-diagnostic-' + 'c' * 32)
            run.mkdir(parents=True)
            nonce = bytes(range(16))
            (run / 'operation-start.request').write_bytes(b'A67P' + b'\0' * 4 + nonce + b'\0' * 16)
            (run / 'result.json').write_text(json.dumps(dict(
                status='recording', operation_id='recovery', mapping='0x1000',
                mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')), encoding='utf-8')
            controller = DesktopController(None, mode='physical', physical_state_dir=state)
            output = io.StringIO()
            requests = json.dumps(dict(command='shutdown')) + '\n'
            with patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test', '--state-dir', str(state)]), \
                    patch('sys.stdin', io.StringIO(requests)), patch('sys.stdout', output):
                physical_bridge.main()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            recovered = next(row for row in messages if row['type'] == 'guided-state'
                             and row['status'] == 'awaiting_retrieval')
            self.assertEqual(recovered['operation_id'], 'recovery')
            self.assertIn('不需要重新复现', recovered['error'])

    def test_recovered_power_cycle_collection_passes_original_recorder_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            run = state / 'runs' / ('fl23-diagnostic-' + 'c' * 32)
            run.mkdir(parents=True)
            nonce = bytes(range(16))
            (run / 'operation-start.request').write_bytes(b'A67P' + b'\0' * 4 + nonce + b'\0' * 16)
            (run / 'result.json').write_text(json.dumps(dict(
                status='recording', operation_id='recovery', mapping='0x1000',
                mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')), encoding='utf-8')
            controller = DesktopController(None, mode='physical', physical_state_dir=state)
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            requests = json.dumps(dict(command='guided_diagnostic', action='collect')) + '\n' + json.dumps(dict(command='shutdown')) + '\n'
            collected = dict(status='collected', path='/tmp/recovered.zip', camera=True,
                             operation_id='recovery', mode='guided', service_recovery_required=False,
                             recorder_stop_attempted=True)
            with patch.object(controller.service, 'collect_operation_log', return_value=collected) as collect, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test', '--state-dir', str(state)]), \
                    patch('sys.stdin', io.StringIO(requests)), patch('sys.stdout', output):
                physical_bridge.main()
            collect.assert_called_once()
            recorder = collect.call_args.kwargs['recorder']
            self.assertEqual(recorder['nonce'], nonce.hex())
            self.assertEqual(recorder['mapping'], '0x1000')
            self.assertEqual(collect.call_args.kwargs['operation_id'], 'recovery')
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'collected'
                                for row in messages))

    def test_pre_stop_reconnect_retries_three_times_in_one_action(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            run = state / 'runs' / ('fl23-diagnostic-' + 'd' * 32)
            run.mkdir(parents=True)
            nonce = bytes(range(16))
            (run / 'operation-start.request').write_bytes(b'A67P' + b'\0' * 4 + nonce + b'\0' * 16)
            (run / 'result.json').write_text(json.dumps(dict(
                status='recording', operation_id='recovery', mapping='0x1000',
                mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')), encoding='utf-8')
            controller = DesktopController(None, mode='physical', physical_state_dir=state)
            controller.service.connected = False
            output = io.StringIO()
            requests = json.dumps(dict(command='guided_diagnostic', action='collect')) + '\n' + json.dumps(dict(command='shutdown')) + '\n'
            results = [
                dict(status='incomplete', camera=True, operation_id='recovery', mode='guided',
                     recorder_stop_attempted=False),
                dict(status='incomplete', camera=True, operation_id='recovery', mode='guided',
                     recorder_stop_attempted=False),
                dict(status='collected', path='/tmp/recovered.zip', camera=True,
                     operation_id='recovery', mode='guided', recorder_stop_attempted=True),
            ]
            with patch('a6700_unlock.guided_diagnostics.stable_msc_observed', return_value=True) as stable, \
                    patch('a6700_unlock.diagnostics.export', side_effect=results) as export, \
                    patch('a6700_unlock.desktop.physical_bridge.time.sleep'), \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test', '--state-dir', str(state)]), \
                    patch('sys.stdin', io.StringIO(requests)), patch('sys.stdout', output):
                physical_bridge.main()
            self.assertEqual(stable.call_count, 3)
            self.assertEqual(export.call_count, 3)
            self.assertEqual(export.call_args_list[0].kwargs['recorder']['nonce'], nonce.hex())
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(any(row['type'] == 'diagnostic-progress' and '第 1/3 次连接' in row['message']
                                for row in messages))
            self.assertTrue(any(row['type'] == 'diagnostic-progress' and '第 2/3 次连接' in row['message']
                                for row in messages))
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'collected'
                                for row in messages))

    def test_pre_stop_reconnect_stops_immediately_once_stop_is_attempted(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            run = state / 'runs' / ('fl23-diagnostic-' + 'e' * 32)
            run.mkdir(parents=True)
            nonce = bytes(range(16))
            (run / 'operation-start.request').write_bytes(b'A67P' + b'\0' * 4 + nonce + b'\0' * 16)
            (run / 'result.json').write_text(json.dumps(dict(
                status='recording', operation_id='recovery', mapping='0x1000',
                mapping_bytes=4096, boot_id='01234567-89ab-cdef-8123-456789abcdef')), encoding='utf-8')
            controller = DesktopController(None, mode='physical', physical_state_dir=state)
            controller.service.connected = False
            output = io.StringIO()
            requests = json.dumps(dict(command='guided_diagnostic', action='collect')) + '\n' + json.dumps(dict(command='shutdown')) + '\n'
            uncertain = dict(status='incomplete', camera=True, operation_id='recovery', mode='guided',
                             recorder_stop_attempted=True)
            with patch('a6700_unlock.guided_diagnostics.stable_msc_observed', return_value=True) as stable, \
                    patch('a6700_unlock.diagnostics.export', return_value=uncertain) as export, \
                    patch('a6700_unlock.desktop.physical_bridge.time.sleep'), \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test', '--state-dir', str(state)]), \
                    patch('sys.stdin', io.StringIO(requests)), patch('sys.stdout', output):
                physical_bridge.main()
            stable.assert_called_once()
            export.assert_called_once()
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(any(row['type'] == 'guided-state' and row['status'] == 'awaiting_retrieval' and
                                '未自动重试' in (row.get('error') or '') for row in messages))

    def test_cancel_uses_joined_collection_instead_of_leaving_camera_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(None, mode='physical', physical_state_dir=folder)
            controller.compatible = True
            controller.service.connected = True
            controller.service._session_backend = object()
            output = io.StringIO()
            commands = [dict(command='guided_diagnostic', action='start'),
                        dict(command='guided_diagnostic', action='cancel'),
                        dict(command='shutdown')]
            stdin = io.StringIO(''.join(json.dumps(row) + '\n' for row in commands))
            recorder = dict(operation_id='operation', nonce='00' * 16, mapping='0x44000000',
                            mapping_bytes=131072, boot_id='01234567-89ab-cdef-8123-456789abcdef')
            collected = dict(status='collected', path='/tmp/cancelled.zip', camera=True,
                             operation_id='operation', mode='guided', service_recovery_required=False)
            with patch('a6700_unlock.guided_diagnostics.stable_msc_observed') as stable, \
                    patch.object(controller.service, 'start_operation_log', return_value=recorder), \
                    patch.object(controller.service, 'collect_operation_log', return_value=collected) as collect, \
                    patch('a6700_unlock.diagnostics.export') as export, \
                    patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                    patch('sys.argv', ['test']), patch('sys.stdin', stdin), patch('sys.stdout', output):
                physical_bridge.main()
            collect.assert_called_once()
            self.assertEqual(collect.call_args.kwargs['recorder'], recorder)
            stable.assert_not_called()
            export.assert_not_called()


class ArtifactUploadTests(unittest.TestCase):
    def result_with_zip(self, folder):
        path = Path(folder) / 'report.zip'
        path.write_bytes(b'PK\x03\x04 synthetic report.json PK\x05\x06')
        return dict(RESULT, path=str(path))

    def test_json_then_zip_uses_same_feedback_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.result_with_zip(folder)
            calls = []
            def success(request, timeout):
                calls.append(request)
                if request.full_url.endswith('/artifact'):
                    return Response(200, dict(status='received'))
                return Response(201, dict(status='received', expires_at='2026-12-07T00:00:00Z'))
            uploaded = feedback.upload_feedback(result, state_dir=folder, opener=success, include_artifact=True)
            self.assertEqual(uploaded['status'], 'received')
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[0].full_url.endswith('/v1/feedback'))
            self.assertTrue(calls[1].full_url.endswith('/artifact'))
            self.assertEqual(calls[0].headers['X-delete-token'], calls[1].headers['X-delete-token'])
            self.assertEqual(calls[1].data, Path(result['path']).read_bytes())

    def test_failure_zip_retries_temporary_storage_without_resending_json(self):
        with tempfile.TemporaryDirectory() as folder:
            result = dict(RESULT, mode='operation_failure', action='inspect', stage_id='inspect',
                          transaction_status='blocked', error_code='guard_mismatch')
            raw = self.result_with_zip(folder)
            archive = Path(raw['path']).read_bytes()
            calls = []

            def interrupted(request, timeout):
                calls.append(request)
                if len(calls) == 1:
                    return Response(201, dict(status='received'))
                if len(calls) == 2:
                    raise feedback.urllib.error.HTTPError(
                        request.full_url, 503, 'storage', {},
                        io.BytesIO(b'{"error":{"code":"storage_unavailable"}}'))
                return Response(200, dict(status='received'))

            with patch('a6700_unlock.feedback.time.sleep'):
                uploaded = feedback.upload_feedback(result, '测试', state_dir=folder, opener=interrupted,
                                                    failure_archive=archive)
            self.assertEqual(uploaded['status'], 'received')
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[1].full_url, calls[2].full_url)
            self.assertIn(uploaded['feedback_id'] + '/artifact', calls[1].full_url)
            self.assertEqual(calls[1].data, archive)
            self.assertEqual(calls[1].data, calls[2].data)
            self.assertEqual(calls[0].headers['X-delete-token'], calls[2].headers['X-delete-token'])

    def test_paused_artifact_is_reported_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.result_with_zip(folder)
            calls = []

            def paused(request, timeout):
                calls.append(request)
                if request.full_url.endswith('/artifact'):
                    raise feedback.urllib.error.HTTPError(
                        request.full_url, 503, 'paused', {},
                        io.BytesIO(b'{"error":{"code":"feedback_paused"}}'))
                return Response(201, dict(status='received'))

            uploaded = feedback.upload_feedback(result, state_dir=folder, opener=paused, include_artifact=True)
            self.assertEqual(uploaded['status'], 'partial')
            self.assertEqual(uploaded['artifact']['error_code'], 'feedback_paused')
            self.assertEqual(len(calls), 2)

    def test_missing_artifact_endpoint_keeps_json_success_and_local_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.result_with_zip(folder)
            def missing(request, timeout):
                if request.full_url.endswith('/artifact'):
                    raise feedback.urllib.error.HTTPError(request.full_url, 404, 'not found', {}, io.BytesIO(b'{}'))
                return Response(201, dict(status='received', expires_at='2026-12-07T00:00:00Z'))
            uploaded = feedback.upload_feedback(result, state_dir=folder, opener=missing, include_artifact=True)
            self.assertEqual(uploaded['status'], 'partial')
            self.assertEqual(uploaded['artifact']['status'], 'unavailable')
            self.assertTrue(Path(result['path']).exists())

    def test_sent_json_retry_reuses_identity_and_only_retries_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.result_with_zip(folder)
            def first_timeout(request, timeout):
                if request.full_url.endswith('/artifact'):
                    raise TimeoutError('synthetic artifact timeout')
                return Response(201, dict(status='received', expires_at='2026-12-07T00:00:00Z'))
            first = feedback.upload_feedback(result, state_dir=folder, opener=first_timeout, include_artifact=True)
            self.assertEqual(first['status'], 'partial')
            operation_id = result['operation_id']
            sent = Path(folder) / 'feedback-sent' / (operation_id + '.json')
            original = json.loads(sent.read_text(encoding='utf-8'))
            original_id = original['payload']['feedback_id']
            # Reproduce the legacy split-brain state: a later retry created a
            # different pending feedback for the same operation and content.
            duplicate = json.loads(sent.read_text(encoding='utf-8'))
            duplicate['payload']['feedback_id'] = '44444444-4444-4444-8444-444444444444'
            duplicate['delete_token'] = original['delete_token']
            pending = Path(folder) / 'feedback-pending' / (operation_id + '.json')
            pending.write_text(json.dumps(duplicate), encoding='utf-8')
            calls = []
            def success(request, timeout):
                calls.append(request)
                return Response(200, dict(status='received'))
            second = feedback.upload_feedback(result, state_dir=folder, opener=success, include_artifact=True)
            self.assertEqual(second['status'], 'received')
            self.assertEqual(second['feedback_id'], original_id)
            self.assertEqual([request.full_url for request in calls],
                             ['https://powerful6700-api.susukele.com/v1/feedback/' + original_id + '/artifact'])
            self.assertEqual(calls[0].headers['X-delete-token'], original['delete_token'])


class ImageClientTests(unittest.TestCase):
    def result_with_zip(self, folder):
        path = Path(folder) / 'report.zip'
        path.write_bytes(b'PK\x03\x04 synthetic report.json PK\x05\x06')
        return dict(RESULT, path=str(path))

    class Connection:
        def __init__(self, host, timeout):
            self.host = host
            self.timeout = timeout
            self.headers = {}
            self.body = bytearray()
        def putrequest(self, method, path):
            self.method, self.path = method, path
        def putheader(self, name, value):
            self.headers[name] = value
        def endheaders(self):
            pass
        def send(self, chunk):
            self.body.extend(chunk)
        def getresponse(self):
            return Response(201, dict(status='received'))
        def close(self):
            pass

    def test_large_image_is_hashed_and_streamed_in_chunks(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.result_with_zip(folder)
            image = Path(folder) / 'A6700-large.arw'
            image.write_bytes(b'x' * (2 * 1024 * 1024 + 17))
            connections = []
            def factory(host, timeout):
                connection = self.Connection(host, timeout)
                connections.append(connection)
                return connection
            def success(request, timeout):
                return Response(201, dict(status='received', expires_at='2026-12-07T00:00:00Z'))
            uploaded = feedback.upload_feedback(result, state_dir=folder, opener=success,
                                                images=[image], connection_factory=factory)
            self.assertEqual(uploaded['status'], 'received')
            self.assertEqual(len(connections), 1)
            connection = connections[0]
            self.assertEqual(connection.method, 'PUT')
            self.assertIn('/images/', connection.path)
            self.assertEqual(connection.headers['Content-Type'], 'image/x-sony-arw')
            self.assertEqual(connection.headers['Content-Length'], str(image.stat().st_size))
            self.assertEqual(connection.headers['X-Filename'], 'A6700-large.arw')
            self.assertEqual(bytes(connection.body), image.read_bytes())
            self.assertEqual(uploaded['images'][0]['bytes'], image.stat().st_size)

    def test_unsupported_image_is_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.result_with_zip(folder)
            bad = Path(folder) / 'not-an-image.exe'
            bad.write_bytes(b'x')
            def success(request, timeout):
                return Response(201, dict(status='received', expires_at='2026-12-07T00:00:00Z'))
            uploaded = feedback.upload_feedback(result, state_dir=folder, opener=success,
                                                images=[bad], connection_factory=self.Connection)
            self.assertEqual(uploaded['status'], 'partial')
            self.assertEqual(uploaded['images'][0]['status'], 'error')


class GuidedTests(unittest.TestCase):
    def test_unplug_operation_and_reconnect_complete_guided_watch(self):
        class Cancel:
            def is_set(self): return False
            def wait(self, interval): return False
        values = iter([[0x0e76], [], [0x0e76], [0x0e76], [0x0e76], [0x0e76]])
        events = []
        outcome = guided_diagnostics.watch(Cancel(), lambda name, elapsed: events.append(name),
                                           observer=lambda: next(values), interval=0)
        self.assertEqual(outcome, 'reconnected')
        self.assertEqual(events, ['unplug_required', 'user_action_required', 'reconnect_observed'])

    def test_already_unplugged_immediately_allows_camera_operation(self):
        class Cancel:
            def is_set(self): return False
            def wait(self, interval): return False
        values = iter([[], [0x0e76], [0x0e76], [0x0e76], [0x0e76]])
        events = []
        outcome = guided_diagnostics.watch(Cancel(), lambda name, elapsed: events.append(name),
                                           observer=lambda: next(values), interval=0)
        self.assertEqual(outcome, 'reconnected')
        self.assertEqual(events, ['user_action_required', 'reconnect_observed'])

    def test_user_can_stop_before_any_disconnect(self):
        class Cancel:
            def __init__(self): self.calls = 0
            def is_set(self): return self.calls > 0
            def wait(self, interval):
                self.calls += 1
                return True
        cancel = Cancel()
        outcome = guided_diagnostics.watch(cancel, lambda *_: None,
                                           observer=lambda: [0x0e76], interval=0)
        self.assertEqual(outcome, 'cancelled')


if __name__ == '__main__':
    unittest.main()
