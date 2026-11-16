"""Test a photo PPLUT1 entry without substituting the active PP or resolver.

The original AP constructor, qualification and binding resolver execute.
Only one call-local qualification context is changed by the CPU harness.
This is an offline candidate, not a camera patch or proof of menu availability.
"""

import argparse
import hashlib
import json
from pathlib import Path

from unicorn.arm64_const import UC_ARM64_REG_X0
from emulate_lut_lifecycle import AP_SHA256, Firmware
from emulate_lut_transaction import TransactionAP, ELIGIBILITY_SITE
from emulate_lut_parameter_helpers import ap_case
from probe_lut_unlock_conditions import output_gate


class NativePPLUTEntry(TransactionAP):
    def __init__(self, firmware, settings=None, active=True, **options):
        self.entry_active = active
        self.qualifier_changes = []
        super().__init__(firmware, policy='original', settings={
            'general_mode': 0, 'eligibility_context': 2, 'pp': 12,
            **(settings or {})}, **options)

    def _code(self, uc, address, size, user):
        original = None
        if address == ELIGIBILITY_SITE:
            if not self.lock_active:
                raise AssertionError('Qualification outside native model transaction')
            original = [self.x(n) & 0xffffffff for n in range(5)]
            s = self.settings
            binding = s['link0']
            expected = [s[k] for k in ('eligibility_context', 'log', 'pp',
                                       'cinematic_mode', 'cinematic_look')]
            if original != expected:
                raise AssertionError('Call arguments differ from snapshot')
            candidate = (self.entry_active and s['enabled'] is True and
                s['general_mode'] == 0 and s['eligibility_context'] == 2 and
                s['pp'] == 12 and s['log'] == 0 and s['cinematic_mode'] == 1 and
                s['cinematic_look'] == 1 and 3 <= binding <= 18 and
                s['slot_exists'][binding - 3] == 1)
            if candidate:
                uc.reg_write(UC_ARM64_REG_X0, 3)
                self.qualifier_changes.append(dict(site=hex(address),
                    original=original, effective=[3] + original[1:]))
        super()._code(uc, address, size, user)
        if original is not None:
            event = self.observed_calls[address + 4]
            event['original_arguments'] = original
            event['python_harness_substitution'] = original != event['effective_arguments']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('ap', type=Path)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        p.error('Output exists')
    firmware = Firmware(args.ap)
    cases = []
    for binding in range(3, 19):
        runner = NativePPLUTEntry(firmware, settings={'link0': binding, 'direct_selection': 6})
        result = runner.construct()
        calls = [e for e in result['events'] if e['event'].startswith('native_')]
        assert (result['selection'], result['exists']) == (binding, 1)
        assert len(runner.qualifier_changes) == 1
        resolver = next(e for e in calls if e['event'] == 'native_pp_resolver_call')
        assert resolver['original_arguments'] == resolver['effective_arguments']
        assert resolver['original_arguments'][1] == 12
        cases.append(dict(name=f'photo_native_pplut1_user_slot_{binding}', result=result,
                          qualifier_changes=runner.qualifier_changes))
    for name, settings in (
            ('pp_off', {'pp': 0}), ('ordinary_pp1', {'pp': 1}),
            ('other_pplut', {'pp': 13}), ('video_pplut1', {'general_mode': 1, 'eligibility_context': 3}),
            ('other_context', {'eligibility_context': 5}), ('log_mode', {'log': 1}),
            ('builtin_binding', {'link0': 2}), ('missing_user_slot', {'slot_exists': [0] * 16}),
            ('disabled', {'enabled': False})):
        native = NativePPLUTEntry(firmware, settings=settings, active=False).construct()
        runner = NativePPLUTEntry(firmware, settings=settings)
        result = runner.construct()
        assert not runner.qualifier_changes
        assert result == native
        cases.append(dict(name=name, result=result, unchanged_from_native=True))
    # Data loading still uses the original file/transfer chain, with its existing
    # named OS/resource/transport fixtures and explicit missing-callback behavior.
    integrations = []
    for name, options, callbacks, blocked in (
            ('load_identity', {}, 1, False),
            ('file_read_failure', {'read_fail': True}, 0, False),
            ('transfer_rejected', {'async_return': 1}, 0, False),
            ('callback_missing', {'withhold_callback': True}, 0, True)):
        runner = NativePPLUTEntry(firmware, **options)
        constructed = runner.construct()
        loaded = runner.execute_constructed()
        assert loaded['native_callback_invocations'] == callbacks
        assert loaded['blocked_at_wait'] == blocked
        assert not loaded['color_apply_ready'] and not loaded['still_jpeg_verified']
        integrations.append(dict(name=name, constructed=constructed, loaded=loaded))
    # Opening the entry alone leaves BOTH original photo message gates closed.
    base = ap_case(firmware.data, [17, 1, 0, 0, 0, 12, 0, 1])
    outputs = output_gate(firmware.data, 0)
    assert base['fields']['base_look'] == 11 and base['fields']['shooting_mode'] == 3
    assert outputs['output_bytes_8_to_12'] == [0] * 5
    report = dict(schema='a6700-lab/photo-native-pplut-entry-candidate/1', result='passed',
        camera_access=False, ap_sha256=AP_SHA256,
        tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        cases=cases, integrations=integrations,
        native_photo_base_message=base, native_photo_output_message=outputs,
        entry_preference='Original PPLUT1 in photo; PP1 remains compatibility option',
        findings=[
            'If photo active PP is already 12, only the constructor qualification context needs local 2-to-3 adaptation for these fixtures',
            'Original resolver uses the unchanged PP=12 and saved PPLUT1 binding; no PP1-to-PP12 resolver substitution is needed',
            'The constructor keeps its settings lock and leaves all model values and bindings unchanged',
            'Entry admission alone still emits base_look=11/shooting_mode=3 and all-zero output flags in photo mode'],
        limitations=[
            'PP=12 in photo is a synthetic fixture; physical menu selection and mode-switch persistence remain unverified',
            'No menu-availability patch or installable adapter has been produced',
            'OS, files, transfers, callbacks and settings-lock endpoints are fixtures',
            'No CP MLUT readiness, preview pixels or final JPEG burn-in is proved',
            'This qualification adapter does not bypass photo exposure, AF, sensor readout or JPEG scheduling'])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f'{len(cases)} entry cases + {len(integrations)} loader integrations passed; original photo color gates remain closed.')


if __name__ == '__main__':
    main()
