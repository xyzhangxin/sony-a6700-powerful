"""Probe synthetic LUT message changes and the unmodified AP output gate.

Offline CPU emulation only. No patched firmware is saved and no camera is
accessed. Nonzero resource IDs do not prove hardware or JPEG processing.
"""

import argparse
import io
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_PC

from emulate_lut_parameter_helpers import AP_SHA256, CP_SHA256, STACK, STACK_TOP, MESSAGE, RETURN, check_hash, cp_case, run_bounded


def output_gate(ap, general_mode):
    elf = ELFFile(io.BytesIO(ap))
    segment = next(segment for segment in elf.iter_segments()
                   if segment["p_type"] == "PT_LOAD" and segment["p_vaddr"] <= 0x34e9000
                   and 0x34ea000 <= segment["p_vaddr"] + segment["p_filesz"])
    offset = 0x34e9000 - segment["p_vaddr"] + segment["p_offset"]
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    machine.mem_map(0x34e9000, 0x1000)
    machine.mem_write(0x34e9000, ap[offset:offset + 0x1000])
    for address, size in ((STACK, 0x10000), (MESSAGE, 0x1000), (RETURN, 0x1000)):
        machine.mem_map(address, size)
    arguments = [12, 0, 1, 0, general_mode, 1]
    registers = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                 UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6)
    for register, value in zip(registers, arguments + [MESSAGE]):
        machine.reg_write(register, value)
    machine.reg_write(UC_ARM64_REG_SP, STACK_TOP)
    machine.reg_write(UC_ARM64_REG_LR, RETURN)
    instructions = run_bounded(machine, 0x34e934c, UC_ARM64_REG_PC, 0x34e934c, 0x34e93b0)
    return {"arguments_w0_to_w5": arguments, "output_bytes_8_to_12": list(machine.mem_read(MESSAGE + 8, 5)),
            "instruction_count": instructions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extracted_root", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("Output exists")
    ap = (args.extracted_root / "files/nflasha15/lib/appFw.so").read_bytes()
    cp = (args.extracted_root / "files/nflasha8/cpapp-b.bin").read_bytes()
    check_hash(ap, AP_SHA256)
    check_hash(cp, CP_SHA256)
    cases = []
    for name, base, mode, size in (("unchanged", 11, 3, 0xbc00),
                                   ("base_look_only", 10, 3, 0xbc00),
                                   ("message_mode_only", 11, 0, 0xbc00),
                                   ("both_message_fields", 10, 0, 0xbc00),
                                   ("both_but_missing_data", 10, 0, 0)):
        normalized = cp_case(cp, 0xb15d30, (base, mode, size))["return_r0"]
        resource = cp_case(cp, 0x4dcab8, (mode, normalized, 1, 0), (0, 0))["return_r0"]
        cases.append({"name": name, "base_look": base, "message_shooting_mode": mode,
                      "lut_bytes": size, "normalized_base_look": normalized, "acquire_id": resource})
    if [(case["normalized_base_look"], case["acquire_id"]) for case in cases] != [(11, 0), (11, 0), (11, 0), (10, 11), (0, 1)]:
        raise AssertionError("Unexpected CP parameter decisions")
    output_cases = [output_gate(ap, mode) for mode in (0, 1)]
    if [case["output_bytes_8_to_12"] for case in output_cases] != [[0] * 5, [1] * 5]:
        raise AssertionError("Unexpected AP output gating")
    report = {"schema": "a6700-lab/native-lut-unlock-conditions/1", "ap_sha256": AP_SHA256, "cp_sha256": CP_SHA256,
              "method": "Unmodified helper machine code; synthetic message inputs; no camera access or binary patch",
              "message_field_cases": cases, "ap_output_gate_cases": output_cases,
              "limitations": ["No firmware state-machine, ISP, DMA or JPEG execution",
                              "Output bytes are not yet mapped to full-resolution still-JPEG routing",
                              "Empty data changes the normalized category to 0; a nonzero resource ID does not imply User3D"]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for case in cases:
        print(case["name"], "base", case["base_look"], "mode", case["message_shooting_mode"],
              "normalized", case["normalized_base_look"], "resource", case["acquire_id"])
    for mode, case in enumerate(output_cases):
        print("general mode", mode, "LUT output bytes", case["output_bytes_8_to_12"])


if __name__ == "__main__":
    main()
