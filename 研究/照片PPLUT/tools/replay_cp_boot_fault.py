"""Replay the failed A6700 2.00 PP1 startup from locally rebuilt code."""

import argparse
import hashlib
import json
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_ARM, CS_ARCH_ARM64, CS_MODE_ARM, CS_MODE_LITTLE_ENDIAN
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_MODE_ARM
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP, UC_CPU_ARM_CORTEX_A9,
)

from build_cp_pp1_capture import CP_BASE, CP_SHA256, image_slice, material, require


LOADER_SHA256 = '314d372d0d2b9883c3b9dc04b40389edb3100e6d5ef529633cc427c3d0cab72e'
PAGE_BYTES = 0x1000
INSTRUCTION_LIMIT = 300_000
STAGES = {
    0x108398: 'page_table_init',
    0x108974: 'input_wait',
    0x108988: 'input_wait_returned',
    0x108728: 'call_boot_finalizer',
    0x10F000: 'candidate_trampoline',
    0x108070: 'stock_finalizer_entered',
}


def candidate_image(stock, observer_elf):
    edits, _ = material(stock, observer_elf)
    updated = bytearray(stock)
    for edit in edits:
        offset = edit['address'] - CP_BASE
        updated[offset:offset + len(edit['after'])] = edit['after']
    require(sum(before != after for before, after in zip(stock, updated, strict=True)) == 711,
            'Rebuilt CP change count differs from the installation audit')
    return bytes(updated)


def arm_vm():
    vm = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    vm.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
    return vm


def replay(image, *, padding, wait_byte):
    vm = arm_vm()
    vm.mem_map(CP_BASE, PAGE_BYTES)
    vm.mem_write(CP_BASE, image_slice(image, CP_BASE, PAGE_BYTES))
    vm.mem_map(0x128000, PAGE_BYTES)
    vm.mem_write(0x128000, image_slice(image, 0x128000, PAGE_BYTES))
    vm.mem_map(0x11DA000, 0x8000)
    vm.mem_map(0x1AD4000, 0xC000)
    vm.mem_map(0x3E42000, PAGE_BYTES)
    vm.mem_write(0x3E42001, bytes([wait_byte]))
    vm.mem_map(0x20000000, PAGE_BYTES)
    vm.reg_write(UC_ARM_REG_SP, 0x20000800)
    if padding != 'unmapped':
        vm.mem_map(0x10F000, 2 * PAGE_BYTES)
        if padding == 'candidate_bytes':
            vm.mem_write(0x10F000, image_slice(image, 0x10F000, 2 * PAGE_BYTES))

    stages = []
    fault = {}

    def stage(uc, address, _size, _user):
        if address in STAGES:
            stages.append({'address': hex(address), 'name': STAGES[address]})
        if address == 0x108070:
            uc.emu_stop()

    def memory_fault(uc, _access, address, _size, _value, _user):
        fault.update(address=hex(address), pc=hex(uc.reg_read(UC_ARM_REG_PC)))
        return False

    vm.hook_add(UC_HOOK_CODE, stage)
    vm.hook_add(UC_HOOK_MEM_INVALID, memory_fault)
    try:
        vm.emu_start(0x108398, 0xFFFFFFFF, count=INSTRUCTION_LIMIT)
    except UcError as error:
        outcome = 'emulator_fault'
        fault.update(error=str(error))
    else:
        outcome = 'stock_finalizer_entered' if vm.reg_read(UC_ARM_REG_PC) == 0x108070 else 'instruction_limit'
    return dict(padding=padding, wait_byte=wait_byte, outcome=outcome, stages=stages,
                stopped_at=hex(vm.reg_read(UC_ARM_REG_PC)), fault=fault or None)


def replay_stock_register_save(stock):
    vm = arm_vm()
    code, data, returned = 0x60DA5C, 0x20000000, 0x30000000
    vm.mem_map(code & -PAGE_BYTES, PAGE_BYTES)
    vm.mem_write(code, image_slice(stock, code, 0x78))
    vm.mem_map(data, PAGE_BYTES)
    vm.mem_map(returned, PAGE_BYTES)
    vm.reg_write(UC_ARM_REG_R0, data)
    vm.reg_write(UC_ARM_REG_LR, returned)
    # Inject synthetic register values, then execute the unmodified save routine.
    registers = (
        ('DFSR', 5, 0, 1, 0x805),
        ('DFAR', 6, 0, 2, 0x110000),
        ('VBAR', 12, 0, 3, 0x128180),
        ('IFSR', 5, 1, 13, 0x5),
        ('IFAR', 6, 2, 14, 0x10F000),
    )
    for _, crn, opc2, _, value in registers:
        vm.cpr_write(15, 0, crn, 0, opc2, 0, False, value)
    vm.emu_start(code, returned, count=80)
    require(vm.reg_read(UC_ARM_REG_PC) == returned, 'Original register saver did not return')
    words = struct.unpack('<16I', vm.mem_read(data, 64))
    for name, _, _, index, value in registers:
        require(words[index] == value, f'Original register saver did not copy {name}')
    return {name: hex(words[index]) for name, _, _, index, _ in registers}


