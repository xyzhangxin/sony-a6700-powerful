"""Run bounded, unmodified AP/CP parameter helpers in Unicorn, without a camera.

This emulates parameter decisions only. It does not emulate an ISP, image data,
LUT hardware, JPEG encoding, or the asynchronous firmware scheduler.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_ARCH_ARM, UC_MODE_ARM, UC_MODE_THUMB, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7, UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_PC
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC


AP_SHA256 = "e65fd595052be5d0e98ce8000dfcb94f90a2f8309de70ef140375052009b0ef3"
CP_SHA256 = "61b7fe5c0ce227ea865d799b81fe77ae359b7c5dcd39d13c9c2241d9cad23559"
CP_BASE = 0x108000
STACK = 0x8000000
STACK_TOP = STACK + 0xf000
MESSAGE = 0x9000000
RETURN = 0xa000000


def check_hash(data, expected):
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"Firmware hash mismatch: {actual}")


def run_bounded(machine, start, pc_register, allowed_start, allowed_end):
    # Reject calls outside the selected helper instead of stubbing firmware APIs.
    count = [0]

    def guard(_machine, address, _size, _user):
        if not allowed_start <= address < allowed_end:
            raise RuntimeError(f"Unexpected execution at 0x{address:x}")
        count[0] += 1

    hook = machine.hook_add(UC_HOOK_CODE, guard)
    try:
        machine.emu_start(start, RETURN, timeout=100_000, count=2_000)
        if machine.reg_read(pc_register) != RETURN:
            raise RuntimeError("Helper did not return within its execution budget")
    finally:
        machine.hook_del(hook)
    return count[0]


def ap_case(data, arguments):
    elf = ELFFile(io.BytesIO(data))
    loads = [segment for segment in elf.iter_segments() if segment["p_type"] == "PT_LOAD"]

    def read_va(address, size):
        segment = next(segment for segment in loads if segment["p_vaddr"] <= address and address + size <= segment["p_vaddr"] + segment["p_filesz"])
        offset = address - segment["p_vaddr"] + segment["p_offset"]
        return data[offset:offset + size]

    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for address in (0x34e9000, 0x40b6000):
        machine.mem_map(address, 0x1000)
        machine.mem_write(address, read_va(address, 0x1000))
    for address, size in ((STACK, 0x10000), (MESSAGE, 0x1000), (RETURN, 0x1000)):
        machine.mem_map(address, size)
    registers = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                 UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7)
    for register, value in zip(registers, arguments):
        machine.reg_write(register, value)
    machine.reg_write(UC_ARM64_REG_SP, STACK_TOP)
    machine.reg_write(UC_ARM64_REG_LR, RETURN)
    machine.mem_write(STACK_TOP, struct.pack("<QQ", 0, MESSAGE))
    instructions = run_bounded(machine, 0x34e93b0, UC_ARM64_REG_PC, 0x34e93b0, 0x34e9564)
    fields = list(machine.mem_read(MESSAGE + 8, 5))
    return {"arguments_w0_to_w7": arguments, "cinematic_look_argument": 0,
            "fields": dict(zip(("base_look", "shooting_mode", "input_gamut", "color_gamut", "target_display"), fields)),
            "instruction_count": instructions}


def cp_case(data, function, arguments, stack_arguments=()):
    machine = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_LITTLE_ENDIAN)
    page = function & ~0xfff
    machine.mem_map(page, 0x1000)
    machine.mem_write(page, data[page - CP_BASE:page - CP_BASE + 0x1000])
    for address, size in ((STACK, 0x10000), (RETURN, 0x1000), (0x199e000, 0x1000)):
        machine.mem_map(address, size)
    for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), arguments):
        machine.reg_write(register, value)
    for index, value in enumerate(stack_arguments):
        machine.mem_write(STACK_TOP + index * 4, struct.pack("<I", value))
    machine.reg_write(UC_ARM_REG_SP, STACK_TOP)
    machine.reg_write(UC_ARM_REG_LR, RETURN | 1)
    end = 0xb15d48 if function == 0xb15d30 else 0x4dcb7e
    instructions = run_bounded(machine, function | 1, UC_ARM_REG_PC, function, end)
    return {"arguments_r0_to_r3": list(arguments), "stack_arguments": list(stack_arguments),
            "return_r0": machine.reg_read(UC_ARM_REG_R0), "instruction_count": instructions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extracted_root", type=Path, help="unpacked-complete directory")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("Output exists")
    ap = (args.extracted_root / "files/nflasha15/lib/appFw.so").read_bytes()
    cp = (args.extracted_root / "files/nflasha8/cpapp-b.bin").read_bytes()
    check_hash(ap, AP_SHA256)
    check_hash(cp, CP_SHA256)
    cases = []
    # These explicit inputs isolate the general-shooting-mode parameter. They
    # are synthetic inputs, not a dump of a camera's currently active settings.
    for name, general_mode, pp in (("zero_mode_user_lut_pp12", 0, 12),
                                  ("nonzero_mode_user_lut_pp12", 1, 12),
                                  ("nonzero_mode_ordinary_pp1", 1, 1)):
        item = ap_case(ap, [3, 1, 0, 0, 0, pp, general_mode, 1])
        item["name"] = name
        fields = item["fields"]
        normalized = cp_case(cp, 0xb15d30, (fields["base_look"], fields["shooting_mode"], 0xbc00))
        item["cp_normalize_base_look"] = normalized
        item["cp_get_mlut_acquire_id"] = cp_case(cp, 0x4dcab8,
            (fields["shooting_mode"], normalized["return_r0"], fields["color_gamut"], fields["input_gamut"]),
            (fields["target_display"], 0))
        cases.append(item)
    normalization_cases = [
        cp_case(cp, 0xb15d30, (10, 3, 0xbc00)),
        cp_case(cp, 0xb15d30, (10, 0, 0xbc00)),
        cp_case(cp, 0xb15d30, (10, 0, 0)),
    ]
    expected_fields = [(11, 3, 0, 1, 0), (10, 0, 0, 1, 0), (11, 3, 0, 1, 0)]
    for item, expected, expected_id in zip(cases, expected_fields, (0, 11, 0)):
        if tuple(item["fields"].values()) != expected:
            raise AssertionError(f"Unexpected AP output: {item}")
        if item["cp_get_mlut_acquire_id"]["return_r0"] != expected_id:
            raise AssertionError(f"Unexpected MLUT resource selection: {item}")
    if [item["return_r0"] for item in normalization_cases] != [11, 10, 0]:
        raise AssertionError("Unexpected CP base-look normalization")
    dispatch = []
    for address, expected_id, expected_setter in ((0x10cbda4, 0x4155, 0x351201), (0x10cbdcc, 0x4157, 0x8bff83)):
        words = struct.unpack_from("<5I", cp, address - CP_BASE)
        if words[:2] != (expected_id, expected_setter):
            raise AssertionError("CP dispatch table mismatch")
        dispatch.append({"table_va": address, "message_id": words[0], "setter_thumb_pointer": words[1],
                         "other_words": list(words[2:])})
    report = {"schema": "a6700-lab/native-lut-parameter-emulation/1", "ap_sha256": AP_SHA256, "cp_sha256": CP_SHA256,
              "cp_base": CP_BASE, "method": "Unmodified helper machine code; bounded Unicorn CPU emulation; zero-initialized debug overrides",
              "ap_cp_cases": cases, "cp_normalization_cases": normalization_cases, "cp_dispatch_records": dispatch,
              "limitations": ["Synthetic inputs, not captured camera state", "No firmware scheduler, DMA, ISP, MLUT hardware or JPEG encoder emulation",
                              "No camera connection or camera write", "Runtime load addresses and still-JPEG routing remain unverified"]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for item in cases:
        print(item["name"], item["fields"], "normalized_base_look", item["cp_normalize_base_look"]["return_r0"],
              "MLUT acquire id", item["cp_get_mlut_acquire_id"]["return_r0"])
    print("CP isolated normalization:", [item["return_r0"] for item in normalization_cases])
    print("Verified CP message IDs: 0x4155 and 0x4157")


if __name__ == "__main__":
    main()
