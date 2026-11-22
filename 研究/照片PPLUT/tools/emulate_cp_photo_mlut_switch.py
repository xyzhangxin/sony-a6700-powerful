"""Exercise the native driver consumer of the photo main MLUT policy word.

Photo descriptors come from the bounded original-code builder in Route.
Original driver policy, crop/alignment validation and register packing execute.
Resource-transition helpers, lower geometry writers and MMIO locations are
explicit fixtures. No physical hardware, image pixels or camera are accessed.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_LITTLE_ENDIAN,
                     UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
                     UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC)
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
from emulate_lut_parameter_helpers import CP_BASE, CP_SHA256, check_hash
from emulate_cp_photo_jpeg_route import Route, JOBS

STACK, TOP, DATA, RETURN, MMIO = 0x8000000, 0x800f000, 0x9000000, 0xa000000, 0xb000000
PARAM = DATA + 0x100
RESOURCE_STATE = 0x19ca038
REGISTER_BASES = 0xe799ac
POLICY = 0xb97516
RANGES = ((POLICY, 0xb97632), (0xb98e04, 0xb98e5e),
          (0xb95cfa, 0xb95d0c), (0xb972e4, 0xb97312),
          (0xb97400, 0xb97516), (0xb95ab2, 0xb95b30))
GEOMETRY_WRITERS = (0xb9585a, 0xb958f0, 0xb95986, 0xb95a1c)


class Driver:
    def __init__(self, raw, ports, resource=0, activated=False, flag=0,
                 clear_return=0, binding_return=0, bad_crop=False):
        self.resource = resource
        self.options = dict(clear_return=clear_return, binding_return=binding_return)
        self.events, self.reads, self.writes, self.instructions = [], [], [], 0
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_LITTLE_ENDIAN)
        size = (len(raw) + 4095) & ~4095
        self.uc.mem_map(CP_BASE, size)
        self.uc.mem_write(CP_BASE, raw)
        self.uc.mem_protect(CP_BASE, size, UC_PROT_READ | UC_PROT_EXEC)
        for at, length in ((STACK, 0x10000), (DATA, 0x2000),
                           (0x19ca000, 0x1000), (MMIO, 0x8000)):
            self.uc.mem_map(at, length, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(RETURN, 0x1000)
        # Replace four data-table words with emulator-only register-bank
        # pointers. No executable instruction is changed.
        self.uc.mem_protect(REGISTER_BASES & ~4095, 4096, UC_PROT_READ | UC_PROT_WRITE)
        for n in range(4):
            self.put(REGISTER_BASES + n * 4, MMIO + n * 0x2000)
        self.uc.mem_write(PARAM, ports)
        self.put(PARAM + 0x150 + 0x6c, flag)
        self.put(RESOURCE_STATE + resource * 4, (1 << 21) if activated else 0)
        if bad_crop:
            self.put(PARAM + 0x28, 9999)
        self.uc.hook_add(UC_HOOK_CODE, self.guard)
        self.uc.hook_add(UC_HOOK_MEM_READ, self.on_read)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write)

    def put(self, at, value):
        self.uc.mem_write(at, struct.pack('<I', value))

    def get(self, at):
        return struct.unpack('<I', bytes(self.uc.mem_read(at, 4)))[0]

    def r(self, n):
        return self.uc.reg_read(UC_ARM_REG_R0 + n)

    def ret(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def on_read(self, _uc, _access, at, size, _value, _user):
        if at == RESOURCE_STATE + self.resource * 4 or at == PARAM + 0x1bc:
            self.reads.append(dict(pc=hex(self.uc.reg_read(UC_ARM_REG_PC)), address=hex(at), bytes=size))

    def on_write(self, _uc, _access, at, size, value, _user):
        if STACK <= at and at + size <= STACK + 0x10000:
            return
        if at == MMIO + self.resource * 0x2000 + 0x10fc or at == 0x19ca024:
            self.writes.append(dict(pc=hex(self.uc.reg_read(UC_ARM_REG_PC)), address=hex(at), bytes=size, value=value))
            return
        raise RuntimeError(f'Unexpected native write {at:#x}, size={size}')

    def guard(self, _uc, at, _size, _user):
        self.instructions += 1
        if at == 0x612934:
            self.uc.mem_write(self.r(0), bytes(self.r(1)))
            self.ret()
        elif at in (0xb97642, 0xb976ae):
            name = 'resource_transition' if at == 0xb97642 else 'resource_binding'
            result = self.options['clear_return' if at == 0xb97642 else 'binding_return']
            self.events.append(dict(fixture=name, api=hex(at), resource_index=self.r(0 if at == 0xb97642 else 1), return_r0=result))
            self.ret(result)
        elif at in GEOMETRY_WRITERS:
            self.events.append(dict(fixture='lower_geometry_writer', api=hex(at), resource_index=self.r(1),
                                   rect=list(struct.unpack('<6H', bytes(self.uc.mem_read(self.r(0), 12))))))
            self.ret()
        elif not any(a <= at < b for a, b in RANGES):
            raise RuntimeError(f'Unexpected execution {at:#x}')

    def run(self):
        self.uc.reg_write(UC_ARM_REG_R0, PARAM)
        self.uc.reg_write(UC_ARM_REG_R0 + 1, self.resource)
        self.uc.reg_write(UC_ARM_REG_SP, TOP)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        self.uc.emu_start(POLICY | 1, RETURN, timeout=200000, count=6000)
        if self.uc.reg_read(UC_ARM_REG_PC) != RETURN:
            raise RuntimeError('Driver did not return within budget')
        return dict(return_r0=self.r(0), resource_index=self.resource,
                    packed_output_register=hex(self.get(MMIO + self.resource * 0x2000 + 0x10fc)),
                    events=self.events, reads=self.reads, writes=self.writes,
                    instruction_count=self.instructions)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('cp', type=Path)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        p.error('Output exists')
    raw = args.cp.read_bytes()
    check_hash(raw, CP_SHA256)
    route = Route(raw, 0x112340000, 0x234560000, 6192, 4128)
    provenance = route.verify()
    ports = bytes(route.uc.mem_read(JOBS + 0x10 + 8, 0x310))
    assert struct.unpack_from('<I', ports, 0x150)[0] == 0x10000
    assert struct.unpack_from('<Q', ports, 0x158)[0] == 0x112340000
    assert struct.unpack_from('<I', ports, 0x1bc)[0] == 0
    cases = []
    for resource in range(4):
        for active in (False, True):
            for flag in (0, 1, 2):
                runner = Driver(raw, ports, resource=resource, activated=active, flag=flag)
                result = runner.run()
                enabled = active and flag == 1
                assert result['return_r0'] == 0, (resource, active, flag, result)
                expected = 0x11110 if enabled else 0x11111
                assert int(result['packed_output_register'], 0) == expected, (resource, active, flag, result)
                transitions = [e['fixture'] for e in result['events'] if e['fixture'] != 'lower_geometry_writer']
                assert transitions == (['resource_transition', 'resource_binding'] if enabled else [])
                assert len([e for e in result['events'] if e['fixture'] == 'lower_geometry_writer']) == 4
                cases.append(dict(activated_cache_bit21=active, main_flag=flag, result=result))
    for name, options, expected in (
            ('resource_transition_failure', dict(activated=True, flag=1, clear_return=7), 7),
            ('resource_binding_failure', dict(activated=True, flag=1, binding_return=9), 9),
            ('invalid_input_crop', dict(activated=True, flag=1, bad_crop=True), 1)):
        result = Driver(raw, ports, **options).run()
        assert result['return_r0'] == expected, (name, result)
        assert not result['writes'], (name, result)
        cases.append(dict(name=name, result=result))
    report = dict(schema='a6700-lab/native-photo-mlut-output-policy/1', result='passed',
        camera_access=False, cp_sha256=CP_SHA256,
        tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        route_tool_sha256=hashlib.sha256(Path(__file__).with_name('emulate_cp_photo_jpeg_route.py').read_bytes()).hexdigest(),
        photo_descriptor_provenance=provenance, photo_ports_sha256=hashlib.sha256(ports).hexdigest(),
        case_count=len(cases), cases=cases,
        findings=[
            'The word zeroed at photo builder 0x480d82 becomes first-descriptor-base+0x1bc in the lower driver',
            'Original b97516 calls original b98e04 only when the per-resource cache at 0x19ca038 has bit21 set',
            'An enabled main output with tail word exactly 1 switches main output mode from 1 to 0 and calls two resource-transition helpers',
            'Original packing changes the emulator register from 0x11111 to 0x11110 while preserving main output enable bit16',
            'Unactivated resources or tail word 2 preserve the normal route; transition/binding failures and invalid crop stop before register publication'],
        limitations=[
            'Hardware activation bit21 is a fixture, not proof of live MLUT readiness or successful User3D loading',
            'Resource-transition and binding helpers are fixtures with tested return-code propagation',
            'MMIO base pointers are replaced only inside the emulator; no real device registers are accessed',
            'Native crop/alignment checks execute for one synthetic full-resolution photo descriptor set',
            'No JPEG pixels or final camera image are produced; a guarded hardware experiment is not yet installed'])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f'{len(cases)} original-driver cases passed; main MLUT flag is consumed conditionally on separate driver activation.')


if __name__ == '__main__':
    main()
