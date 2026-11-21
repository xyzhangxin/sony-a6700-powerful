import unittest

from a6700_unlock.desktop.aftercare import build_aftercare


def report(status='completed', features=None, final=None, **extra):
    return dict(status=status, verified=True,
                plan=dict(features=features or []), final_features=final or features or [], **extra)


class OperationWorkflowTests(unittest.TestCase):
    def test_fl23_update_has_estimate_readiness_and_pending_acceptance(self):
        row = dict(id='fl23', name='FL2／FL3', state='enabled', update_available=True)
        result = report(features=[row], final=[dict(row, update_available=False)])
        guide = build_aftercare('unlock', result, ['fl23'], False)
        self.assertEqual(guide['workflow']['schema'], 'a6700.workflow/v1')
        wait = next(step for step in guide['workflow']['steps'] if step['id'] == 'restart_wait')
        self.assertEqual(wait['timing'], dict(kind='minimum', seconds=10))
        ready = next(step for step in guide['workflow']['steps'] if step['id'] == 'fl23_ready')
        self.assertEqual(ready['timing']['kind'], 'estimate')
        self.assertEqual(ready['timing']['seconds'], 40)
        self.assertEqual(ready['timing']['completion'], 'prompt_checks_only')
        self.assertEqual(ready['anchor'], 'camera_power_on_confirmed')
        self.assertEqual(guide['evidence']['stages']['transaction']['status'], 'verified')
        self.assertEqual(guide['evidence']['stages']['service_exit']['status'], 'unknown')
        self.assertEqual([row['step_id'] for row in guide['evidence']['acceptance']],
                         ['fl23_menu_acceptance', 'fl23_capture_playback_acceptance'])
        self.assertTrue(all(row['status'] == 'pending' for row in guide['evidence']['acceptance']))
        self.assertTrue(all(row['source'] is None for row in guide['evidence']['acceptance']))

    def test_noop_does_not_restart_or_wait_for_fl23(self):
        row = dict(id='fl23', name='FL2／FL3', state='enabled')
        guide = build_aftercare('unlock', report('no-op', features=[row]), ['fl23'], False)
        self.assertFalse(guide['restart_required'])
        self.assertIsNone(guide['startup_notice'])
        self.assertFalse(any(step['phase'] in ('restart', 'ready') for step in guide['workflow']['steps']))
        self.assertEqual(guide['evidence']['stages']['transaction']['status'], 'no_op')

    def test_custom_sound_or_names_unlock_adds_resident_startup_wait(self):
        for fid, name in (('custom_shutter_sound', '自定义快门声'), ('creative_look_names', '创意外观中文名称')):
            with self.subTest(fid=fid):
                row = dict(id=fid, name=name, state='disabled')
                guide = build_aftercare('unlock', report(features=[row], final=[dict(row, state='enabled')]), [fid], False)
                self.assertIsNotNone(guide['startup_notice'])
                ready = next((step for step in guide['workflow']['steps'] if step['id'] == 'fl23_ready'), None)
                self.assertIsNotNone(ready)
                self.assertEqual(ready['timing']['seconds'], 40)

    def test_close_never_adds_fl23_startup_wait(self):
        row = dict(id='fl23', name='FL2／FL3', state='enabled')
        final = dict(row, state='disabled')
        guide = build_aftercare('close', report(features=[row], final=[final]), ['fl23'], False)
        self.assertIsNone(guide['startup_notice'])
        self.assertFalse(any(step['id'] == 'fl23_ready' for step in guide['workflow']['steps']))

    def test_each_feature_has_independent_manual_acceptance(self):
        rows = [dict(id='ftp', name='FTP', state='disabled'),
                dict(id='sound', name='电子快门声音', state='disabled')]
        final = [dict(row, state='enabled') for row in rows]
        guide = build_aftercare('unlock', report(features=rows, final=final), ['sound', 'ftp'], False)
        self.assertEqual([row['feature_id'] for row in guide['evidence']['acceptance']], ['ftp', 'sound'])
        self.assertTrue(all(row['required'] and row['method'] == 'manual'
                            for row in guide['evidence']['acceptance']))
        self.assertNotEqual(guide['evidence']['acceptance'][0]['status'], 'passed')

    def test_service_exit_is_not_inferred_from_verified_firmware(self):
        row = dict(id='ftp', name='FTP', state='disabled')
        guide = build_aftercare('unlock', report(features=[row], cleanup_status='unconfirmed'), ['ftp'], False)
        self.assertEqual(guide['evidence']['stages']['service_exit']['status'], 'unconfirmed')
        self.assertEqual(guide['evidence']['stages']['transaction']['status'], 'verified')
    def test_close_without_setting_changes_is_unchanged_not_unknown(self):
        row = dict(id='ftp', name='FTP', state='enabled')
        preparation = dict(settings_write_attempted=False, completed=[], settings_verified=True, ready=True)
        guide = build_aftercare('close', report('no-op', features=[row], close_preparation=preparation), ['ftp'], False)
        self.assertEqual(guide['evidence']['stages']['setting_preparation']['status'], 'unchanged')

    def test_verified_setting_preparation_is_separate_from_firmware_noop(self):
        row = dict(id='ftp', name='FTP', state='disabled')
        preparation = dict(settings_write_attempted=True, completed=['ftp'], settings_verified=True, ready=True)
        guide = build_aftercare('close', report('settings-prepared', features=[row], close_preparation=preparation), ['ftp'], False)
        self.assertEqual(guide['evidence']['stages']['setting_preparation']['status'], 'verified')
        self.assertEqual(guide['evidence']['stages']['transaction']['status'], 'no_op')


if __name__ == '__main__':
    unittest.main()
