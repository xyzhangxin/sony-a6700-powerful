import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import uuid

from a6700_unlock.usage import UsageReporter

class UsageTests(unittest.TestCase):
    def test_usage_counts_actual_results_once_after_explicit_enable(self):
        with tempfile.TemporaryDirectory() as folder:
            reporter = UsageReporter(folder)
            reporter.start_delivery = Mock()
            reporter.observe(dict(simulation=False, phase='notice'))
            self.assertFalse(Path(folder, 'usage.json').exists())
            reporter.set_enabled(False)
            reporter.start_session()
            reporter.set_enabled(True)
            reporter.observe(dict(simulation=False, phase='preparation'))
            self.assertEqual(len(reporter.state['queue']), 1)
            operation = dict(simulation=False, phase='result', operation_id=str(uuid.uuid4()),
                             action='unlock', write_attempted=True, result=dict(status='completed', success=True))
            reporter.observe(operation)
            reporter.observe(operation)
            self.assertEqual(sum(event['kind']=='flash_success' for event in reporter.state['queue']), 1)
            reporter.observe(dict(operation, operation_id=str(uuid.uuid4()), result=dict(status='no-op', success=True)))
            reporter.observe(dict(operation, operation_id=str(uuid.uuid4()), result=dict(status='cancelled', success=False)))
            reporter.observe(dict(operation, operation_id=str(uuid.uuid4()), simulation=True))
            self.assertEqual(sum(event['kind']=='flash_success' for event in reporter.state['queue']), 1)
            reporter.observe(dict(operation, operation_id=str(uuid.uuid4()), action='inspect', result=dict(status='error', success=False)))
            self.assertEqual(sum(event['kind']=='operation_error' for event in reporter.state['queue']), 1)
            reporter.set_enabled(False)
            self.assertFalse(reporter.state['queue'])
            reporter.observe(dict(operation, operation_id=str(uuid.uuid4())))
            self.assertFalse(reporter.state['queue'])

    def test_usage_retry_retains_event_and_instance_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def unavailable(request, timeout):
                calls.append(request.data)
                raise OSError('synthetic lost response')
            reporter = UsageReporter(folder, opener=unavailable)
            reporter.start_delivery = Mock()
            reporter.start_session()
            reporter.observe(dict(simulation=False, phase='notice'))
            reporter.flush()
            self.assertEqual(len(reporter.state['queue']), 1)
            class Accepted:
                status = 200
                def __enter__(self):return self
                def __exit__(self, *args):pass
                def read(self, maximum):return json.dumps(dict(event_id=json.loads(calls[-1])['event_id'])).encode()
            def accepted(request, timeout):
                calls.append(request.data)
                return Accepted()
            recovered = UsageReporter(folder, opener=accepted)
            recovered.flush()
            self.assertEqual(calls[0], calls[1])
            self.assertFalse(recovered.state['queue'])
            self.assertEqual(reporter.state['installation_id'], recovered.state['installation_id'])

    def test_default_on_starts_only_in_real_desktop_session(self):
        with tempfile.TemporaryDirectory() as folder:
            transport = Mock(side_effect=AssertionError('No network in this test'))
            first = UsageReporter(folder, opener=transport)
            first.start_delivery = Mock()
            self.assertTrue(first.snapshot()['enabled'])
            first.observe(dict(simulation=False, phase='notice'))
            self.assertFalse(Path(folder, 'usage.json').exists())
            first.start_session()
            first.observe(dict(simulation=True, phase='notice'))
            self.assertEqual(first.snapshot()['queued'], 0)
            first.observe(dict(simulation=False, phase='notice'))
            first.observe(dict(simulation=False, phase='preparation'))
            self.assertTrue(first.start_delivery.called)
            row, = first.state['queue']
            self.assertEqual(row['consent_version'], 'usage-default-on-v1')
            self.assertEqual(row['page'], 'prepare')
            second = UsageReporter(folder, opener=transport)
            second.start_session()
            self.assertEqual(second.state['installation_id'], first.state['installation_id'])
            transport.assert_not_called()

    def test_explicit_disable_is_retained_across_instances_and_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            transport = Mock(side_effect=AssertionError('Disabled statistics must not send'))
            old = UsageReporter(folder, opener=transport)
            old.start_delivery = Mock()
            old.start_session()
            old.observe(dict(simulation=False, phase='notice'))
            UsageReporter(folder).set_enabled(False)
            old.observe(dict(simulation=False, phase='home'))
            old.flush()
            restarted = UsageReporter(folder, opener=transport)
            restarted.start_session()
            restarted.observe(dict(simulation=False, phase='notice'))
            self.assertFalse(restarted.snapshot()['enabled'])
            self.assertFalse(restarted.snapshot()['queued'])
            transport.assert_not_called()
