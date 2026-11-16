"""Build a Thumb offline state coordinator, with no hardware execution command."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
from elftools.elf.elffile import ELFFile

HERE = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists():parser.error('Output exists')
    compiler=args.compiler.resolve(strict=True)
    assert subprocess.check_output([str(compiler),'version'],text=True).strip()=='0.13.0'
    args.out.mkdir(parents=True)
    for name in ('photo_pp1_lut_coordinator.c','photo_pp1_lut_coordinator.ld'):
        shutil.copyfile(HERE/'native'/name,args.out/name)
    command=[str(compiler),'cc','-target','thumb-freestanding-eabi','-mcpu=cortex_a9','-mthumb',
        '-mfloat-abi=soft','-mfpu=none','-fno-vectorize','-fno-slp-vectorize',
        '-Os','-g','-fno-lto','-fno-pic','-fno-pie','-static','-ffreestanding','-fno-builtin','-fno-stack-protector',
        '-fno-unwind-tables','-fno-asynchronous-unwind-tables','-nostdlib','-Wall','-Wextra','-Werror',
        '-Wl,--build-id=none','-Wl,--no-gc-sections',
        '-Wl,-e,pp1_bind','-Wl,-T,photo_pp1_lut_coordinator.ld','photo_pp1_lut_coordinator.c','-o','coordinator.elf']
    completed=subprocess.run(command,cwd=args.out,capture_output=True,text=True)
    if completed.returncode:
        print(completed.stdout+completed.stderr)
        raise RuntimeError('Offline coordinator compilation failed')
    elf=ELFFile(io.BytesIO((args.out/'coordinator.elf').read_bytes()))
    assert elf['e_machine']=='EM_ARM' and elf['e_type']=='ET_EXEC' and elf.elfclass==32 and elf.little_endian
    assert not any(section['sh_type'] in ('SHT_REL','SHT_RELA','SHT_DYNAMIC') for section in elf.iter_sections())
    loads=[segment for segment in elf.iter_segments() if segment['p_type']=='PT_LOAD']
    assert loads and all(segment['p_flags'] in (5,6) for segment in loads)
    assert all(0x12000000<=segment['p_vaddr']<0x12011000 for segment in loads)
    symbols={symbol.name:int(symbol['st_value']) for section in elf.iter_sections()
        if section['sh_type']=='SHT_SYMTAB' for symbol in section.iter_symbols() if symbol.name.startswith('pp1_')}
    assert len(symbols)==14 and all(address&1 for address in symbols.values())
    manifest=dict(schema='d6700/photo-PP1-private-coordinator-offline/1',camera_access=False,
        installable_candidate=False,hardware_entrypoint=None,observation_bytes=92,private_state_bytes=56,
        serialization='Caller must serialize; no implicit mutex',
        receipt_scope='Explicit native-boundary receipts supplied by the offline harness; real drain not proven',
        original_shared_upper_cache_used=False,host_USB_engine_added=False,
        compiler_sha256=sha(compiler),compiler_version='0.13.0',build_command=command,symbols=symbols,
        files={name:dict(bytes=(args.out/name).stat().st_size,sha256=sha(args.out/name)) for name in
            ('coordinator.elf','photo_pp1_lut_coordinator.c','photo_pp1_lut_coordinator.ld')},
        builder_sha256=sha(Path(__file__)),lab_memory_only=True)
    with (args.out/'manifest.json').open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(manifest,stream,indent=2);stream.write('\n')
    print(json.dumps(dict(result='offline_coordinator_built',manifest_sha256=sha(args.out/'manifest.json'),
        ELF_sha256=sha(args.out/'coordinator.elf'),camera_access=False,installable_candidate=False)))

if __name__=='__main__':main()
