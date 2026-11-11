"""Preparation is testable without a camera, USB enumeration, or root access."""
import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from a6700_unlock import preparation
from a6700_unlock.cli import main


ENVIRONMENT = dict(ready=True, checks=[dict(id='python', name='Python', ok=True,
                                         detail='synthetic', remediation='')], device_access=False)


class PreparationTests(unittest.TestCase):
    def test_all_human_steps_cannot_override_failed_environment(self):
        report = preparation.assess(None, preparation.STEP_IDS, dict(ENVIRONMENT, ready=False))
        self.assertFalse(report['ready'])
        self.assertEqual(report['status'], 'environment-blocked')
        self.assertFalse(report['device_access'])
        self.assertFalse(report['compatibility_verified'])

    def test_partial_confirmation_does_not_unlock_preflight(self):
        report = preparation.assess(None, preparation.STEP_IDS[:-1], ENVIRONMENT)
        self.assertEqual(report['status'], 'preparation-required')
        self.assertEqual(sum(not s['confirmed'] for s in report['steps']), 1)

    def test_unknown_confirmation_is_rejected(self):
        with self.assertRaises(ValueError):
            preparation.assess(None, ['skip-firmware-check'], ENVIRONMENT)

    def test_guided_confirmation_is_only_readiness_not_compatibility(self):
        answers = Mock(side_effect=['y'] * len(preparation.STEPS))
        with patch.object(preparation, 'diagnose', return_value=ENVIRONMENT) as diagnosis:
            report = preparation.guide(None, interactive=True, input_fn=answers, emit=lambda _: None)
        diagnosis.assert_called_once()
        self.assertTrue(report['ready'])
        self.assertFalse(report['compatibility_verified'])
        self.assertEqual(report['next_step'], 'device-read-only-preflight')
        self.assertEqual(answers.call_count, len(preparation.STEPS))

    def test_failed_environment_does_not_ask_manual_questions(self):
        answers = Mock()
        with patch.object(preparation, 'diagnose', return_value=dict(ENVIRONMENT, ready=False)):
            report = preparation.guide(None, interactive=True, input_fn=answers, emit=lambda _: None)
        answers.assert_not_called()
        self.assertFalse(report['ready'])

    def test_declined_or_interrupted_preparation_never_proceeds(self):
        for answer in ('n', EOFError(), KeyboardInterrupt()):
            with self.subTest(answer=type(answer).__name__), \
                 patch.object(preparation, 'diagnose', return_value=ENVIRONMENT):
                input_fn = Mock(side_effect=answer) if isinstance(answer, BaseException) else Mock(return_value=answer)
                report = preparation.guide(None, interactive=True, input_fn=input_fn, emit=lambda _: None)
                self.assertTrue(report['cancelled'])
                self.assertFalse(report['ready'])
                self.assertFalse(report['device_access'])

    def test_unprepared_noninteractive_device_command_never_builds_backend(self):
        with patch.object(preparation, 'diagnose', return_value=ENVIRONMENT), \
             patch('sys.stdin.isatty', return_value=False), \
             patch('a6700_unlock.usb.backend.UsbBackend') as backend, \
             patch('a6700_unlock.cli.execute') as execute, \
             contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['device', 'unlock', '--json']), 2)
        backend.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'], 'preparation-required')

    def test_explicit_confirmations_still_require_environment_then_device_workflow(self):
        arguments = ['device', 'inspect', '--json']
        for step in preparation.STEP_IDS:
            arguments += ['--confirm', step]
        for environment, expected_code in ((ENVIRONMENT, 0), (dict(ENVIRONMENT, ready=False), 2)):
            with self.subTest(environment_ready=environment['ready']), \
                 patch.object(preparation, 'diagnose', return_value=environment), \
                 patch('sys.stdin.isatty', return_value=False), \
                 patch('a6700_unlock.usb.backend.UsbBackend') as backend, \
                 patch('a6700_unlock.cli.execute', return_value=dict(status='inspected')) as execute, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(arguments), expected_code)
                self.assertEqual(backend.call_count, int(environment['ready']))
                self.assertEqual(execute.call_count, int(environment['ready']))

    def test_new_assessment_does_not_remember_old_confirmations(self):
        with patch.object(preparation, 'diagnose', return_value=ENVIRONMENT):
            self.assertTrue(preparation.assess(None, preparation.STEP_IDS)['ready'])
            self.assertFalse(preparation.assess(None)['ready'])


if __name__ == '__main__':
    unittest.main()
