"""One-page readiness, failure privacy, and idempotent opt-in usage delivery."""
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from a6700_unlock.desktop.controller import DesktopController
from a6700_unlock.failure_feedback import operation_failure, FailureTrace
from a6700_unlock.feedback import build_feedback, upload_feedback


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
            self.assertFalse(upload.call_args.kwargs['include_artifact'])
            self.assertNotIn('private',json.dumps(upload.call_args.args[0]))


if __name__ == '__main__':
    unittest.main()
