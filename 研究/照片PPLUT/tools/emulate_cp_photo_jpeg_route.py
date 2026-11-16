"""Trace native still-VFX and main-JPEG descriptor construction offline.

Only the listed original Thumb ranges execute. Buffer-manager metadata,
task guards, logging, and unrelated resource helpers are named fixtures.
The transfer and JPEG encoder are capture-only endpoints: no image pixels,
MLUT hardware, asynchronous scheduler, or camera are emulated.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_LITTLE_ENDIAN,
                     UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
                     UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC)
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R7, UC_ARM_REG_R8,
                               UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                               UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)
from emulate_lut_parameter_helpers import CP_BASE, CP_SHA256, check_hash

STACK, TOP, DATA, RETURN = 0x8000000, 0x800f000, 0x9000000, 0xa000000
CONTEXT, GROUP, AUX_A, AUX_B, BITS, WRAPPER = (
    DATA, DATA + 0x8000, DATA + 0x9000, DATA + 0xa000,
    DATA + 0xb000, DATA + 0xc000)
JOBS = 0x1688200
STATE, WAIT = 0x19b69f8, 0x1a67a1b
MAIN_HANDLE, JPEG_HANDLE, UNUSED_HANDLE = 0x123401, 0x123402, 0x123403
DEVELOP_FETCH, DEVELOP_FETCH_END = 0x47e236, 0x47e27c
PHOTO_BUILD, PHOTO_SUBMIT = 0x480b84, 0x475768
JPEG_STAGE, JPEG_STAGE_END = 0x48220c, 0x4822c4
JPEG_PACK, JPEG_PACK_END = 0x481aa8, 0x481b16

# The data-flow checks also lock the meaning of the relevant offsets to the
# actual instruction encodings. Nearby strings alone are not the evidence.
INSTRUCTIONS = {
    0x47e25e: ('movw', 'r0, #0x5b04'),
    0x47e262: ('add.w', 'r1, fp, #0xd8'),
    0x47e26a: ('bl', '#0xaff94a'),
    0x480c64: ('ldrd', 'r3, r5, [r4, #0xf0]'),
    0x480d3a: ('str.w', 'r1, [r5, #0x170]'),
    0x480d34: ('str.w', 'r1, [r5, #0x174]'),
    0x480d82: ('str.w', 'fp, [r5, #0x1d4]'),
    0x480f70: ('bl', '#0x475768'),
    0x4757ea: ('add.w', 'r0, sl, #8'),
    0x4758c0: ('bl', '#0x541974'),
    0x48222c: ('ldr', 'r0, [r4, r0]'),
    0x482230: ('bl', '#0xaff94a'),
    0x48228c: ('ldrd', 'r1, r2, [sp, #0x268]'),
    0x48229c: ('strd', 'r1, r2, [sp, #0x30]'),
    0x4823b0: ('bl', '#0x481aa8'),
    0x481abc: ('ldrd', 'r0, r1, [r6]'),
    0x481ac0: ('strd', 'r0, r1, [sp, #0xd8]'),
    0x481c72: ('bl', '#0x473688'),
    0x4dc090: ('ldr.w', 'r1, [r8]'),
    0x4dc106: ('str.w', 'r1, [sp, #0xb3c]'),
}


def static_checks(raw):
    dis = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    result = []
    for address, expected in INSTRUCTIONS.items():
        decoded = next(dis.disasm(raw[address - CP_BASE:address - CP_BASE + 4], address))
        actual = (decoded.mnemonic, decoded.op_str)
        if actual != expected:
            raise AssertionError((hex(address), actual, expected))
        result.append(dict(va=hex(address), instruction=' '.join(actual), bytes=decoded.bytes.hex()))
    return result


class Route:
    def __init__(self, raw, main_physical, jpeg_physical, width, height, state=2, wait=0):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_LITTLE_ENDIAN)
        length = (len(raw) + 4095) & ~4095
        self.uc.mem_map(CP_BASE, length)
        self.uc.mem_write(CP_BASE, raw)
        self.uc.mem_protect(CP_BASE, length, UC_PROT_READ | UC_PROT_EXEC)
        for at, size in ((STACK, 0x10000), (DATA, 0x20000),
                         (0x1688000, 0x3000), (0x199c000, 0x1000), (0x199e000, 0x1000),
                         (0x19b6000, 0x1000), (0x1a11000, 0x1000),
                         (0x1a67000, 0x1000)):
            self.uc.mem_map(at, size, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(RETURN, 0x1000)
        self.events, self.state_reads, self.visited, self.writes = [], [], set(), []
        self.allowed, self.phase, self.cutpoint = (), '', None
        self.instructions = 0
        self.uc.hook_add(UC_HOOK_CODE, self.guard)
        self.uc.hook_add(UC_HOOK_MEM_READ, self.read_guard)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write_guard)
        self.main, self.jpeg, self.width, self.height = main_physical, jpeg_physical, width, height
        self.put(CONTEXT + 0x5b04, MAIN_HANDLE)
        self.put(CONTEXT + 0x5b20, JPEG_HANDLE)
        self.put(CONTEXT + 0x5b30, UNUSED_HANDLE)
        self.put(CONTEXT + 0x6090, width, 'H')
        self.put(CONTEXT + 0x6092, height, 'H')
        # Stage_Dev_Vfx copies 0x6090/92 to 0x60c4/c6 before this builder.
        self.put(CONTEXT + 0x60c4, width, 'H')
        self.put(CONTEXT + 0x60c6, height, 'H')
        self.put(WRAPPER, CONTEXT)
        self.put(STATE, state)
        self.put(WAIT, wait, 'B')
        # Synthetic side information, sufficient to exercise the normal
        # single-pass 0x20 photo branch. These are not captured camera structs.
        self.put(AUX_A + 0xb8, height)
        self.put(AUX_A + 0xc8, height)
        self.put(AUX_A + 0xe0, height)
        self.put(AUX_B + 0x88, width)
        self.put(AUX_B + 0x90, width)
        self.put(AUX_B + 0x98, width)
        self.put(AUX_B + 0x9c, 0)
        self.put(AUX_B + 0xa0, 0)
        self.uc.mem_write(JOBS, b'\xa5' * 0x1920)

    def put(self, at, value, fmt='I'):
        self.uc.mem_write(at, struct.pack('<' + fmt, value))

    def get(self, at, fmt='I'):
        return struct.unpack('<' + fmt, bytes(self.uc.mem_read(at, struct.calcsize('<' + fmt))))[0]

    def r(self, n):
        return self.uc.reg_read(UC_ARM_REG_R0 + n)

    def ret(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value & 0xffffffff)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def metadata(self, handle):
        if handle not in (MAIN_HANDLE, JPEG_HANDLE, UNUSED_HANDLE):
            raise AssertionError(f'Unknown fixture buffer handle {handle:#x}')
        physical = {MAIN_HANDLE: self.main, JPEG_HANDLE: self.jpeg, UNUSED_HANDLE: 0x6789a0000}[handle]
        blob = bytearray(0xd8)
        struct.pack_into('<Q', blob, 0x18, physical)
        struct.pack_into('<I', blob, 0x20, 0x100000)
        return bytes(blob)

    def read_guard(self, _uc, _access, address, size, _value, _user):
        if (address < STATE + 4 and address + size > STATE) or (address <= WAIT < address + size):
            self.state_reads.append(dict(phase=self.phase, address=hex(address), bytes=size))

    def write_guard(self, _uc, _access, address, size, value, _user):
        if not any(a <= address and address + size <= b for a, b in (
                (STACK, STACK + 0x10000), (JOBS, JOBS + 0x1920), (BITS, BITS + 4),
                (0x199c174, 0x199c224))):
            raise RuntimeError(f'Unexpected native write {address:#x}, {size} bytes, phase={self.phase}')
        if JOBS <= address < JOBS + 0x1920:
            self.writes.append(dict(va=hex(self.uc.reg_read(UC_ARM_REG_PC)),
                                    address=hex(address), bytes=size, value=value))

    def guard(self, _uc, address, _size, _user):
        self.instructions += 1
        if self.instructions > 16000:
            raise RuntimeError('Aggregate instruction budget exceeded')
        self.visited.add(address)
        if address == self.cutpoint:
            if address == PHOTO_SUBMIT:
                sp = self.uc.reg_read(UC_ARM_REG_SP)
                header = self.r(3)
                channels = [self.get(sp + 4 * n) for n in range(4)]
                self.events.append(dict(fixture='capture_only.photo_vfx_submission',
                    api=hex(address), task_handle=hex(self.r(1)), common_header=hex(header),
                    channel_pointers=[hex(v) for v in channels]))
                self.submitted = (header, channels)
            self.uc.emu_stop()
            return
        if address == 0xaff94a:
            handle, out = self.r(0), self.r(1)
            self.uc.mem_write(out, self.metadata(handle))
            self.events.append(dict(fixture='buffer_manager.metadata', phase=self.phase,
                                    handle=hex(handle), destination=hex(out)))
            self.ret()
        elif address in (0x481544, 0xafe31e):
            self.events.append(dict(fixture='resource_helper', api=hex(address), phase=self.phase))
            self.ret()
        elif address == 0xafe1d2:
            self.events.append(dict(fixture='capture_mode.query', return_r0=0))
            self.ret()
        elif address == 0xb28824:
            self.events.append(dict(fixture='logger.information', phase=self.phase))
            self.ret()
        elif address == 0xb287b4:
            raise RuntimeError(f'Native diagnostic/log branch reached at {address:#x}')
        elif address == 0xafdcb4:
            self.events.append(dict(fixture='capture_metadata.query', return_r0=0))
            self.ret()
        elif address == 0x612240:
            self.uc.mem_write(self.r(0), bytes(self.uc.mem_read(self.r(1), self.r(2))))
            self.events.append(dict(fixture='memory.copy', bytes=self.r(2)))
            self.ret()
        elif not any(a <= address < b for a, b in self.allowed):
            raise RuntimeError(f'Unexpected execution {address:#x}, phase={self.phase}')

    def execute(self, phase, start, end, ranges, args=(), stack_args=(), regs=()):
        self.phase, self.allowed, self.cutpoint = phase, ranges, end
        self.uc.reg_write(UC_ARM_REG_SP, TOP)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        for n, value in enumerate(args):
            self.uc.reg_write(UC_ARM_REG_R0 + n, value)
        for n, value in enumerate(stack_args):
            self.put(TOP + n * 4, value)
        for reg, value in regs:
            self.uc.reg_write(reg, value)
        try:
            self.uc.emu_start(start | 1, RETURN, timeout=200000, count=6000)
        except Exception as error:
            raise RuntimeError(f'{phase}: PC={self.uc.reg_read(UC_ARM_REG_PC):#x}, '
                               f'r0-r3={[hex(self.r(n)) for n in range(4)]}') from error
        if self.uc.reg_read(UC_ARM_REG_PC) != end:
            raise RuntimeError(f'Phase {phase} did not reach expected cutpoint')

    def verify(self):
        self.execute('photo_buffer_metadata', DEVELOP_FETCH, DEVELOP_FETCH_END,
                     ((DEVELOP_FETCH, DEVELOP_FETCH_END),),
                     regs=((UC_ARM_REG_R10, CONTEXT), (UC_ARM_REG_R9, 0x20),
                           (UC_ARM_REG_R11, GROUP)))
        assert self.get(GROUP + 0xf0, 'Q') == self.main
        self.execute('photo_vfx_build', PHOTO_BUILD, PHOTO_SUBMIT,
                     ((PHOTO_BUILD, 0x480fc0),),
                     args=(CONTEXT, 0x20, DATA + 0xd000, GROUP),
                     stack_args=(BITS, AUX_A, AUX_B, 0, 0))
        header, channels = self.submitted
        assert header == JOBS and channels == [JOBS + 0x10, JOBS + 0x330, JOBS + 0x650, JOBS + 0x970]
        channel = channels[0]
        main_port = channel + 0x158
        assert self.get(main_port) == 0x10000
        assert self.get(main_port + 8, 'Q') == self.main
        assert self.get(main_port + 0x6c) == 0
        stores = [w for w in self.writes if int(w['address'], 0) == main_port + 0x6c]
        assert stores == [dict(va='0x480d82', address=hex(main_port + 0x6c), bytes=4, value=0)]
        self.execute('main_jpeg_stage_pack', JPEG_STAGE, JPEG_STAGE_END,
                     ((JPEG_STAGE, JPEG_STAGE_END),), args=(WRAPPER,))
        jpeg_params = self.uc.reg_read(UC_ARM_REG_SP) + 0x30
        assert self.get(jpeg_params, 'Q') == self.main
        assert self.get(jpeg_params + 8, 'Q') == self.jpeg
        assert [self.get(jpeg_params + 0x10 + 2 * n, 'H') for n in range(2)] == [self.width, self.height]
        # Preserve caller-produced arguments before starting another stack frame.
        blob = bytes(self.uc.mem_read(jpeg_params, 0x78))
        self.uc.mem_write(DATA + 0xe000, blob)
        self.execute('native_jpeg_encoder_argument_prefix', JPEG_PACK, JPEG_PACK_END,
                     ((JPEG_PACK, JPEG_PACK_END),),
                     args=(CONTEXT, DATA + 0xe000, DATA + 0xf000, 0))
        packed = self.uc.reg_read(UC_ARM_REG_SP) + 0xd8
        assert self.get(packed, 'Q') == self.main
        assert self.get(packed + 0x30, 'Q') == self.jpeg
        assert [self.get(packed + 8 + n * 4) for n in range(2)] == [self.width, self.height]
        assert not self.state_reads
        return dict(main_physical=hex(self.main), jpeg_destination_physical=hex(self.jpeg),
            dimensions=[self.width, self.height], photo_transfer_output=hex(self.get(main_port + 8, 'Q')),
            main_jpeg_input=hex(self.get(packed, 'Q')),
            main_port_tail_word_offset='0x6c', main_port_tail_word=0,
            native_zero_store=stores, mlut_state_reads=self.state_reads,
            instruction_count=self.instructions, events=self.events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cp', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Output exists')
    raw = args.cp.read_bytes()
    check_hash(raw, CP_SHA256)
    instructions = static_checks(raw)
    cases = []
    for width, height, main_address, jpeg_address in (
            (6192, 4128, 0x112340000, 0x234560000),
            (3096, 2064, 0x723450000, 0x845670000),
            (6000, 4000, 0x98760000, 0xa9870000)):
        for state, wait in ((0, 0), (2, 1), (2, 0)):
            runner = Route(raw, main_address, jpeg_address, width, height, state, wait)
            case = runner.verify()
            case.update(synthetic_mlut_state=state, synthetic_wait_callback=wait)
            cases.append(case)
    report = dict(schema='a6700-lab/native-photo-main-jpeg-descriptor-route/1',
        result='verified_bounded_native_descriptor_route', camera_access=False,
        cp_sha256=CP_SHA256, tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        instruction_checks=instructions, cases=cases,
        findings=[
            'Photo development and main-JPEG stage both obtain the buffer handle at capture_context+0x5b04',
            'With the named buffer-manager fixture, original code transports metadata+0x18 uint64 to the photo VFX main output and main JPEG encoder input',
            'The photo 0x20 branch unconditionally writes zero to that main output descriptor+0x6c at 0x480d82',
            'The native view/VFX wrapper writes the Main output policy to the same descriptor offset: stack+0xb3c minus group base stack+0x978 equals 0x1c4 = 0x158+0x6c',
            'The executed photo builder does not consult the earlier VFX MLUT state or callback-pending globals'],
        limitations=[
            'Synthetic handles, physical addresses, metadata and side structures; not a live camera capture',
            'Only the 0x20, single-pass, index-0 photo branch is executed; other photo modes remain unverified',
            'Buffer-manager lookup and auxiliary helpers are fixtures; aliasing, allocator behavior and lifetime are not proved',
            'Photo transfer and JPEG encoder are capture endpoints; no hardware pixels or completed JPEG are produced',
            'No proof yet that enabling this word acquires the correct MLUT engine, waits for loading, or survives task cleanup',
            'No runtime CP address/call patch or camera module is installed'])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f'{len(cases)} native descriptor cases passed; main photo output matches main JPEG input; output tail word forced to zero.')


if __name__ == '__main__':
    main()
