"""Bounded FL23 A1 container packer, shared with the verified project loader.

Build time only; uses pyelftools, never a device or personal input.
"""
import hashlib
import io
import struct
from elftools.elf.elffile import ELFFile
HEADER=struct.Struct('<8sIIQQQIIQQQQ32s16s')
SEGMENT=struct.Struct('<QQQQII')
RELOCATION=struct.Struct('<QQ')

def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(value, size=4096):
    return (value + size - 1) & -size


def pack(raw, entry_symbol):
    elf = ELFFile(io.BytesIO(raw))
    if (elf['e_type'], elf['e_machine'], elf.elfclass, elf.little_endian) != ('ET_DYN', 'EM_AARCH64', 64, True):
        raise ValueError('Expected little-endian AArch64 ET_DYN')
    if any(s['p_type'] in ('PT_INTERP', 'PT_TLS') for s in elf.iter_segments()):
        raise ValueError('Interpreter and TLS are outside the A1 loader ABI')
    symbols = elf.get_section_by_name('.symtab')
    if symbols is None:
        raise ValueError('Explicit function symbol table required')
    entries = [s for s in symbols.iter_symbols() if s.name == entry_symbol
               and s['st_info']['type'] == 'STT_FUNC' and s['st_size']]
    if len(entries) != 1:
        raise ValueError('Expected one explicit nonempty function entry')
    entry = entries[0]['st_value']
    dynsym = elf.get_section_by_name('.dynsym')
    if dynsym and any(s.name and s['st_shndx'] == 'SHN_UNDEF' for s in dynsym.iter_symbols()):
        raise ValueError('Unresolved dynamic symbols are unsupported')
    dynamic = elf.get_section_by_name('.dynamic')
    if dynamic:
        forbidden = {'DT_NEEDED', 'DT_TEXTREL', 'DT_RELR', 'DT_RELRSZ', 'DT_RELRENT', 'DT_JMPREL', 'DT_REL'}
        if any(t.entry.d_tag in forbidden for t in dynamic.iter_tags()):
            raise ValueError('Unsupported dynamic-linker requirement')
    loads = sorted([s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD' and s['p_memsz']],
                   key=lambda s: s['p_vaddr'])
    if not 1 <= len(loads) <= 8 or loads[0]['p_vaddr']:
        raise ValueError('Expected at most eight segments linked from zero')
    memory = align(max(s['p_vaddr'] + s['p_memsz'] for s in loads))
    if not 0 < memory <= 8 * 1024 * 1024:
        raise ValueError('A1 memory bound exceeded')
    end = 0
    for s in loads:
        if (s['p_flags'] not in (4, 5, 6) or s['p_filesz'] > s['p_memsz']
                or (s['p_vaddr'] & ~4095) < align(end)
                or s['p_offset'] + s['p_filesz'] > len(raw)):
            raise ValueError('Unsupported permissions, overlapping pages or truncated segment')
        end = s['p_vaddr'] + s['p_memsz']
    if entry & 3 or not any(s['p_flags'] & 1 and s['p_vaddr'] <= entry
                           and entry + 4 <= s['p_vaddr'] + s['p_filesz'] for s in loads):
        raise ValueError('Entry is outside file-backed executable bytes')
    relocations = []
    for section in elf.iter_sections():
        if section['sh_type'] in ('SHT_REL', 'SHT_RELR'):
            raise ValueError('Only explicit RELA is supported')
        if section['sh_type'] != 'SHT_RELA':
            continue
        for r in section.iter_relocations():
            if r['r_info_type'] != 1027 or r['r_info_sym']:
                raise ValueError('Only symbol-free R_AARCH64_RELATIVE is supported')
            at, add = r['r_offset'], r['r_addend']
            if (at & 7 or not any(s['p_flags'] & 2 and s['p_vaddr'] <= at
                                 and at + 8 <= s['p_vaddr'] + s['p_memsz'] for s in loads)
                    or not any(s['p_vaddr'] <= add < s['p_vaddr'] + s['p_memsz'] for s in loads)):
                raise ValueError('Relocation target or addend outside load segments')
            relocations.append((at, add))
    relocations.sort()
    if len(relocations) > 64 or len(set(a for a, _ in relocations)) != len(relocations):
        raise ValueError('Excessive or duplicate relocations')
    reloc_at = HEADER.size + len(loads) * SEGMENT.size
    payload_at = align(reloc_at + len(relocations) * RELOCATION.size, 16)
    image = bytearray(payload_at)
    segment_rows = []
    for i, s in enumerate(loads):
        offset = len(image)
        image.extend(s.data())
        row = (offset, s['p_vaddr'], s['p_filesz'], s['p_memsz'], s['p_flags'], 0)
        SEGMENT.pack_into(image, HEADER.size + i * SEGMENT.size, *row)
        segment_rows.append(dict(zip(('offset', 'vaddr', 'filesz', 'memsz', 'flags', 'reserved'), row)))
        if i + 1 < len(loads):
            image.extend(bytes(align(len(image), 16) - len(image)))
    for i, row in enumerate(relocations):
        RELOCATION.pack_into(image, reloc_at + i * RELOCATION.size, *row)
    HEADER.pack_into(image, 0, b'FL23A1L1', 1, HEADER.size, len(image), memory, entry,
                     len(loads), len(relocations), HEADER.size, reloc_at, payload_at, 0,
                     bytes.fromhex(sha(raw)), bytes(16))
    if len(image) > 2 * 1024 * 1024:
        raise ValueError('A1 package bound exceeded')
    package = bytes(image)
    return package, dict(version=1, elf_sha256=sha(raw), package_sha256=sha(package),
                         package_bytes=len(package), memory_bytes=memory, entry_symbol=entry_symbol,
                         entry_offset=entry, segments=segment_rows, relocations=relocations,
                         camera_access=False, installable=False)
