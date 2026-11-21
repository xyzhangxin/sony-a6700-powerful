import json
import unittest

from a6700_unlock.desktop.protocol import API_VERSION, CAPABILITIES, capabilities, decode_request, event


class ProtocolTests(unittest.TestCase):
    def test_versioned_request_and_correlated_event(self):
        request = decode_request(json.dumps(dict(
            command='inspect', api_version=1, request_id='req-123')))
        self.assertEqual(request['command'], 'inspect')
        payload = event('state', request_id=request['request_id'],
                        state=dict(operation_id='operation-1', phase='running'))
        self.assertEqual(payload['api_version'], API_VERSION)
        self.assertEqual(payload['request_id'], 'req-123')
        self.assertEqual(payload['operation_id'], 'operation-1')

    def test_legacy_request_defaults_to_current_version(self):
        request = decode_request('{"command":"environment"}')
        self.assertNotIn('request_id', request)

    def test_duplicate_unknown_and_mismatched_fields_are_rejected(self):
        invalid = [
            '{"command":"environment","command":"inspect"}',
            '{"command":"environment","backend":"usb"}',
            '{"command":"inspect","path":"/dev/nflasha5"}',
            '{"command":"environment","api_version":2}',
            '{"command":"environment","api_version":true}',
            '{"command":"environment","request_id":"bad id"}',
            '[]',
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_request(raw)

    def test_request_size_is_bounded_in_bytes(self):
        with self.assertRaises(ValueError):
            decode_request('{"command":"environment"}' + ' ' * 8192)

    def test_capabilities_do_not_offer_arbitrary_device_access(self):
        self.assertFalse(CAPABILITIES['arbitrary_device_requests'])
        self.assertFalse(CAPABILITIES['signed_packages'])
        self.assertEqual(CAPABILITIES['execution'], 'local-core')
        self.assertNotIn('takeover', capabilities('simulation')['commands'])
        self.assertNotIn('reset', capabilities('physical')['commands'])
        self.assertIn('guided_diagnostic', capabilities('physical')['commands'])
        self.assertIn('execute_audio_import', capabilities('physical')['commands'])
        self.assertNotIn('execute_audio_import', capabilities('simulation')['commands'])
        self.assertIn('refresh_audio_slots', capabilities('physical')['commands'])
        self.assertNotIn('refresh_audio_slots', capabilities('simulation')['commands'])


if __name__ == '__main__':
    unittest.main()
