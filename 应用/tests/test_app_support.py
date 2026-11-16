"""One-page readiness, failure privacy, and idempotent opt-in usage delivery."""
import json
import io
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

from a6700_unlock.desktop.controller import DesktopController
from a6700_unlock.failure_feedback import operation_failure, FailureTrace, build_failure_archive
from a6700_unlock.feedback import auto_failure_enabled, build_feedback, set_auto_failure_enabled, upload_feedback


class AppSupportTests(unittest.TestCase):
    def test_merged_declaration_requires_both_confirmations_and_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(folder)
            controller.check_environment()
            for notice, prepared in ((False, True), (True, False), (True, None)):
                with self.assertRaises(ValueError):
                    controller.prepare_and_reserve(notice, prepared)
                self.assertFalse(controller.busy)
            controller.prepare_and_reserve(True, True)
            self.assertTrue(controller.busy)
            self.assertEqual(controller.action, 'inspect')
            with self.assertRaises(ValueError):
                controller.prepare_and_reserve(True, True)
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(folder, environment_provider=lambda: dict(ready=False, checks=[], device_access=False))
            controller.check_environment()
            with self.assertRaises(ValueError):
                controller.prepare_and_reserve(True, True)
            self.assertFalse(controller.busy)

    def test_failure_feedback_is_allowlisted_without_connecting(self):
        snapshot = dict(simulation=False, busy=False, operation_id=uuid.uuid4().hex,
                        action='inspect', result=dict(status='error', error='文件写入失败，状态：04000010 ftp://user:secret@192.0.2.1/private'))
        failure = operation_failure(snapshot)
        report = build_feedback(failure)
        self.assertEqual(report['diagnostic']['action'], 'inspect')
        self.assertEqual(report['diagnostic']['events'][0]['step_id'], 'camera_status_04000010')
        for private in ('secret', '192.0.2.1', '/private', 'ftp://'):
            self.assertNotIn(private, json.dumps(report))
        self.assertIsNone(operation_failure(dict(snapshot, simulation=True)))
        self.assertIsNone(operation_failure(dict(snapshot, result=dict(status='cancelled'))))

    def test_failure_trace_never_keeps_raw_progress(self):
        identity = str(uuid.uuid4())
        trace = FailureTrace();trace.begin(identity)
        trace.progress('正在认证相机 USB 服务模式 /Users/private/token')
        trace.progress('正在读取目标 ftp://secret@example')
        trace.progress('正在独立回读 secret')
        report = trace.report(dict(simulation=False, operation_id=identity, action='unlock',
                                   result=dict(status='verification-failed',error='private')))
        self.assertEqual([event['step_id'] for event in report['events'] if event['phase'] != 'environment'],
                         ['authenticate','read_targets','readback','operation_result'])
        self.assertEqual(report['stage_id'],'verify')
        self.assertNotIn('secret',json.dumps(report))
        self.assertNotIn('/Users',json.dumps(report))

    def test_blocked_inspection_reports_compatibility_categories_without_raw_values(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(
                None, mode='physical', physical_state_dir=folder,
                environment_provider=lambda: dict(ready=True, checks=[], device_access=False))
            controller.check_environment()
            controller.prepare_and_reserve(True, True)
            reserved = controller.snapshot()
            issues = [
                dict(kind='sector-guard-mismatch', role='guard', device='/private/location', offset=174735),
                dict(kind='sector-guard-mismatch', role='target'),
                dict(kind='unknown-field', edit_id='private-field', actual='deadbeef'),
                dict(kind='unknown-field', edit_id='repeat'),
                dict(kind='unreviewed', actual='secret'),
            ]
            outcome = dict(status='blocked', device_access=True, write_attempted=False,
                           verified=False, plan=dict(compatible=False, issues=issues))
            trace = FailureTrace()
            with patch('a6700_unlock.failure_feedback.sys.platform', 'win32'), \
                    patch('a6700_unlock.failure_feedback.platform.win32_ver',
                          return_value=('10', '10.0.19045', '', '')):
                trace.begin(reserved['operation_id'], reserved)
            with patch.object(controller.service, 'perform', return_value=outcome):
                state = controller.run_reserved()
            self.assertEqual(state['phase'], 'result')
            self.assertFalse(state['compatible'])
            self.assertFalse(state['write_attempted'])
            self.assertIn('本次未写入', state['result']['detail'])
            diagnostic = build_feedback(trace.report(state))['diagnostic']
            self.assertEqual(diagnostic['stage_id'], 'inspect')
            self.assertEqual(diagnostic['error_code'], 'guard_mismatch')
            self.assertEqual([event['step_id'] for event in diagnostic['events'] if
                              event['step_id'].startswith(('windows_', 'compatibility_'))],
                             ['windows_10.0.19045', 'compatibility_structure',
                              'compatibility_target-content', 'compatibility_unknown-feature-state'])
            for private in ('/private/location', '174735', 'private-field', 'deadbeef', 'secret'):
                self.assertNotIn(private, json.dumps(diagnostic))

    def test_edited_failure_description_retains_separate_pending_identity(self):
        failure=operation_failure(dict(simulation=False,operation_id=str(uuid.uuid4()),
                                      action='inspect',result=dict(status='error')))
        with tempfile.TemporaryDirectory() as folder:
            def offline(*args, **kwargs):raise OSError('synthetic')
            with patch('a6700_unlock.feedback.time.sleep'):
                upload_feedback(failure,'first',state_dir=folder,opener=offline)
                upload_feedback(failure,'edited',state_dir=folder,opener=offline)
            rows=[json.loads(path.read_text()) for path in Path(folder,'feedback-pending').glob('*.json')]
            self.assertEqual(len(rows),2)
            self.assertEqual({row['payload']['description'] for row in rows},{'first','edited'})
            self.assertEqual(len({row['payload']['feedback_id'] for row in rows}),2)

    def test_optional_contact_preserves_existing_feedback_fields(self):
        failure = operation_failure(dict(simulation=False, operation_id=str(uuid.uuid4()),
                                         action='inspect', result=dict(status='error')))
        anon = build_feedback(failure, '连接后报错')
        contact = build_feedback(failure, '连接后报错', email=' test@example.com ')
        self.assertEqual(set(anon), set(contact))
        self.assertEqual(anon['diagnostic'], contact['diagnostic'])
        self.assertEqual(contact['description'], '连接后报错\n\n联系邮箱：test@example.com')
        with self.assertRaises(ValueError):
            build_feedback(failure, email='test@example.com\nprivate')

    def test_failure_archive_is_allowlisted_and_reuses_the_same_feedback_receipt(self):
        identity = str(uuid.uuid4())
        snapshot = dict(operation_id=identity, action='inspect', result=dict(status='blocked'))
        report = operation_failure(snapshot)
        local = dict(run_directory='/private/run', verified=False, write_attempted=False,
                     plan=dict(compatible=False, issues=[
                         dict(kind='unknown-field', edit_id='approved', offset=54321, actual='deadbeef'),
                         dict(kind='unknown-field', edit_id='/unreviewed/value', actual='badvalue'),
                         dict(kind='sector-guard-mismatch', role='target', index=128, device='/private/device'),
                     ]))
        blob = build_failure_archive(report, snapshot, local, {'approved'})
        self.assertEqual(blob, build_failure_archive(report, snapshot, local, {'approved'}))
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(set(archive.namelist()), {'report.json', 'plan.json', 'result.json'})
            issues = json.loads(archive.read('plan.json'))['issues']
            self.assertEqual(issues, [
                dict(kind='unknown-field', check_id='approved'),
                dict(kind='unknown-field'),
                dict(kind='sector-guard-mismatch', role='target', check_index=128),
            ])
            for name in archive.namelist():
                for private in ('/private', '/unreviewed', '54321', 'deadbeef', 'badvalue'):
                    self.assertNotIn(private, archive.read(name).decode())

        requests = []
        class Accepted:
            status = 201
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return b'{"status":"received"}'

        def opener(request, timeout):
            requests.append(request)
            return Accepted()

        with tempfile.TemporaryDirectory() as folder:
            first = upload_feedback(report, state_dir=folder, opener=opener, failure_archive=blob)
            retry = upload_feedback(report, state_dir=folder, opener=opener, failure_archive=blob)
        self.assertEqual(first['status'], 'received')
        self.assertEqual(first['feedback_id'], retry['feedback_id'])
        attachment = '/v1/feedback/' + first['feedback_id'] + '/artifact'
        self.assertEqual([r.full_url.endswith(attachment) for r in requests], [False, True, True])
        self.assertEqual([r.data for r in requests[1:]], [blob, blob])
        self.assertEqual(len({r.get_header('X-delete-token') for r in requests}), 1)

    def test_automatic_failure_preference_preserves_explicit_off(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            self.assertTrue(auto_failure_enabled(state))
            set_auto_failure_enabled(state, False)
            self.assertFalse(auto_failure_enabled(state))
            set_auto_failure_enabled(state, True)
            self.assertTrue(auto_failure_enabled(state))
            (state / 'automatic-failure-feedback.json').write_text('{"enabled":1}', encoding='utf-8')
            self.assertFalse(auto_failure_enabled(state))

    def test_automatic_failure_upload_defaults_on_and_sends_no_email(self):
        from a6700_unlock.desktop import physical_bridge
        for preference in (None, False, True):
            enabled = preference is not False
            with self.subTest(preference=preference), tempfile.TemporaryDirectory() as folder:
                controller = DesktopController(None, mode='physical', physical_state_dir=folder,
                    environment_provider=lambda: dict(ready=True, checks=[], device_access=False))
                controller.check_environment()
                output = io.StringIO()
                uploaded = Event()

                def upload(*args, **kwargs):
                    uploaded.set()
                    return dict(status='received', feedback_id='synthetic')

                def commands():
                    yield json.dumps(dict(command='set_usage', enabled=False))
                    if preference is not None:
                        yield json.dumps(dict(command='set_automatic_failure_feedback', enabled=preference))
                    yield json.dumps(dict(command='start_usage'))
                    yield json.dumps(dict(command='prepare_and_inspect', acknowledged=True, prepared=True))
                    if enabled:
                        self.assertTrue(uploaded.wait(5))
                    else:
                        self.assertFalse(uploaded.wait(0.15))
                    yield json.dumps(dict(command='shutdown'))

                with patch.object(physical_bridge, 'DesktopController', return_value=controller), \
                     patch.object(controller.service, 'perform', return_value=dict(status='blocked', plan=dict(
                         compatible=False, issues=[dict(kind='unknown-field')]))), \
                     patch('a6700_unlock.feedback.recover_diagnostic', return_value=None), \
                     patch('a6700_unlock.diagnostics.load_unfinished_recorder', return_value=None), \
                     patch('a6700_unlock.feedback.upload_feedback', side_effect=upload) as send, \
                     patch('sys.argv', ['test', '--state-dir', folder]), \
                     patch('sys.stdin', commands()), patch('sys.stdout', output):
                    physical_bridge.main()

                self.assertEqual(send.call_count, int(enabled))
                self.assertNotIn('failure-feedback-result', output.getvalue())
                if enabled:
                    self.assertEqual(send.call_args.kwargs['email'], '')
                    with zipfile.ZipFile(io.BytesIO(send.call_args.kwargs['failure_archive'])) as archive:
                        self.assertIn('plan.json', archive.namelist())

    def test_failure_bridge_requires_preview_and_keeps_network_outside_camera(self):
        from a6700_unlock.desktop import physical_bridge
        with tempfile.TemporaryDirectory() as folder:
            controller=DesktopController(None,mode='physical',physical_state_dir=folder)
            controller.action='inspect';controller.phase='result'
            controller.result=dict(status='error',error='状态：04000010 private',success=False)
            identity=str(uuid.UUID(controller.operation_id));output=io.StringIO()
            commands=[dict(command='upload_failure_feedback',operation_id=identity,confirmed=True),
                      dict(command='preview_failure_feedback'),
                      dict(command='upload_failure_feedback',operation_id=str(uuid.uuid4()),confirmed=True),
                      dict(command='upload_failure_feedback',operation_id=identity,confirmed=True,description='synthetic'),
                      dict(command='shutdown')]
            with patch.object(physical_bridge,'DesktopController',return_value=controller), \
                    patch('a6700_unlock.feedback.recover_diagnostic',return_value=None), \
                    patch('a6700_unlock.diagnostics.load_unfinished_recorder',return_value=None), \
                    patch('a6700_unlock.feedback.upload_feedback',return_value=dict(status='received',feedback_id='synthetic')) as upload, \
                    patch.object(controller.service,'perform') as camera, \
                    patch('sys.argv',['test','--state-dir',folder]), \
                    patch('sys.stdin',io.StringIO(''.join(json.dumps(command)+'\n' for command in commands))), \
                    patch('sys.stdout',output):
                physical_bridge.main()
            messages=[json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len([row for row in messages if row['type']=='error']),2)
            self.assertEqual(len([row for row in messages if row['type']=='failure-feedback-preview']),1)
            upload.assert_called_once();camera.assert_not_called()
            with zipfile.ZipFile(io.BytesIO(upload.call_args.kwargs['failure_archive'])) as archive:
                self.assertEqual(set(archive.namelist()), {'report.json', 'result.json'})
            self.assertNotIn('private',json.dumps(upload.call_args.args[0]))


if __name__ == '__main__':
    unittest.main()
