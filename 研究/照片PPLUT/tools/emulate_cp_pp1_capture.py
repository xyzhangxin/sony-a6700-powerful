"""Execute the compiled CP hooks and compare stock instruction boundaries."""

import argparse
import json
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB
from unicorn import UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE
import unicorn.arm_const as arm

from build_cp_pp1_capture import (CP_BASE, CODE, CODE_END, TRACE, TRACE_BYTES,
                                  binding, image_slice, material, require)


STACK = 0x8000000
TOP = STACK + 0xF000
PACKET = 0x9000000
RETURN = 0xA000000
CONFIG = 0x15BF360
ASSET = 0x15BE310
TASK = 0x199A1A0
TABLE = 0x19CA078
CALLBACK = 0x19C52D8
UPPER_GENERATION = 0x19B6A0C
TAG = 0x31544350
ARMED, FREEZE, LOST, WRITING = 0x11, 2, 4, 8
REGISTERS = tuple(getattr(arm, f'UC_ARM_REG_R{i}') for i in range(13))
ROW = struct.Struct('<5I')


class Machine:
    def __init__(self, image, edits=()):
        self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        size = (len(image) + 4095) & ~4095
        self.u.mem_map(CP_BASE, size)
        self.u.mem_write(CP_BASE, image)
        for edit in edits:
            self.u.mem_write(edit['address'], edit['after'])
        self.u.mem_protect(CP_BASE, size, UC_PROT_READ | UC_PROT_EXEC)
        self.u.mem_protect(TRACE & ~4095, 4096, UC_PROT_READ | UC_PROT_WRITE)
        for address, length in ((STACK, 0x10000), (PACKET, 4096), (RETURN, 4096),
                                (ASSET & ~4095, 8192), (TASK & ~4095, 4096),
                                (TABLE & ~4095, 4096), (CALLBACK & ~4095, 4096),
                                (UPPER_GENERATION & ~4095, 4096)):
            self.u.mem_map(address, length)
        self.cuts, self.tail, self.cache_calls = (), 0, []
        self.submit_boundary = None
        self.u.hook_add(UC_HOOK_CODE, self.guard)

    def read(self, address, words=1):
        result = struct.unpack(f'<{words}I', self.u.mem_read(address, words * 4))
        return result[0] if words == 1 else result

    def write(self, address, *values):
        self.u.mem_write(address, struct.pack(f'<{len(values)}I', *values))

    def r(self, index):
        return self.u.reg_read(REGISTERS[index])

    def guard(self, uc, address, size, user):
        if address in self.cuts:
            uc.emu_stop()
        elif address == 0x5FA164:
            assert not uc.reg_read(arm.UC_ARM_REG_CPSR) & 0x20
            assert uc.reg_read(arm.UC_ARM_REG_SP) % 8 == 0
            self.cache_calls.append((self.r(0), self.r(1)))
            uc.reg_write(arm.UC_ARM_REG_PC, uc.reg_read(arm.UC_ARM_REG_LR))
        elif address == 0x542344:
            self.submit_boundary = self.result()
            uc.reg_write(arm.UC_ARM_REG_R0, 0x3A)
            uc.reg_write(arm.UC_ARM_REG_PC, uc.reg_read(arm.UC_ARM_REG_LR))
        elif not (CODE <= address < CODE_END or self.start <= address < self.start + 4
                  or address in (0x55A680, 0x55A6B0)
                  or 0x55A664 <= address < 0x55A752):
            raise AssertionError(f'Unexpected CP execution at {address:#x}')

    def result(self):
        return dict(registers=[self.r(i) for i in range(13)], stack=self.u.reg_read(arm.UC_ARM_REG_SP),
                    link=self.u.reg_read(arm.UC_ARM_REG_LR),
                    apsr=self.u.reg_read(arm.UC_ARM_REG_CPSR) & 0xF8000000)

    def execute(self, address, registers=(), *, cuts=(RETURN,), flags=0xA8000000):
        self.start, self.cuts = address, cuts
        self.u.reg_write(arm.UC_ARM_REG_CPSR, flags | 0x33)
        for index, register in enumerate(REGISTERS):
            self.u.reg_write(register, 0x5A000000 + 17 * index)
        for item, value in registers:
            self.u.reg_write(REGISTERS[item], value)
        self.u.reg_write(arm.UC_ARM_REG_SP, TOP)
        self.u.reg_write(arm.UC_ARM_REG_LR, RETURN | 1)
        self.u.emu_start(address | 1, RETURN, timeout=200_000, count=5000)
        pc = self.u.reg_read(arm.UC_ARM_REG_PC)
        assert pc in cuts, hex(pc)
        return pc, self.result()

    def query(self, operation, *, selector=0x43, tag=TAG, reserved=0):
        self.u.mem_write(PACKET, struct.pack('<BBH5I', selector, 0, reserved, operation, 0, 0, 0, tag))
        self.execute(0x55A664, [(0, PACKET)])
        return struct.unpack('<BBH5I', self.u.mem_read(PACKET, 24))

    def snapshot(self):
        raw = bytes(self.u.mem_read(TRACE, TRACE_BYTES))
        state, count = struct.unpack_from('<II', raw)
        assert count <= 24 and raw[8:32] == bytes(24)
        return state, tuple(ROW.unpack_from(raw, 32 + 20*i) for i in range(count))


