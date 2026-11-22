"""Rebuild the failed CP photo observer for offline replay."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM
from unicorn import UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE
from unicorn.arm_const import (UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0,
                              UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

from emulate_lut_parameter_helpers import CP_BASE, CP_SHA256, check_hash


NATIVE = Path(__file__).resolve().parent / 'native'
CODE = 0x10F000
TRACE = 0x110000
CODE_END = TRACE
CODE_SECTIONS = {
    '.text': (CODE, CODE_END),
}
TRACE_BYTES = 512
BOOT_HOOK = 0x108728
SITES = (
    (0x351300, 'c4f8c000', 'pp1_desc_hook', False),
    (0x4DC86E, '65f069fd', 'pp1_submit_hook', True),
    (0xB6707A, '45f2d821', 'pp1_irq_hook', False),
    (0xB66FC2, '0546fdb1', 'pp1_wait_hook', False),
    (0x480F74, '9df84c00', 'pp1_photo_hook', False),
    (0x55A664, 'f0b503af', 'pp1_query_hook', False),
)

LINKER = """ENTRY(pp1_query_hook)
SECTIONS {
    .text 0x10f000 : { KEEP(*(.text.pp1_boot)) KEEP(*(.text.pp1_hooks)) *(.text*) *(.rodata*) }
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) }
}
desc_next = 0x351305;
original_user3d = 0x542345;
irq_next = 0xb6707f;
wait_error = 0xb66fc7;
wait_success = 0xb67007;
photo_next = 0x480f79;
query_next = 0x55a669;
original_boot_clear = 0x108c54;
original_boot_map = 0x108768;
original_boot_finish = 0x108070;
"""


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def binding(path):
    raw = path.read_bytes()
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def image_slice(image, start, count):
    require(CP_BASE <= start and start - CP_BASE + count <= len(image), 'Image slice out of bounds')
    return image[start - CP_BASE:start - CP_BASE + count]


def thumb_branch(site, target, link):
    displacement = target - (site + 4)
    require(displacement % 2 == 0 and -(1 << 24) <= displacement < (1 << 24),
            'Hook is outside the Thumb branch span')
    sign = (displacement >> 24) & 1
    j1 = 1 ^ sign ^ ((displacement >> 23) & 1)
    j2 = 1 ^ sign ^ ((displacement >> 22) & 1)
    return struct.pack('<HH', 0xF000 | (sign << 10) | ((displacement >> 12) & 0x3FF),
                       (0xD000 if link else 0x9000) | (j1 << 13) | (j2 << 11)
                       | ((displacement >> 1) & 0x7FF))


def boot_mapping(image, edits, boot):
    require(image_slice(image, 0x108388, 4) == struct.pack('<I', 0x108D94),
            'Stock boot/stack boundary changed')
    vm = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    vm.mem_map(CP_BASE, (len(image) + 4095) & ~4095)
    vm.mem_write(CP_BASE, image)
    for edit in edits:
        vm.mem_write(edit['address'], edit['after'])
    root = 0x1AC4000  # Synthetic aligned L1 storage, separate from the mapped image.
    coarse, stack = root + 0x4000, 0x20000000
    vm.mem_map(root, 0x8000)
    vm.mem_map(stack, 0x4000)
    vm.mem_protect(CP_BASE, (len(image) + 4095) & ~4095, UC_PROT_READ | UC_PROT_EXEC)
    vm.mem_protect(CP_BASE, 0x1000, UC_PROT_READ | UC_PROT_WRITE | UC_PROT_EXEC)
    vm.mem_protect(TRACE, 0x1000, UC_PROT_READ | UC_PROT_WRITE)
    vm.mem_write(TRACE, bytes([0xA5]) * TRACE_BYTES)
    seen = []

    def word(address):
        return struct.unpack('<I', vm.mem_read(address, 4))[0]

    def store(address, value):
        vm.mem_write(address, struct.pack('<I', value))

    def boot_call(uc, address, size, user):
        if address in (boot, 0x108C54, 0x108768, 0x108070):
            seen.append(address)
        if address == 0x108070:
            # The unchanged CP15 hardware setup is outside this slice.
            assert uc.reg_read(UC_ARM_REG_LR) == BOOT_HOOK + 4
            uc.reg_write(UC_ARM_REG_PC, BOOT_HOOK + 4)

    # The real mapper uses original ARM instructions. Only its previously
    # constructed L1/L2 storage and inputs to the pre-MMU slice are seeded.
    vm.mem_write(root, b''.join(struct.pack('<I', (n << 20) | 0x10C12) for n in range(4096)))
    store(0x108F1C, root)
    store(0x108D98, coarse)
    store(0x108D9C, 0)
    for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3),
                               (0x108000, 0x800, 0x108000, 2), strict=True):
        vm.reg_write(register, value)
    vm.reg_write(UC_ARM_REG_SP, stack + 0x2000)
    vm.hook_add(UC_HOOK_CODE, boot_call)
    vm.emu_start(BOOT_HOOK, BOOT_HOOK+4, timeout=200_000, count=20_000)
    require(vm.reg_read(UC_ARM_REG_PC) == BOOT_HOOK+4 and seen == [boot, 0x108C54, 0x108768, 0x108768, 0x108070]
            and bytes(vm.mem_read(TRACE, TRACE_BYTES)) == bytes(TRACE_BYTES), 'Cold-boot clear or mapper failed')
    table = word(root + (CODE >> 20) * 4)
    entry = word((table & ~0x3FF) + ((CODE >> 12) & 0xFF) * 4)
    trace_entry = word((table & ~0x3FF) + ((TRACE >> 12) & 0xFF) * 4)
    require(table & 3 == 1 and entry & 3 == 2 and entry & (1 << 9)
            and entry & 0xFFFFF000 == CODE, f'Observer code page is not mapped RX: {entry:#x}')
    require(trace_entry & 3 == 3 and not trace_entry & (1 << 9)
            and trace_entry & 0xFFFFF000 == TRACE, f'Observer RAM page is not mapped RW/XN: {trace_entry:#x}')
    return dict(native_boot_clear_and_original_mapper_replayed=True,
                boot_hook_address=hex(BOOT_HOOK), recorder_page=hex(CODE), capture_page=hex(TRACE),
                native_code_page_descriptor=hex(entry), native_capture_page_descriptor=hex(trace_entry),
                zeroed_bytes=TRACE_BYTES)


def material(image, elf_bytes):
    check_hash(image, CP_SHA256)
    elf = ELFFile(io.BytesIO(elf_bytes))
    require(elf['e_machine'] == 'EM_ARM' and elf['e_type'] == 'ET_EXEC', 'Wrong CP ELF')
    require(not any(s['sh_size'] and s['sh_type'] in ('SHT_REL', 'SHT_RELA', 'SHT_DYNAMIC')
                    for s in elf.iter_sections()), 'Unexpected CP link dependency')
    sections = [elf.get_section_by_name(name) for name in CODE_SECTIONS]
    require({s.name for s in elf.iter_sections() if s['sh_flags'] & 2} == {s.name for s in sections},
            'Unexpected allocated CP section')
    code_spans = tuple(CODE_SECTIONS.values())
    for section, (start, end) in zip(sections, code_spans, strict=True):
        require(section['sh_addr'] == start and 0 < section['sh_size'] <= end - start,
                'Observer code exceeds its fixed region')
        require(image_slice(image, start, end-start) == bytes(end-start), 'Nonzero CP code bytes')
    require(image_slice(image, CODE, CODE_END - CODE) == bytes(CODE_END - CODE), 'Nonzero CP boot padding')
    require(image_slice(image, TRACE, TRACE_BYTES) == bytes(TRACE_BYTES), 'Nonzero CP file-tail bytes')
    symtab = elf.get_section_by_name('.symtab')
    require(symtab is not None, 'CP ELF symbol table required')
    require(not any(s.name and s['st_shndx'] == 'SHN_UNDEF' for s in symtab.iter_symbols()),
            'Undefined CP observer symbol')
    names = {row.name: row['st_value'] for row in symtab.iter_symbols() if row.name.startswith(('pp1_', 'cp_pp1_'))}
    require(set(names) == {site[2] for site in SITES} | {'cp_pp1_record', 'cp_pp1_query', 'pp1_boot_hook'}
            and all((start <= (value & ~1) < end) for value in names.values() for start, end in code_spans)
            and all(bool(value & 1) == (name != 'pp1_boot_hook') for name, value in names.items()),
            'CP observer symbols are outside fixed text')
    require(elf['e_entry'] == names['pp1_query_hook'], 'CP query entry differs')
    dis = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    writes = []
    for site, before, symbol, link in SITES:
        original = bytes.fromhex(before)
        require(image_slice(image, site, 4) == original, f'Wrong CP original at {site:#x}')
        target = names[symbol] & ~1
        branch = thumb_branch(site, target, link)
        decoded = next(dis.disasm(branch, site))
        require(decoded.mnemonic == ('bl' if link else 'b.w') and int(decoded.op_str[1:], 0) == target,
                'Wrong CP branch encoding')
        writes.append(dict(address=site, before=original, after=branch, event=symbol))
    original = image_slice(image, BOOT_HOOK, 4)
    require(original.hex() == '50feffeb', 'Original boot finalization call changed')
    delta = names['pp1_boot_hook'] - (BOOT_HOOK+8)
    require(delta % 4 == 0 and -(1 << 25) <= delta < (1 << 25), 'Boot hook branch is out of range')
    replacement = struct.pack('<I', 0xEB000000 | ((delta >> 2) & 0xFFFFFF))
    writes.append(dict(address=BOOT_HOOK, before=original, after=replacement, event='pp1_boot_hook'))
    for section in sections:
        start, length = section['sh_addr'], section['sh_size']
        writes.append(dict(address=start, before=image_slice(image, start, length), after=section.data(), event=section.name))
    return writes, names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cp', required=True, type=Path, help='Locally extracted stock cpapp-b.bin')
    parser.add_argument('--compiler', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    require(not args.out.exists(), 'Use a new evidence directory')
    compiler = args.compiler.resolve(strict=True)
    require(subprocess.check_output([str(compiler), 'version'], text=True).strip() == '0.13.0', 'Zig 0.13.0 required')
    image = args.cp.read_bytes()
    check_hash(image, CP_SHA256)
    sources = tuple(NATIVE / f'cp_pp1_capture.{suffix}' for suffix in ('c', 'S'))
    with tempfile.TemporaryDirectory(prefix='cp-pp1-capture-') as temp:
        staging = Path(temp)
        for file in sources:
            shutil.copyfile(file, staging / file.name)
        (staging / 'capture.ld').write_text(LINKER, encoding='ascii', newline='\n')
        flags = ['cc', '-target', 'thumb-freestanding-eabi', '-mcpu=cortex_a9', '-mthumb',
                 '-mfloat-abi=soft', '-mfpu=none', '-Oz', '-g', '-ffreestanding', '-fno-builtin',
                 '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                 '-Wall', '-Wextra', '-Werror', '-nostdlib', '-static', '-Wl,--build-id=none',
                 '-Wl,-e,pp1_query_hook', '-Wl,-T,capture.ld', *(file.name for file in sources),
                 '-o', 'capture.elf']
        subprocess.run([str(compiler), *flags], cwd=staging, check=True)
        edits, symbols = material(image, (staging / 'capture.elf').read_bytes())
        placement = boot_mapping(image, edits, symbols['pp1_boot_hook'])
        args.out.mkdir(parents=True)
        for name in ('capture.elf', 'capture.ld'):
            shutil.copyfile(staging / name, args.out / name)
    report = dict(schema='d6700/CP-PP1-local-observer/1', camera_access=False,
                  installable_candidate=False, CP_stock_sha256=CP_SHA256,
                  observer_code_bytes=sum(len(e['after']) for e in edits if e['event'].startswith('.text')),
                  capture_address=TRACE,
                  capture_bytes=TRACE_BYTES, native_text_mapping_replay=placement,
                  symbols=symbols, edits=[dict(address=e['address'], bytes=len(e['after']),
                      original_sha256=hashlib.sha256(e['before']).hexdigest(),
                      replacement_sha256=hashlib.sha256(e['after']).hexdigest(),
                      event=e['event']) for e in edits],
                  sources={p.name: binding(p) for p in (Path(__file__), *sources)},
                  compiler_arguments=flags, files={n: binding(args.out / n) for n in ('capture.elf', 'capture.ld')})
    with (args.out / 'build.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(code_bytes=report['observer_code_bytes'], native_text_mapping=placement)))


if __name__ == '__main__':
    main()
