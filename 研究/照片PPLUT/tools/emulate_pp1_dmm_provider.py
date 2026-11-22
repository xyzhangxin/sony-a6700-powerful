"""Replay the A6700 DMM provider's Share/Open/Free/Close path in local RAM."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM, UC_PROT_EXEC, UC_PROT_READ
import unicorn.arm64_const as arm


DMM_SHA256 = 'cf4a2fd966832ced14c06e7d256bc9ccb16d24bc5dc5b0cea367e3cf7cadc0ce'
TEXT, RODATA, TABLE_ROOT, UNIT = 0x1000000, 0x1100000, 0x1200000, 0x1210000
EXTENT, DATA, STACK, RETURN = 0x1220000, 0x1300000, 0x1400000, 0x1500000
REQUEST, REPLY, EXTENTS = DATA, DATA + 0x100, DATA + 0x200
EXTERNAL = RETURN + 0x100
UNIT_INDEX, HANDLE_GENERATION = 1, 3
HANDLE = HANDLE_GENERATION << 10 | UNIT_INDEX
ENTRY = UNIT + 0x70 * UNIT_INDEX
ASSET_TYPE = 1578
EXTENT_BASE, EXTENT_OFFSET, EXTENT_LENGTH = 0x12340000, 0x10000, 48128
DISPATCH = {0x21: 0x59E8, 0x23: 0x5148, 0x25: 0x50D0}
UNIT_FUNCTIONS = ('dmm_unit_ctrl_Share', 'dmm_unit_ctrl_Open', 'dmm_unit_ctrl_Close', 'dmm_unit_ctrl_Free')
EXTERNAL_SYMBOLS = ('dmm_log_SetUnitAccInfo', 'dmm_ext_ctrl_FreeExtent')


def check(ok, message):
    if not ok:
        raise ValueError(message)


def symbol(elf, name):
    entry, = elf.get_section_by_name('.symtab').get_symbol_by_name(name)
    check(elf.get_section(entry['st_shndx']).name == '.text', f'{name} is outside .text')
    return entry['st_value'], entry['st_size']


def relocations(elf, name):
    symbols = elf.get_section_by_name('.symtab')
    for relocation in elf.get_section_by_name(name).iter_relocations():
        entry = symbols.get_symbol(relocation['r_info_sym'])
        index = entry['st_shndx']
        section = elf.get_section(index).name if isinstance(index, int) else index
        yield relocation['r_offset'], entry.name, section, relocation['r_addend']


class Provider:
    def __init__(self, raw):
        check(hashlib.sha256(raw).hexdigest() == DMM_SHA256, 'Original dmm.ko changed')
        elf = ELFFile(io.BytesIO(raw))
        text = bytearray(elf.get_section_by_name('.text').data())
        rodata = elf.get_section_by_name('.rodata').data()
        code_relocations = {at: (name, section, addend)
                            for at, name, section, addend in relocations(elf, '.rela.text')}
        table_reference = code_relocations[0x6B10]
        check(table_reference == ('', '.rodata', 0x280), 'DMM synchronous command table changed')
        table = {at: (name, section, addend) for at, name, section, addend in
                 relocations(elf, '.rela.rodata')}
        for command, entry in DISPATCH.items():
            check(table[0x280 + command * 8] == ('', '.text', entry), 'DMM command handler changed')

        functions = {name: symbol(elf, name) for name in UNIT_FUNCTIONS}
        self.native_entries = {at: name for name, (at, _) in functions.items()}
        ranges = ((0x4D40, 0x4DE4), (0x5280, 0x52D8), (0x50D0, 0x5148), (0x5148, 0x51FC),
                  (0x59E8, 0x5AB8), *((at, at + size) for at, size in functions.values()))
        self.instructions = {address for start, end in ranges
                             for address in range(TEXT + start, TEXT + end, 4)}
        external_targets = dict(zip(EXTERNAL_SYMBOLS, (EXTERNAL, EXTERNAL + 4)))
        self.external_calls = {address: name for name, address in external_targets.items()}
        for at, (name, section, addend) in code_relocations.items():
            if TEXT + at not in self.instructions:
                continue
            target = external_targets.get(name)
            if target is None:
                check(section == '.text' and name in UNIT_FUNCTIONS and not addend,
                      f'Unexpected DMM code relocation at {at:#x}')
                target = TEXT + functions[name][0]
            delta = target - (TEXT + at)
            check(struct.unpack_from('<I', text, at)[0] == 0x94000000 and delta % 4 == 0
                  and -(1 << 27) <= delta < (1 << 27), 'Unexpected AArch64 call relocation')
            struct.pack_into('<I', text, at, 0x94000000 | ((delta >> 2) & 0x3FFFFFF))
        for at in (0x8EA0, 0x9350, 0x9450, 0x95C0):
            check(code_relocations[at] == ('', '.bss', 0x165C90), 'DMM unit table reference changed')
            struct.pack_into('<Q', text, at, TABLE_ROOT)
        check(code_relocations[0x9458][:2] == ('', '.rodata'), 'DMM Close jump table changed')
        struct.pack_into('<Q', text, 0x9458, RODATA + code_relocations[0x9458][2])
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.map_data(TEXT, bytes(text), executable=True)
        self.map_data(RODATA, rodata)
        for at in (TABLE_ROOT, UNIT, EXTENT, DATA, STACK, RETURN):
            self.uc.mem_map(at, 4096)
        self.uc.mem_protect(RETURN, 4096, UC_PROT_READ | UC_PROT_EXEC)
        self.write(TABLE_ROOT + 0x20, 'Q', UNIT)
        self.write(ENTRY, 'I', ASSET_TYPE)
        self.write(ENTRY + 8, '4BHHQ', 1, 1, 0, HANDLE_GENERATION, 2, 1, EXTENT)
        self.write(EXTENT + 8, 'QII', EXTENT_BASE, EXTENT_OFFSET, EXTENT_LENGTH)
        self.effects, self.calls = [], []
        self.uc.hook_add(UC_HOOK_CODE, self.on_code)

    def map_data(self, address, data, *, executable=False):
        size = (len(data) + 4095) & ~4095
        self.uc.mem_map(address, size)
        self.uc.mem_write(address, data)
        self.uc.mem_protect(address, size, UC_PROT_READ | (UC_PROT_EXEC if executable else 0))

    def write(self, address, shape, *values):
        self.uc.mem_write(address, struct.pack('<' + shape, *values))

    def read(self, address, shape):
        layout = struct.Struct('<' + shape)
        return layout.unpack(bytes(self.uc.mem_read(address, layout.size)))

    def state(self):
        shares, opens, _, generation, state, extents = self.read(ENTRY + 8, '4BHH')
        return dict(shares=shares, opens=opens, generation=generation, state=state, extents=extents)

    def on_code(self, uc, address, size, user):
        offset = address - TEXT
        if offset in self.native_entries:
            self.calls.append(self.native_entries[offset])
        if address in self.external_calls:
            name = self.external_calls[address]
            self.effects.append(dict(fixture=name, extent=hex(uc.reg_read(arm.UC_ARM64_REG_X0))
                                     if name.endswith('FreeExtent') else None))
        elif offset == 0x8988:
            self.effects.append(dict(fixture='DMM_unit_unlink', native_entry=hex(offset)))
        else:
            check(address in self.instructions, f'Unreviewed DMM execution at .text+{offset:#x}')
            return
        uc.reg_write(arm.UC_ARM64_REG_X0, 0)
        uc.reg_write(arm.UC_ARM64_REG_PC, uc.reg_read(arm.UC_ARM64_REG_LR))

    def handle_request(self, raw):
        check(len(raw) == 0xD8, 'Unexpected DMM request size')
        command, = struct.unpack_from('<H', raw)
        check(command in DISPATCH, 'DMM command is outside this replay')
        self.effects, self.calls = [], []
        self.uc.mem_write(DATA, bytes(0x400))
        # The stock dispatcher copies the 216-byte packet after 12 internal bytes.
        self.write(REQUEST + 4, 'I', 0x61003C)
        self.uc.mem_write(REQUEST + 12, raw)
        for register, value in ((arm.UC_ARM64_REG_X0, REQUEST), (arm.UC_ARM64_REG_X1, REPLY),
                                (arm.UC_ARM64_REG_X2, EXTENTS), (arm.UC_ARM64_REG_SP, STACK + 0xFF0),
                                (arm.UC_ARM64_REG_LR, RETURN)):
            self.uc.reg_write(register, value)
        self.uc.emu_start(TEXT + DISPATCH[command], RETURN, timeout=100_000, count=5000)
        check(self.uc.reg_read(arm.UC_ARM64_REG_PC) == RETURN, 'DMM handler did not return')
        reply = bytearray(self.uc.mem_read(REPLY, 0xD8))
        reply[0x18:0xD8] = self.uc.mem_read(EXTENTS, 0xC0)
        check(struct.unpack_from('<H', reply)[0] == command | 0x8000, 'DMM reply shape changed')
        return bytes(reply)

    def command(self, command, mode, *, kind=ASSET_TYPE, handle=HANDLE):
        request = bytearray(0xD8)
        struct.pack_into('<H2xII', request, 0, command, kind if command == 0x21 else 0, handle)
        struct.pack_into('<HH', request, 0x14, 0x55, mode)
        raw = self.handle_request(bytes(request))
        _, status, tag, count, detail, returned_handle = struct.unpack_from('<Hh2xHHhI', raw)
        check(tag == 0x55, 'DMM reply tag changed')
        address, size = self.read(EXTENTS, 'QI')
        return dict(command=hex(command), mode=mode, status=status, detail=detail,
                    mapped_extents=count, returned_handle=hex(returned_handle),
                    physical_address=hex(address), mapped_bytes=size,
                    unit=self.state(), native_calls=list(self.calls), effects=list(self.effects))


def replay(raw):
    def active(result, shares, opens, state):
        check(result['status'] == 0 and (
              result['unit']['shares'], result['unit']['opens'], result['unit']['state']) == (shares, opens, state),
              'Unexpected DMM unit state')

    m = Provider(raw)
    retained = m.command(0x21, 1)
    active(retained, 2, 2, 2)
    check(retained['returned_handle'] == hex(HANDLE) and retained['mapped_extents'] == 1
          and retained['physical_address'] == hex(EXTENT_BASE + EXTENT_OFFSET)
          and retained['mapped_bytes'] == EXTENT_LENGTH, 'AllocShare did not return the existing handle and extent')
    source_released = m.command(0x23, 2)
    active(source_released, 1, 1, 2)
    check([call for call in source_released['native_calls']] == ['dmm_unit_ctrl_Close', 'dmm_unit_ctrl_Free']
          and not any(e['fixture'].endswith('FreeExtent') for e in source_released['effects']),
          'Retained extent was freed prematurely')
    pair_released = m.command(0x23, 2)
    active(pair_released, 0, 0, 0)
    check(any(e['fixture'].endswith('FreeExtent') for e in pair_released['effects']), 'Last pair did not reach deallocation')
    cases = [dict(name='share_and_open_pin_unit_until_both_references_are_released',
                  states=[retained, source_released, pair_released])]

    m = Provider(raw)
    retained = m.command(0x21, 1)
    close_only = m.command(0x25, 0)
    active(close_only, 2, 1, 2)
    check(close_only['native_calls'] == ['dmm_unit_ctrl_Close'], 'Close changed the shared reference count')
    free_only = m.command(0x23, 0)
    active(free_only, 1, 1, 2)
    check(free_only['native_calls'] == ['dmm_unit_ctrl_Free'], 'Free-only unexpectedly closed a mapping')
    cases.append(dict(name='Close_releases_mapping_only_Free_releases_share',
                      states=[retained, close_only, free_only]))

    m = Provider(raw)
    m.write(ENTRY + 9, 'B', 0xFF)
    failure = m.command(0x21, 1)
    check(failure['status'] != 0 and not failure['mapped_extents'] and failure['unit']['shares'] == 2
          and failure['unit']['opens'] == 0xFF and failure['native_calls'] ==
          ['dmm_unit_ctrl_Share', 'dmm_unit_ctrl_Open'], 'Failed mapping lost its partial share side effect')
    cases.append(dict(name='share_increment_survives_mapping_failure', result=failure))

    for name, kwargs in (('stale_unit_generation', dict(handle=HANDLE ^ (1 << 10))),
                         ('unexpected_asset_type', dict(kind=ASSET_TYPE + 1))):
        m = Provider(raw)
        state = m.state()
        rejected = m.command(0x21, 1, **kwargs)
        check(rejected['status'] != 0 and rejected['unit'] == state and rejected['native_calls'] ==
              ['dmm_unit_ctrl_Share'], 'Rejected asset changed an existing shared unit')
        cases.append(dict(name=name, result=rejected))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dmm', type=Path, required=True, help='Locally extracted dmm.ko')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    output = args.out
    if output.exists():
        parser.error('Use a new output file')
    cases = replay(args.dmm.read_bytes())
    report = dict(schema='d6700/PP1-DMM-provider-replay/1',
                  recorded_utc=datetime.now(timezone.utc).isoformat(), result='passed',
                  camera_access=False, firmware_write=False, case_count=len(cases),
                  AP_DMM_module_sha256=DMM_SHA256, source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  dispatch={hex(command): hex(entry) for command, entry in DISPATCH.items()}, cases=cases,
                  limits=['AArch64 command handlers and unit bookkeeping run from dmm.ko with synthetic unit/extent entries.',
                          'The OS message dispatcher, provider lock, live mappings, extent free and unit unlink use fixtures.',
                          'This proves the local AP provider code paths. Routing of the CP LUT allocation to this provider '
                          'and protection from concurrent payload reuse remain to be verified.'])
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(f'{len(cases)} original DMM provider scenarios passed')


if __name__ == '__main__':
    main()