def new_events(machine):
    desc = dict(generation=0x180, asset=34431, lut=48000, registers=124)
    machine.write(ASSET, desc['asset'])
    machine.write(CONFIG + 0x78, desc['lut'], desc['registers'])
    args = [(0, desc['generation']), (4, CONFIG)]
    yield 0x351300, args, (0x351304,), (1, desc['generation'], 34431, 48000, 124)
    machine.write(PACKET + 64, 0x40003)
    machine.write(TOP, PACKET + 64)
    machine.write(PACKET + 0x70, desc['generation'])
    args = [(2, 6), (3, 0x4DCA4D), (4, PACKET)]
    yield 0x4DC86E, args, (0x4DC872,), (2, desc['generation'], 0x40003, 6, desc['generation'])
    machine.write(CALLBACK, 0x4DCA4D)
    machine.write(UPPER_GENERATION, desc['generation'])
    args = [(0, 0)]
    yield 0xB6707A, args, (0xB6707E,), (3, desc['generation'], 0, 0x4DCA4D, desc['generation'])
    yield 0xB66FC2, args, (0xB66FC6, 0xB67006), (4, desc['generation'], 0, 0x4DCA4D, desc['generation'])
    machine.write(PACKET + 128, TASK)
    machine.write(PACKET + 132, 0x7788)
    machine.write(TASK + 0x24, 0x40003)
    for bank in range(4):
        machine.write(TABLE + bank * 8, 0x40003, 1)
    machine.u.mem_write(TOP + 0x4C, bytes([1]))
    args = [(4, PACKET + 132), (10, PACKET + 128)]
    yield 0x480F74, args, (0x480F78,), (5, desc['generation'], 0x40003, 0x7788, 0xF01)