def disassemble_at(decoder, image, base, addresses):
    return {hex(address): f'{ins.mnemonic} {ins.op_str}'
            for address in addresses
            for ins in decoder.disasm(image[address - base:address - base + 4], address)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cp', type=Path, required=True)
    parser.add_argument('--loader', type=Path, required=True, help='Locally extracted loader_spi.bin')
    parser.add_argument('--observer-elf', type=Path, required=True, help='Rebuilt capture.elf')
    parser.add_argument('--out', type=Path, required=True, help='New JSON evidence file')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Use a new output file')
    stock = args.cp.read_bytes()
    loader = args.loader.read_bytes()
    require(hashlib.sha256(stock).hexdigest() == CP_SHA256, 'Wrong CP image')
    require(hashlib.sha256(loader).hexdigest() == LOADER_SHA256, 'Wrong loader image')
    observer_elf = args.observer_elf.read_bytes()
    candidate = candidate_image(stock, observer_elf)

    cases = (
        ('stock', stock, 'unmapped', 0, 'stock_finalizer_entered', '0x108070'),
        ('candidate_loaded', candidate, 'candidate_bytes', 0, 'stock_finalizer_entered', '0x108070'),
        ('candidate_unmapped', candidate, 'unmapped', 0, 'emulator_fault', '0x10f000'),
        ('candidate_zeroed', candidate, 'zeroed', 0, 'emulator_fault', '0x111000'),
        ('stock_wait_held', stock, 'unmapped', 1, 'instruction_limit', '0x108980'),
    )
    results = {}
    for name, image, padding, wait, outcome, pc in cases:
        result = replay(image, padding=padding, wait_byte=wait)
        require(result['outcome'] == outcome and result['stopped_at'] == pc, f'Different replay for {name}')
        results[name] = result
    for name in ('candidate_unmapped', 'candidate_zeroed'):
        require('UC_ERR_FETCH_UNMAPPED' in results[name]['fault']['error'], f'{name} did not fail on fetch')

    dec = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    dec64 = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    report = dict(
        schema='d6700/CP-boot-fault-replay/1',
        camera_access=False,
        CP_stock_sha256=CP_SHA256,
        loader_spi_sha256=LOADER_SHA256,
        rebuilt_observer_sha256=hashlib.sha256(observer_elf).hexdigest(),
        rebuilt_CP_sha256=hashlib.sha256(candidate).hexdigest(),
        cp_boot_instructions=disassemble_at(dec, stock, CP_BASE, (
            0x108360, 0x108728, 0x10875C, 0x128180, 0x60DB2C, 0x60DB3C,
            0x60DB78, 0x600FAC, 0x609858, 0x625A10, 0x625A64,
        )),
        cp_candidate_branch=disassemble_at(dec, candidate, CP_BASE, (0x108728,)),
        post_mmu_entry=hex(struct.unpack('<I', image_slice(stock, 0x128000, 4))[0]),
        cp_vector_destinations=[hex(addr) for addr in struct.unpack(
            '<8I', image_slice(stock, 0x1281A0, 32))],
        loader_file_offsets=disassemble_at(dec64, loader, 0, (4, 8, 12, 0x5298, 0x58C4, 0x5998)),
        loader_ddr_log_strings=[
            loader[off:].partition(b'\0')[0].decode('ascii').strip() for off in (0x8B78, 0x8B93)
        ],
        instruction_limit=INSTRUCTION_LIMIT,
        replays=results,
        stock_exception_register_save_with_injected_values=replay_stock_register_save(stock),
        limits=[
            'Replay stops at entry to the stock MMU finalizer.',
            'Missing or zeroed pages, the wait byte, and memory regions are fixtures.',
            'The replay does not model the real loader copy, vector entry, or behavior of the AP.',
            'No device fault register, UART output, or retained DDR record was obtained.',
        ],
    )
    with args.out.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(f'Checked {len(results)} boot replays and the stock exception register save.')


if __name__ == '__main__':
    main()
