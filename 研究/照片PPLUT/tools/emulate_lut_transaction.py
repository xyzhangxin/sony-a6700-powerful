"""Exercise the original LUT sequence constructor and loader in synthetic settings.

The optional two-callsite substitution is a Python harness policy, not an
installable shim. The native constructor, getters, eligibility, PP resolver,
filename copy and loader execute. Locks/base construction and transport/OS/C++
endpoints remain explicit fixtures. Nothing connects to a camera.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm64_const import UC_ARM64_REG_X0

from emulate_lut_lifecycle import (
    AP_SHA256, CP_HANDLE, DATA, VTABLE, Firmware, NativeAP,
)

CTOR = 0x334e1a4
ELIGIBILITY_SITE, RESOLVER_SITE = 0x334e224, 0x334e330
OBJECT = DATA + 0x8000
GETTERS = {
    "eligibility_context": 0x2f6f630, "log": 0x2f75aa0,
    "pp": 0x2f77a54, "direct_selection": 0x2f77a7c,
    "cinematic_mode": 0x2f770cc, "cinematic_look": 0x2f7b974,
    "link0": 0x2f929b8, "link1": 0x2f929e0,
    "link2": 0x2f92a08, "link3": 0x2f92a30,
    "slot_exists": 0x2f928c8, "slot_names": 0x2f928f0,
}
EXTRA_RANGES = ((CTOR, 0x334e3e4), (0x3289ba4, 0x3289c74)) + tuple(
    (a, a + 20) for a in GETTERS.values())
DEFAULTS = {
    "general_mode": 0, "enabled": True, "eligibility_context": 3,
    "log": 0, "pp": 1, "direct_selection": 0,
    "cinematic_mode": 1, "cinematic_look": 1,
    "link0": 17, "link1": 18, "link2": 3, "link3": 4,
    "slot_exists": [1] * 16,
}


class TransactionAP(NativeAP):
    def __init__(self, firmware, policy="original", settings=None, **options):
        if policy not in ("original", "eligibility_only", "resolver_only", "both"):
            raise ValueError("Unknown offline substitution policy")
        self.policy = policy
        self.settings = {**DEFAULTS, **(settings or {})}
        self.transaction_events = []
        self.lock_active = self.watch_settings = False
        self.lock_entries = self.lock_exits = 0
        self.observed_calls = {}
        super().__init__(firmware, **options)
        self.extra_ranges.extend(EXTRA_RANGES)
        self.externals.update({
            0x2e4c81c: self.base_constructor,
            0x34ed634: self.message_token,
            0x3137990: self.lock,
            0x313759c: self.unlock,
            0x176cb20: self.strncpy,
        })
        # Execute each hash-checked getter to derive its address; this also
        # covers the context getter's mov/add shape (not just adrp/add chains).
        self.addresses = {name: self.invoke(a) for name, a in GETTERS.items()}
        for name, address in self.addresses.items():
            if name == "slot_exists":
                values = self.settings[name]
                if len(values) != 16:
                    raise ValueError("Expected exactly 16 slot values")
                self.write(address, struct.pack("<16I", *values))
            elif name == "slot_names":
                names = b"".join(self.slot_name(i).encode("ascii").ljust(64, b"\0") for i in range(16))
                self.write(address, names)
            else:
                self.put(address, "I", self.settings[name])
        self.put(0x4f009f8, "Q", VTABLE)
        self.model_ranges = [(a, 64 if n == "slot_exists" else 1024 if n == "slot_names" else 4)
                             for n, a in self.addresses.items()]
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.settings_write)

    @staticmethod
    def slot_name(index):
        return f"SYNTHETIC_SLOT_{index + 1:02d}.cube"

    def settings_write(self, _uc, _access, address, size, _value, _user):
        if self.watch_settings and any(address < a + n and a < address + size for a, n in self.model_ranges):
            raise AssertionError("Native constructor/policy wrote a model or LUT slot")

    def base_constructor(self):
        if self.x(0) != OBJECT:
            raise AssertionError("Unexpected sequence object")
        self.write(OBJECT, bytes(0x208))
        self.transaction_events.append({"event": "synthetic_base_constructor"})
        self.ret(OBJECT)

    def message_token(self):
        self.transaction_events.append({"event": "synthetic_message_token", "value": 42})
        self.ret(42)

    def lock(self):
        if self.lock_active:
            raise AssertionError("Unexpected nested settings transaction")
        self.lock_active = True
        self.lock_entries += 1
        self.transaction_events.append({"event": "synthetic_settings_lock"})
        self.ret()

    def unlock(self):
        if not self.lock_active:
            raise AssertionError("Unlock without settings transaction")
        self.lock_active = False
        self.lock_exits += 1
        self.transaction_events.append({"event": "synthetic_settings_unlock"})
        self.ret()

    def strncpy(self):
        destination, source, count = self.x(0), self.x(1), self.x(2)
        if count != 64 or destination != OBJECT + 0x1c4:
            raise AssertionError("Unexpected constructor filename copy")
        value = self.read(source, count).split(b"\0", 1)[0]
        self.write(destination, value.ljust(count, b"\0"))
        self.ret(destination)

    def narrow_candidate(self):
        s = self.settings
        selection = s["link0"]
        return (s["enabled"] is True and s["general_mode"] == 0 and s["pp"] == 1 and
                s["log"] == 0 and s["cinematic_mode"] == 1 and s["cinematic_look"] == 1 and
                s["eligibility_context"] in (3, 4) and 3 <= selection <= 18 and
                s["slot_exists"][selection - 3] == 1)

    def _code(self, uc, address, size, user):
        if address in (ELIGIBILITY_SITE, RESOLVER_SITE):
            if not self.lock_active:
                raise AssertionError("Selector called outside constructor's settings lock")
            gate = address == ELIGIBILITY_SITE
            count = 5 if gate else 8
            original = [self.x(i) & 0xffffffff for i in range(count)]
            changed = (self.narrow_candidate() and self.policy in (
                ("eligibility_only", "both") if gate else ("resolver_only", "both")))
            if changed:
                # Change one register immediately before the original BL;
                # never patch the original helper or write a model value.
                uc.reg_write(UC_ARM64_REG_X0 + (2 if gate else 1), 12)
            event = {"event": "native_eligibility_call" if gate else "native_pp_resolver_call",
                     "site": hex(address), "original_arguments": original,
                     "effective_arguments": [self.x(i) & 0xffffffff for i in range(count)],
                     "python_harness_substitution": changed, "settings_lock_active": True}
            self.transaction_events.append(event)
            self.observed_calls[address + 4] = event
        if address in self.observed_calls:
            self.observed_calls.pop(address)["native_return"] = self.x(0) & 0xffffffff
        # Retain the lifecycle ordering without dereferencing LUT buffers or
        # interpreting callback argument zero as a CP application result.
        stages = {
            0x3440d50: "native_send_lut_call", 0x3440bd4: "native_read_lut_call",
            0x3440be4: "native_upload_call", 0x34d22f4: "native_async_submission_call",
            0x34d1ec4: "native_completion_callback_entry", 0x3440d64: "native_descriptor_call",
        }
        if address in stages:
            if self.lock_active:
                raise AssertionError("Loader reentered the constructor's model lock")
            self.transaction_events.append({"event": stages[address],
                "arguments_x0_x3": [self.x(i) for i in range(4)]})
        super()._code(uc, address, size, user)

    def construct(self):
        before = [(a, self.read(a, n)) for a, n in self.model_ranges]
        self.write(OBJECT - 16, b"\xa5" * (0x208 + 32))
        self.watch_settings = True
        self.invoke(CTOR, (OBJECT, DATA + 0x9000))
        if self.lock_active or self.lock_entries != 1 or self.lock_exits != 1 or self.observed_calls:
            raise AssertionError("Constructor failed to complete one settings transaction")
        if any(self.read(a, len(value)) != value for a, value in before):
            raise AssertionError("Settings or slot contents changed")
        if self.read(OBJECT - 16, 16) != b"\xa5" * 16 or self.read(OBJECT + 0x208, 16) != b"\xa5" * 16:
            raise AssertionError("Constructor/policy wrote outside sequence object")
        selected, exists = self.get(OBJECT + 0x1c0), self.get(OBJECT + 0x204)
        name = self.read(OBJECT + 0x1c4, 64).split(b"\0", 1)[0].decode("ascii")
        return {"selection": selected, "exists": exists, "filename": name,
                "token": self.get(OBJECT + 0x1bc), "globals_and_slots_unchanged": True,
                "synthetic_settings_lock_balanced": True, "object_bounds_verified": True,
                "color_apply_ready": False, "events": list(self.transaction_events)}

    def execute_constructed(self):
        selected = self.get(OBJECT + 0x1c0)
        self.slot = selected - 3 if 3 <= selected <= 18 else None
        # Do not call NativeAP.sequence(), which would overwrite object fields.
        result = self.invoke(0x3440d28, (OBJECT,))
        return {"returned": result is not None, "blocked_at_wait": self.blocked,
                "native_callback_invocations": self.callback_count,
                "futex_wait_calls": self.wait_count, "descriptors": self.descriptors,
                "transaction_events": self.transaction_events,
                "synthetic_endpoint_events": self.log,
                "color_apply_ready": False, "still_jpeg_verified": False}


def verify(firmware):
    cases = []
    specs = [
        ("original_photo_pp1_is_rejected_before_resolver", "original", {}, (0, 0)),
        ("resolver_only_never_reached", "resolver_only", {}, (0, 0)),
        ("eligibility_only_resolves_original_direct_selection", "eligibility_only", {}, (0, 0)),
        ("two_local_substitutions_select_pplut1", "both", {}, (17, 1)),
        ("original_video_pplut1", "original", {"pp": 12, "general_mode": 1}, (17, 1)),
        ("enabled_policy_preserves_video_pplut1", "both", {"pp": 12, "general_mode": 1}, (17, 1)),
        ("photo_pp_off_unchanged", "both", {"pp": 0}, (0, 0)),
        ("photo_pp2_unchanged", "both", {"pp": 2}, (0, 0)),
        ("disabled_unchanged", "both", {"enabled": False}, (0, 0)),
        ("empty_slot_unchanged", "both", {"slot_exists": [1] * 14 + [0, 1]}, (0, 0)),
        ("nonboolean_slot_unchanged", "both", {"slot_exists": [1] * 14 + [2, 1]}, (0, 0)),
        ("builtin_binding_unchanged", "both", {"link0": 2}, (0, 0)),
        ("invalid_binding_unchanged", "both", {"link0": 19}, (0, 0)),
        ("log_setting_outside_candidate_keeps_original", "both", {"log": 1}, (0, 0)),
        ("cinematic_mode_outside_candidate_keeps_original", "both", {"cinematic_mode": 0}, (0, 0)),
        ("cinematic_look_outside_candidate_keeps_original", "both", {"cinematic_look": 6}, (0, 0)),
        ("photo_pp13_does_not_redirect_pplut2_to_pplut1", "both", {"pp": 13}, (18, 1)),
        ("context4_original_eligibility_allows_but_pp1_selects_direct", "original", {"eligibility_context": 4}, (0, 0)),
        ("context4_two_local_substitutions", "both", {"eligibility_context": 4}, (17, 1)),
        ("context5_uses_direct_selection_and_bypasses_resolver", "original", {"eligibility_context": 5, "direct_selection": 18}, (18, 1)),
        ("context5_candidate_declines_and_preserves_original", "both", {"eligibility_context": 5, "direct_selection": 18}, (18, 1)),
        ("context5_no_direct_data_is_not_a_pplut1_route", "both", {"eligibility_context": 5}, (0, 0)),
        ("context2_declined", "both", {"eligibility_context": 2}, (0, 0)),
    ]
    specs += [(f"all_user_slots_{selection}", "both", {"link0": selection}, (selection, 1))
              for selection in range(3, 19)]
    for name, policy, settings, expected in specs:
        runner = TransactionAP(firmware, policy=policy, settings=settings)
        result = runner.construct()
        if (result["selection"], result["exists"]) != expected:
            raise AssertionError((name, result, expected))
        expected_name = runner.slot_name(expected[0] - 3) if expected[1] == 1 else ""
        if result["filename"] != expected_name:
            raise AssertionError((name, result))
        if runner.log:
            raise AssertionError("Constructor performed I/O through a lifecycle endpoint")
        calls = [e for e in result["events"] if e["event"].startswith("native_")]
        if name == "resolver_only_never_reached" and len(calls) != 1:
            raise AssertionError("Resolver was unexpectedly reached after original eligibility rejection")
        if settings.get("eligibility_context") == 5 and (len(calls) != 1 or any(e["python_harness_substitution"] for e in calls)):
            raise AssertionError("Context 5 did not retain its separate native path")
        cases.append({"name": name, "policy": policy, "changed_fixture_settings": settings,
                      "result": result, "native_instruction_count": runner.instructions})

    integrations = []
    for name, settings, options, expected_callbacks, blocked in (
        ("identity_slot17_from_constructed_object", {}, {}, 1, False),
        ("swap_slot18_from_constructed_object", {"link0": 18}, {}, 1, False),
        ("read_failure_does_not_establish_readiness", {}, {"read_fail": True}, 0, False),
        ("async_submit_failure_still_sends_descriptor", {}, {"async_return": 1}, 0, False),
        ("callback_missing_blocks_before_descriptor", {}, {"withhold_callback": True}, 0, True),
    ):
        runner = TransactionAP(firmware, policy="both", settings=settings, **options)
        constructed = runner.construct()
        result = runner.execute_constructed()
        if result["native_callback_invocations"] != expected_callbacks or result["blocked_at_wait"] != blocked:
            raise AssertionError((name, result))
        if blocked:
            if result["descriptors"] or result["returned"]:
                raise AssertionError("Missing completion was mistaken for a completed native sequence")
        else:
            descriptor = result["descriptors"][0]
            expected_handle = CP_HANDLE if expected_callbacks else 0xffffffff
            if descriptor["fields"][1] != expected_handle or descriptor["name"] != constructed["filename"]:
                raise AssertionError((name, result))
        integrations.append({"name": name, "constructed": constructed, "result": result})
    return cases, integrations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ap", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output exists")
    firmware = Firmware(args.ap)
    cases, integrations = verify(firmware)
    report = {
        "schema": "a6700-lab/lut-settings-transaction-emulation/1", "result": "passed",
        "ap_sha256": AP_SHA256, "case_count": len(cases), "integration_count": len(integrations),
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "lifecycle_tool_sha256": hashlib.sha256((Path(__file__).parent / "emulate_lut_lifecycle.py").read_bytes()).hexdigest(),
        "cases": cases, "integrations": integrations,
        "additional_native_ranges": [{"start": hex(a), "end_exclusive": hex(b),
            "sha256": hashlib.sha256(firmware.bytes_at(a, b - a)).hexdigest()} for a, b in EXTRA_RANGES],
        "method": "Original constructor/getters/helpers and loader; optional Python register substitution at two exact native BLs",
        "limitations": [
            "Settings, slot availability/names, context and general-mode input are synthetic",
            "Model-lock calls execute as named fixtures; no real lock concurrency or native base-constructor execution",
            "Substitution is a Python harness policy; no compiled live hook, installer or device operation",
            "Context 5 bypasses the PP resolver and is excluded from the two-callsite candidate",
            "The original constructor unlocks before a separate synthetic harness invokes sequence execution",
            "File/OS/C++/interchip endpoints and callback scheduling retain the lifecycle harness's synthetic scope",
            "No successful CP application, retained ownership, photo processing route or JPEG result is established",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Passed {len(cases)} constructor transaction cases and {len(integrations)} constructed-object loader integrations.")


if __name__ == "__main__":
    main()
