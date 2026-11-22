"""Build a relocatable raw-code experiment for two exact constructor calls.

This emits an offline ELF/raw image and original-code guards. It has no device
entry point, USB runner, installer, service module or imaging output setter.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess

from elftools.elf.elffile import ELFFile
from emulate_lut_lifecycle import AP_SHA256, Firmware

ROOT=Path(__file__).resolve().parents[1]
SOURCES=('photo_lut_context_adapter.c','photo_lut_context_adapter.S','photo_lut_context_adapter.ld')
SYMBOLS=('bridge_eligibility_entry','bridge_resolver_entry','bridge_eligibility_core',
         'bridge_resolver_core','bridge_config')
GUARD_RANGES=((0x334e1a4,0x240),(0x3216168,0x50),(0x3215fc0,0x1a8),
              (0x32862fc,12),(0x34f3ce0,0x50)) + tuple((a,20) for a in
              (0x2f88d28,0x2f6f630,0x2f75aa0,0x2f77a54,0x2f77a7c,
               0x2f770cc,0x2f7b974,0x2f929b8,0x2f929e0,0x2f92a08,0x2f92a30,0x2f928c8))

def sha(data): return hashlib.sha256(data).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('ap',type=Path);p.add_argument('--compiler',type=Path,required=True)
    p.add_argument('--out-dir',type=Path,required=True);args=p.parse_args()
    if args.out_dir.exists():p.error('Output directory exists')
    firmware=Firmware(args.ap)
    compiler=args.compiler.resolve(strict=True)
    version=subprocess.check_output([str(compiler),'version'],text=True).strip()
    if version!='0.13.0':p.error('Pinned compiler is Zig 0.13.0')
    out=args.out_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    source=out/'source';source.mkdir()
    for name in SOURCES:shutil.copyfile(ROOT/'tools/native'/name,source/name)
    shutil.copyfile(Path(__file__),source/Path(__file__).name)
    command=[str(compiler),'cc','-target','aarch64-freestanding-none','-O2','-mcpu=generic',
        '-mno-outline-atomics','-mgeneral-regs-only','-mstrict-align','-ffreestanding',
        '-fno-builtin','-fno-stack-protector','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
        '-nostdlib','-fpie','-no-pie','-Wall','-Wextra','-Werror','-Wl,--no-gc-sections',
        '-Wl,-T,photo_lut_context_adapter.ld','-Wl,-e,bridge_eligibility_entry',
        '-Wl,--build-id=none','-Wl,-z,noexecstack',
        'photo_lut_context_adapter.S','photo_lut_context_adapter.c','-o',str(out/'adapter.elf')]
    subprocess.run(command,cwd=source,check=True)
    raw=(out/'adapter.elf').read_bytes();elf=ELFFile(io.BytesIO(raw))
    if elf['e_machine']!='EM_AARCH64' or elf['e_type']!='ET_EXEC':raise ValueError('ELF type')
    if any(s['sh_type'] in ('SHT_REL','SHT_RELA','SHT_DYNAMIC') for s in elf.iter_sections()):
        raise ValueError('Unexpected relocations or dynamic section')
    symbols={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    if symbols['bridge_config']!=0x4000:raise ValueError('Config offset')
    if elf['e_entry']!=symbols['bridge_eligibility_entry']:raise ValueError('Entry point')
    undefined=[s.name for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s.name and s['st_shndx']=='SHN_UNDEF']
    if undefined:raise ValueError(f'Undefined imports: {undefined}')
    code=bytearray()
    for segment in elf.iter_segments():
        if segment['p_type']!='PT_LOAD' or not segment['p_filesz']:continue
        start=segment['p_vaddr'];end=start+segment['p_filesz']
        if end>0x4000:raise ValueError('Image exceeds code window')
        if len(code)<end:code.extend(bytes(end-len(code)))
        code[start:end]=segment.data()
    (out/'adapter.bin').write_bytes(code)
    manifest=dict(schema='a6700-lab/photo-context-adapter-build/1',version='0.1.0',
        purpose='Offline compiled constructor adaptation; no installer or imaging output override',
        ap_sha256=AP_SHA256,compiler_version=version,compiler_sha256=sha(compiler.read_bytes()),
        command=command,elf_sha256=sha(raw),code_sha256=sha(code),code_bytes=len(code),
        config_offset=0x4000,config_bytes=64,mapping_bytes=0x5000,
        symbols={n:symbols[n] for n in SYMBOLS},
        sources={n:sha((source/n).read_bytes()) for n in SOURCES},
        builder_sha256=sha(Path(__file__).read_bytes()),
        sites=[dict(va=0x334e224,helper=0x3216168,entry='bridge_eligibility_entry'),
               dict(va=0x334e330,helper=0x3215fc0,entry='bridge_resolver_entry')],
        guards=[dict(va=a,bytes=n,sha256=sha(firmware.bytes_at(a,n))) for a,n in GUARD_RANGES],
        frame_record=dict(offset=0x58,bytes=16,frame_bytes=0x70,object_register='x19',
                          caller_frame_register='x29',frame_equals_sp_required=True),
        general_mode_getter=0x2f88d28,general_mode_getter_binding='static source +0x6fc producer/consumer; physical getter value not sampled by ABI3',
        entry_point_is_service_module=False,device_access=False,camera_tested=False,
        cp_mlut_ready_verified=False,still_jpeg_verified=False)
    (out/'build.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(build=str(out),code_bytes=len(code),elf_sha256=manifest['elf_sha256'],camera_access=False)))

if __name__=='__main__':main()
