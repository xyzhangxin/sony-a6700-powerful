"""Package option transitions and app selection, without a camera or firmware."""
import itertools
import tempfile
import unittest
from unittest.mock import Mock

from a6700_unlock.catalog import load_catalog
from a6700_unlock.creative_looks import (FEATURE_IDS, LookOptions, feature_definitions,
                                        pending_rows, plan_options)
from a6700_unlock.desktop.controller import DesktopController
from a6700_unlock.desktop.physical import PhysicalService


class LookOptionTests(unittest.TestCase):
    def test_all_combinations_and_partial_selection_preserve_the_other_option(self):
        for flags, feature, action in itertools.product(range(4), FEATURE_IDS, ('unlock', 'close')):
            with self.subTest(flags=flags, feature=feature, action=action):
                before = LookOptions.from_flags(flags)
                result = plan_options(before, [feature], action, main_selection=0)
                after = LookOptions.from_flags(result['after_flags'])
                self.assertEqual(getattr(after, feature), action == 'unlock')
                other = next(key for key in FEATURE_IDS if key != feature)
                self.assertEqual(getattr(after, other), getattr(before, other))
                self.assertEqual(result['cp_action'] == 'preserve', before.fl23 == after.fl23)
                self.assertEqual(result['shared_loader_required'], bool(after.flags))
                self.assertFalse(result['executable'])

    def test_names_only_does_not_install_or_preflight_cp(self):
        plan = plan_options(LookOptions(), ['creative_look_names'], 'unlock', main_selection=10)
        self.assertEqual(plan['add_components'], ['localized-look-names', 'startup-loader'])
        self.assertEqual(plan['cp_action'], 'preserve')
        self.assertFalse(plan['cp_preflight_required'])
        self.assertEqual(plan['main_selection_after'], 10)

    def test_turning_names_off_preserves_fl23_icons_and_full_help(self):
        plan = plan_options(LookOptions(True, True), ['creative_look_names'], 'close', main_selection=17)
        self.assertEqual(plan['remove_components'], ['localized-look-names'])
        self.assertEqual(plan['after_flags'], 1)
        self.assertEqual(plan['fl23_help'], 'full-native')
        self.assertEqual(plan['main_selection_after'], 17)

    def test_close_fl23_normalizes_selection_but_keeps_names_and_loader(self):
        for main in (17, 18):
            plan = plan_options(LookOptions(True, True), ['fl23'], 'close', main_selection=main)
            self.assertEqual(plan['main_selection_after'], 0)
            self.assertEqual(plan['after_flags'], 2)
            self.assertEqual(plan['cp_action'], 'restore')
            self.assertNotIn('startup-loader', plan['remove_components'])
            self.assertNotIn('localized-look-names', plan['remove_components'])

    def test_missing_or_inconsistent_state_cannot_become_a_plan(self):
        for flags in (True, -1, 4, None):
            with self.assertRaises(ValueError): LookOptions.from_flags(flags)
        for current in (None, {'fl23': True}):
            with self.assertRaises(ValueError): plan_options(current, ['fl23'], 'unlock', main_selection=0)
        for selection in (16, 19, True, None):
            with self.assertRaises(ValueError): plan_options(LookOptions(), ['fl23'], 'unlock', main_selection=selection)
        for selected in ([], ['fl23', 'fl23'], ['ftp']):
            with self.assertRaises(ValueError): plan_options(LookOptions(), selected, 'unlock', main_selection=0)
        with self.assertRaises(ValueError): plan_options(LookOptions(), ['fl23'], 'unlock', main_selection=17)

    def test_physical_options_are_unknown_not_inferred_from_sparse_catalog(self):
        catalog = load_catalog()
        before = catalog.digest
        definitions = feature_definitions(catalog)
        self.assertEqual(catalog.digest, before)
        for row in definitions:
            if row['id'] in FEATURE_IDS:
                self.assertFalse(row['available'])
                self.assertEqual(row['requires'], [])
        self.assertTrue(all(row['state'] == 'unknown' for row in pending_rows()))
        factory = Mock(side_effect=AssertionError('must not open USB'))
        service = PhysicalService(backend_factory=factory)
        for action, feature in itertools.product(('unlock', 'close', 'restore'), FEATURE_IDS):
            with self.assertRaisesRegex(ValueError, '暂未开放'):
                service.perform(action, features=[feature])
        factory.assert_not_called()

    def test_desktop_ships_only_fl23_and_keeps_deferred_name_option_hidden(self):
        environment = dict(ready=True, checks=[], device_access=False)
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(folder, environment_provider=lambda: environment)
            controller.reset('original')
            controller.check_environment(); controller.acknowledge_notice(True)
            controller.begin_preparation(); controller.confirm_preparation(True)
            controller.operate('inspect')
            self.assertNotIn('creative_look_names',{r['id'] for r in controller.snapshot()['features']})
            with self.assertRaises(ValueError):controller.set_feature_selection('creative_look_names',True)
            for feature, action, expected in (
                    ('fl23', 'unlock', {'fl23'}),
                    ('fl23', 'close', set())):
                controller.select_features('none')
                controller.set_feature_selection(feature, True)
                state = controller.operate(action)
                self.assertEqual(state['result']['status'], 'completed')
                self.assertEqual({row['id'] for row in state['features'] if row['state'] == 'enabled'}, expected)
                self.assertFalse(state['device_access']); self.assertFalse(state['write_attempted'])
                controller.restart_preparation(); controller.confirm_preparation(True)
                controller.operate('inspect')


if __name__ == '__main__':
    unittest.main()