def replay(image, edits, symbols):
    stock, patched = Machine(image), Machine(image, edits)
    findings = []
    assert patched.query(2)[1] == 0x80
    assert patched.query(1, tag=0)[1] == 0x80
    assert patched.query(1, reserved=1)[1] == 0x80
    assert patched.snapshot() == (0, ())
    findings.append('private_commands_require_marker_and_prior_arm')

    for left, right in zip(new_events(stock), new_events(patched), strict=True):
        address, regs, cuts, _ = left
        assert left == right and stock.execute(address, regs, cuts=cuts) == patched.execute(address, regs, cuts=cuts)
    assert patched.snapshot() == (0, ())
    assert patched.cache_calls == []
    findings.append('all_unarmed_hooks_preserve_registers_flags_stack_and_original_results')

    assert patched.query(1)[1] == 0x5A
    assert patched.query(1)[1] == 0x80
    expected = []
    for left, right in zip(new_events(stock), new_events(patched), strict=True):
        address, regs, cuts, row = left
        assert left == right and stock.execute(address, regs, cuts=cuts) == patched.execute(address, regs, cuts=cuts)
        assert stock.submit_boundary == patched.submit_boundary
        expected.append(row)
    assert patched.snapshot() == (ARMED, tuple(expected))
    findings.append('five_compiled_sites_capture_fields_and_preserve_original_state')

    query = patched.query(2)
    assert query == (0x43, 0x5B, 0, 2, TRACE, 0, TRACE_BYTES, TAG)
    assert patched.cache_calls == [(TRACE, TRACE_BYTES)]
    saved = bytes(patched.u.mem_read(TRACE, TRACE_BYTES))
    for address, regs, cuts, _ in new_events(patched):
        patched.execute(address, regs, cuts=cuts)
    assert bytes(patched.u.mem_read(TRACE, TRACE_BYTES)) == saved
    assert patched.query(1)[1] == 0x80 and patched.query(2)[1] == 0x80
    findings.append('successful_collect_is_one_shot_with_fixed_cache_range_and_immutable_rows')

    # Original selectors and arbitrary wrong marker remain within the original
    # callback rules. Stop at the first original branch before external APIs.
    for selector in (0, 1, 2, 3, 4, 5, 255):
        for machine in (stock, patched):
            machine.u.mem_write(PACKET, bytes([selector]) + bytes(23))
        cut = (0x55A680,)
        assert stock.execute(0x55A664, [(0, PACKET)], cuts=cut) == patched.execute(0x55A664, [(0, PACKET)], cuts=cut)
        assert bytes(stock.u.mem_read(TOP - 40, 40)) == bytes(patched.u.mem_read(TOP - 40, 40))
    findings.append('seven_stock_selectors_reenter_original_machine_code_with_original_frame')

    m = Machine(image, edits)
    assert m.query(1)[1] == 0x5A
    for _ in range(24):
        m.execute(symbols['cp_pp1_record'] & ~1, [(0, 1)])
    assert m.snapshot()[0] == ARMED and len(m.snapshot()[1]) == 24
    m.execute(symbols['cp_pp1_record'] & ~1, [(0, 1)])
    assert m.snapshot()[0] == (ARMED | LOST) and len(m.snapshot()[1]) == 24
    assert m.query(2)[1] == 0x80 and not m.cache_calls
    findings.append('capacity_exhaustion_never_overwrites_or_publishes_as_complete')

    m = Machine(image, edits)
    assert m.query(1)[1] == 0x5A
    m.write(TRACE, ARMED | WRITING)
    assert m.query(2)[1] == 0x80 and not m.cache_calls
    m.execute(symbols['cp_pp1_record'] & ~1, [(0, 1)])
    assert m.snapshot() == (ARMED | WRITING | LOST, ())
    m.write(TRACE, ARMED | LOST)
    assert m.query(2)[1] == 0x80 and not m.cache_calls
    findings.append('in_flight_and_nested_producer_conflicts_refuse_complete_capture')

    m = Machine(image, edits)
    m.query(1)
    before = stock.execute(0xB66FC2, [(0, 0xFFFFFFF9)], cuts=(0xB66FC6, 0xB67006))
    after = m.execute(0xB66FC2, [(0, 0xFFFFFFF9)], cuts=(0xB66FC6, 0xB67006))
    assert before == after and after[0] == 0xB66FC6 and m.snapshot()[1][0][2] == 0xFFFFFFF9
    m.write(PACKET + 128, TASK - 0x14C)
    m.execute(0x480F74, [(4, PACKET + 132), (10, PACKET + 128)], cuts=(0x480F78,))
    assert len(m.snapshot()[1]) == 1
    findings.append('native_wait_error_retained_and_unrelated_task_ignored')
    return findings, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cp', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    require(not args.out.exists(), 'Use a new acceptance output')
    build = args.package / 'build.json'
    manifest = json.loads(build.read_bytes())
    elf = args.package / 'capture.elf'
    require(binding(elf) == manifest['files']['capture.elf'], 'Observer ELF changed')
    image = args.cp.read_bytes()
    edits, symbols = material(image, elf.read_bytes())
    require(image_slice(image, TRACE, TRACE_BYTES) == bytes(TRACE_BYTES), 'Wrong initial trace')
    cases, rows = replay(image, edits, symbols)
    result = dict(schema='d6700/CP-PP1-compiled-observer-acceptance/1', result='passed',
                  camera_access=False, case_count=len(cases), cases=cases, replayed_rows=rows,
                  executable= binding(elf), build=binding(build), source=binding(Path(__file__)),
                  limits=['The actual compiled branches and recorder execute in Unicorn.',
                          'Direct call entry, cache endpoint and one interrupted-writer state are fixtures.',
                          'No real interrupt scheduling, boot publication, live provenance or pixels.'])
    with args.out.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(f'{len(cases)} compiled CP observer groups passed')


if __name__ == '__main__':
    main()
