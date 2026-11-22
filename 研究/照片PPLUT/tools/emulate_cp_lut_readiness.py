"""Execute original CP MLUT state/pending/generation logic with named fixtures.

MLUT driver APIs and logging are synthetic endpoints. This verifies the control
state, not hardware readiness or the full-resolution photo/JPEG processing route.
The StartVfx test executes only its readiness/output-gating basic blocks.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_LITTLE_ENDIAN,
                     UC_HOOK_CODE, UC_PROT_READ, UC_PROT_EXEC)
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R7, UC_ARM_REG_R8,
                              UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)
from emulate_lut_parameter_helpers import CP_BASE, CP_SHA256, check_hash

STACK, DATA, RETURN = 0x8000000, 0x9000000, 0xa000000
PARAM, OUTPUTS = DATA, DATA + 0x1000
STATE, LOOK, GENERATION, WAIT_CALLBACK = 0x19b69f8, 0x19b69fc, 0x19b6a0c, 0x1a67a1b
SETTER, CALLBACK, MATCH = 0x4dc7c8, 0x4dca4c, 0xb15cd8
START_SLICE, END_SLICE = 0x4dbf62, 0x4dbff6
RANGES = ((SETTER, 0x4dc9cc), (CALLBACK, 0x4dca9c),
          (MATCH, 0xb15d30), (START_SLICE, END_SLICE))


class CPReadiness:
    def __init__(self, firmware, **options):
        self.options, self.events = options, []
        self.instructions = 0
        self.pending = None
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_LITTLE_ENDIAN)
        self.uc.mem_map(CP_BASE, (len(firmware) + 4095) & ~4095)
        self.uc.mem_write(CP_BASE, firmware)
        self.uc.mem_protect(CP_BASE, (len(firmware) + 4095) & ~4095, UC_PROT_READ | UC_PROT_EXEC)
        for address, size in ((STACK, 0x10000), (DATA, 0x2000), (RETURN, 0x1000),
                              (0x199e000, 0x1000), (0x19b6000, 0x1000),
                              (0x1a67000, 0x1000)):
            self.uc.mem_map(address, size)
        self.uc.hook_add(UC_HOOK_CODE, self.guard)
        self.put(PARAM, 11)  # Synthetic acquire ID from the known parameter case.
        self.put(PARAM + 8, 10)  # User3D category; separate namespace from acquire ID.
        self.put(PARAM + 0x10, 100)
        self.put(PARAM + 0x14, 100)
        self.uc.mem_write(PARAM + 0x18, struct.pack("<QQII", 0x12340000, 0x12350000, 48000, 124))
        self.put(PARAM + 0x70, 7)
        self.put(LOOK, 0)
        self.put(STATE, 0)
        self.put(GENERATION, 0)
        self.put(WAIT_CALLBACK, 0, "B")

    def put(self, address, value, fmt="I"):
        self.uc.mem_write(address, struct.pack("<" + fmt, value))

    def get(self, address, fmt="I"):
        return struct.unpack("<" + fmt, bytes(self.uc.mem_read(address, struct.calcsize("<" + fmt))))[0]

    def r(self, index):
        return self.uc.reg_read(UC_ARM_REG_R0 + index)

    def ret(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def guard(self, _uc, address, _size, _user):
        self.instructions += 1
        if self.instructions > 20000:
            raise RuntimeError("Aggregate CP instruction budget exceeded")
        if address == 0xb96862:
            self.events.append({"event": "synthetic_mlut_pre", "return": self.options.get("pre_return", 0)})
            self.ret(self.options.get("pre_return", 0))
        elif address in (0x542108, 0x542344):
            callback = self.r(2 if address == 0x542108 else 3)
            if callback != CALLBACK | 1:
                raise AssertionError("Unexpected native MLUT callback target")
            if self.pending:
                raise AssertionError("New synthetic request while MLUT callback is outstanding")
            rc = self.options.get("submit_return", 0)
            event = {"event": "synthetic_mlut_user3d_submit", "target": hex(address),
                     "callback": hex(callback), "return": rc, "native_state_at_submission": self.state()}
            self.events.append(event)
            if rc == 0:
                self.pending = callback
            self.ret(rc)
        elif address == 0xb96c88:
            self.events.append({"event": "synthetic_ei_gain", "ei": self.r(0), "base_iso": self.r(1)})
            self.ret()
        elif address == 0xb287b4:
            self.events.append({"event": "synthetic_logger"})
            self.ret()
        elif address == 0xb96d66:
            self.events.append({"event": "synthetic_mlut_all_outputs_off_transition"})
            self.ret()
        elif not any(a <= address < b for a, b in RANGES):
            raise RuntimeError(f"Unexpected CP execution: {address:#x}")

    def execute(self, address, args=(), end=RETURN):
        for index, value in enumerate(args):
            self.uc.reg_write(UC_ARM_REG_R0 + index, value)
        self.uc.reg_write(UC_ARM_REG_SP, STACK + 0xf000)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        self.uc.emu_start(address | 1, end, timeout=100000, count=3000)
        if self.uc.reg_read(UC_ARM_REG_PC) != end:
            raise RuntimeError("CP did not reach the bounded return/cutpoint")
        return self.r(0)

    def state(self):
        return {"state": self.get(STATE), "acquire_id": self.get(LOOK),
                "generation": self.get(GENERATION), "wait_callback": self.get(WAIT_CALLBACK, "B")}

    def match(self):
        # This answers the native reuse predicate, not whether image pixels
        # have passed through User3D. In particular acquire_id=0 returns true.
        return self.execute(MATCH, (self.get(PARAM), self.get(PARAM + 8), PARAM + 0x18))

    def set_parameters(self):
        before = bytes(self.uc.mem_read(PARAM, 0x78))
        self.execute(SETTER, (42, PARAM))  # Synthetic nonzero VFX handle.
        if bytes(self.uc.mem_read(PARAM, len(before))) != before:
            raise AssertionError("Original CP setter modified its parameter input")
        return self.state()

    def callback(self, status):
        if not self.pending:
            raise AssertionError("No synthetic MLUT request to complete")
        callback, self.pending = self.pending, None
        self.events.append({"event": "synthetic_callback_dispatch", "status_argument": status})
        self.execute(callback & ~1, (status,))
        return self.state()

    def gate_outputs(self, values=(0, 1, 1, 1)):
        packed = struct.pack("<4I", *values)
        self.uc.mem_write(OUTPUTS - 16, b"\xa5" * 48)
        self.uc.mem_write(OUTPUTS, packed)
        self.uc.reg_write(UC_ARM_REG_R8, OUTPUTS)
        self.uc.reg_write(UC_ARM_REG_R7, STACK + 0xf080)
        self.execute(START_SLICE, end=END_SLICE)
        if bytes(self.uc.mem_read(OUTPUTS - 16, 16)) != b"\xa5" * 16 or bytes(self.uc.mem_read(OUTPUTS + 16, 16)) != b"\xa5" * 16:
            raise AssertionError("Readiness slice wrote outside the four output words")
        return list(struct.unpack("<4I", bytes(self.uc.mem_read(OUTPUTS, 16))))


def verify(cp):
    cases = []
    # Isolate the native state/reuse predicate using synthetic data and state.
    for name, changes, expected in (
        ("matching_user_generation", {}, 1),
        ("state_zero", {STATE: 0}, 0),
        ("state_prepared_only", {STATE: 1}, 0),
        ("different_acquire_id", {LOOK: 12}, 0),
        ("callback_pending", {WAIT_CALLBACK: 1}, 0),
        ("different_lut_generation", {GENERATION: 8}, 0),
        ("no_acquire_required_returns_true_without_data_proof", {PARAM: 0, STATE: 0}, 1),
        ("zero_user_bytes_skips_generation_check", {PARAM + 0x28: 0, GENERATION: 8}, 1),
        ("non_user_category_skips_generation_check", {PARAM + 8: 0, GENERATION: 8}, 1),
    ):
        runner = CPReadiness(cp)
        for a, value in {STATE: 2, LOOK: 11, GENERATION: 7, **changes}.items():
            runner.put(a, value, "B" if a == WAIT_CALLBACK else "I")
        result = runner.match()
        if result != expected:
            raise AssertionError((name, result, expected))
        cases.append({"name": name, "state": runner.state(), "native_reuse_predicate": result,
                      "changes": {hex(a): v for a, v in changes.items()}})

    for name, state, pending, outputs in (
        ("start_before_preparation", 0, 0, [0, 0, 0, 0]),
        ("start_after_preparation_only", 1, 0, [0, 0, 0, 0]),
        ("start_waiting_for_mlut_callback", 2, 1, [0, 0, 0, 0]),
        ("start_after_successful_callback_keeps_output_policy", 2, 0, [0, 1, 1, 1]),
    ):
        runner = CPReadiness(cp)
        runner.put(STATE, state)
        runner.put(WAIT_CALLBACK, pending, "B")
        result = runner.gate_outputs()
        if result != outputs:
            raise AssertionError((name, result, outputs))
        cases.append({"name": name, "state": runner.state(), "input_outputs": [0, 1, 1, 1],
                      "outputs_at_start_cutpoint": result, "full_start_function_executed": False})

    # The real setter uses a pre-stage and a later asynchronous User3D stage.
    for name, options, callback_status, expected_final in (
        ("artgen_success_requires_its_own_callback", {}, 0, (2, 0, 11)),
        ("user3d_without_register_data", {"register_bytes": 0}, 0, (2, 0, 11)),
        ("artgen_submit_error", {"submit_return": 8}, None, (0, 0, 0)),
        ("artgen_callback_error", {}, 8, (0, 0, 0)),
        ("artgen_missing_callback_keeps_outputs_disabled", {}, None, (2, 1, 11)),
    ):
        runner = CPReadiness(cp, **options)
        if "register_bytes" in options:
            runner.put(PARAM + 0x2c, options["register_bytes"])
        first = runner.set_parameters()
        if first["state"] != 1 or first["wait_callback"] or runner.pending:
            raise AssertionError((name, "pre-stage", first))
        second = runner.set_parameters()
        if options.get("submit_return", 0) == 0 and (second["state"] != 2 or second["wait_callback"] != 1):
            raise AssertionError((name, "submit-stage", second))
        if callback_status is not None:
            runner.callback(callback_status)
        final = runner.state()
        if (final["state"], final["wait_callback"], final["acquire_id"]) != expected_final:
            raise AssertionError((name, final))
        predicate, outputs = runner.match(), runner.gate_outputs()
        success = callback_status == 0
        if predicate != int(success) or outputs != ([0, 1, 1, 1] if success else [0, 0, 0, 0]):
            raise AssertionError((name, predicate, outputs))
        cases.append({"name": name, "prepared": first, "submitted": second,
                      "final": final, "native_reuse_predicate": predicate,
                      "outputs_at_start_cutpoint": outputs, "events": runner.events})

    runner = CPReadiness(cp, pre_return=8)
    result = runner.set_parameters()
    if result["state"] != 0 or runner.pending or runner.gate_outputs() != [0] * 4:
        raise AssertionError("Failed preparation was treated as ready")
    cases.append({"name": "mlut_preparation_error", "final": result, "events": runner.events})

    runner = CPReadiness(cp)
    runner.set_parameters()
    runner.set_parameters()
    runner.callback(0)
    count_before = sum(e["event"] == "synthetic_mlut_user3d_submit" for e in runner.events)
    runner.set_parameters()
    if sum(e["event"] == "synthetic_mlut_user3d_submit" for e in runner.events) != count_before:
        raise AssertionError("Unchanged generation unexpectedly resubmitted")
    runner.put(PARAM + 0x70, 8)
    if runner.match() != 0:
        raise AssertionError("Old MLUT generation accepted as current")
    first, second = runner.set_parameters(), runner.set_parameters()
    if first["state"] != 1 or second["generation"] != 8 or second["wait_callback"] != 1:
        raise AssertionError("Changed generation did not prepare/reload")
    runner.callback(0)
    if runner.match() != 1:
        raise AssertionError("New completed generation did not match")
    cases.append({"name": "same_generation_reuses_but_new_generation_prepares_and_reloads",
                  "final": runner.state(), "events": runner.events})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cp", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output exists")
    cp = args.cp.read_bytes()
    check_hash(cp, CP_SHA256)
    cases = verify(cp)
    report = {
        "schema": "a6700-lab/cp-lut-readiness-emulation/1", "result": "passed",
        "cp_sha256": CP_SHA256, "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "case_count": len(cases), "cases": cases,
        "native_ranges": [{"start": hex(a), "end_exclusive": hex(b),
            "sha256": hashlib.sha256(cp[a - CP_BASE:b - CP_BASE]).hexdigest()} for a, b in RANGES],
        "limitations": [
            "Inputs and CP state are synthetic, not camera RAM snapshots",
            "Original CP setter, reuse predicate and MLUT callback execute; MLUT driver APIs and logger are named fixtures",
            "The StartVfx test stops at 0x4dbff6, before the remaining transfer setup or hardware execution",
            "AP interchip completion and CP MLUT completion are distinct callbacks; neither alone proves JPEG pixels",
            "A true native reuse predicate can also mean no acquire ID or no user data; it is not a standalone readiness grant",
            "No real MLUT, DMA, full-resolution photo routing, JPEG encoding or camera operation",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Passed {len(cases)} CP state/callback/generation scenarios. Hardware and JPEG remain unverified.")


if __name__ == "__main__":
    main()
