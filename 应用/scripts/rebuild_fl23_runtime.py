"""Rebuild the shipped frozen FL23 runtime without research data or camera.

Uses included C/assembly/link scripts, clang, aarch64-elf-ld and pyelftools.
Refuses source or generated-byte mismatch instead of silently replacing the
installed package. Build output is always a separate new directory.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile
from fl23_container import pack

ROOT=Path(__file__).resolve().parents[1]
sha=lambda data:hashlib.sha256(data).hexdigest()


def run(args,work):subprocess.run(args,cwd=work,check=True)


def compile_role(role,source,meta,output):
    clang=shutil.which('clang');linker=shutil.which('aarch64-elf-ld') or shutil.which('ld.lld')
    if not clang or not linker:raise RuntimeError('clang and an AArch64 ELF linker are required')
    with tempfile.TemporaryDirectory(prefix='powerful-fl23-'+role+'-') as tmp:
        work=Path(tmp)
        for f in source.iterdir():
            if f.is_file():shutil.copyfile(f,work/f.name)
        if role in ('resident','stage'):
            name='resident' if role=='resident' else 'stage'
            flags=['-Oz','-Wall','-Werror','-Wno-logical-op-parentheses','-ffunction-sections','-fdata-sections',
                '-fPIC','-fvisibility=hidden','-ffreestanding','-fno-builtin','-fno-stack-protector',
                '-mgeneral-regs-only','-mstrict-align','-mno-outline-atomics']
            run([clang,'-target','aarch64-none-elf',*flags,'-c',name+'.c','-o',name+'.o'],work)
            objects=[name+'.o']
            if role=='resident':
                run([clang,'-target','aarch64-none-elf','-c','trigger.S','-o','trigger.o'],work);objects.append('trigger.o')
            entry='fl23_a1_trigger_entry' if role=='resident' else 'fl23_a1_stage_entry'
            run([linker,'-T',name+'.ld','--gc-sections','--build-id=none',
                '--defsym=fl23_a1_trigger_resume='+hex(meta['trigger_resume']),'-e',entry,'-o',name+'.elf',*objects],work)
            raw=(work/(name+'.elf')).read_bytes();elf=ELFFile(io.BytesIO(raw))
            sections=[s for s in elf.iter_sections() if s['sh_flags']&2 and s['sh_size']]
            if any(s['sh_flags']&1 or s['sh_type']=='SHT_NOBITS' for s in sections):raise ValueError('Mutable flat section')
            start=min(s['sh_addr'] for s in sections);end=max(s['sh_addr']+s['sh_size'] for s in sections);blob=bytearray(end-start)
            for s in sections:blob[s['sh_addr']-start:s['sh_addr']-start+s['sh_size']]=s.data()
            generated={name+'.elf':raw,name+'.bin':bytes(blob)}
        elif role=='core' or role in ('names-module','names-bootstrap'):
            spec=meta['roles'][role]
            source_name=spec.get('source','core.c');entry=spec.get('entry','fl23_self_publish_core');stem=spec.get('stem','core')
            run([clang,'-target','aarch64-none-elf','-O2','-mgeneral-regs-only','-mstrict-align',
                '-fPIC','-fvisibility=hidden','-ffreestanding','-fno-builtin','-fno-stack-protector',
                '-I',str(work),'-c',source_name,'-o','component.o'],work)
            run([linker,'-shared','-Bsymbolic','-z','defs','-z','noexecstack','-z','max-page-size=4096',
                '--build-id=none','--entry='+entry,'-o',stem+'.elf','component.o'],work)
            raw=(work/(stem+'.elf')).read_bytes();container,_=pack(raw,entry)
            generated={stem+'.elf':raw,stem+'.a1':container}
        elif role=='worker':
            for name in ('thread','loader'):
                run([clang,'-target','aarch64-none-elf','-O2','-Wall','-Werror','-Wno-logical-op-parentheses',
                    '-fPIC','-ffreestanding','-fno-builtin','-fno-stack-protector','-mgeneral-regs-only',
                    '-mno-outline-atomics','-c',name+'.c','-o',name+'.o'],work)
            run([linker,'-shared','-Bsymbolic','-z','defs','-z','noexecstack','--build-id=none','-o','thread.elf','thread.o','loader.o'],work)
            raw=(work/'thread.elf').read_bytes();container,_=pack(raw,'fl23_a1_thread_start')
            generated={'thread.elf':raw,'thread.a1':container}
        else:raise ValueError('Unknown runtime role')
        for name,data in generated.items():
            if sha(data)!=meta['roles'][role]['outputs'][name]:raise ValueError('Rebuilt runtime differs: '+role+'/'+name)
            target=output/role;target.mkdir(exist_ok=True);(target/name).write_bytes(data)


def rebuild(destination,flags=1):
    if flags not in (1,2,3):raise ValueError('Unknown Creative Look flags')
    base=ROOT/'src/a6700_unlock/data/fl23'
    source=(base if flags==1 else base/'variants'/str(flags))/'runtime';meta=json.loads((source/'manifest.json').read_bytes())
    for name,digest in meta['files'].items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or sha((source/path).read_bytes())!=digest:raise ValueError('Runtime source differs: '+name)
    if destination.exists():raise ValueError('Choose a new output directory')
    destination.mkdir(parents=True)
    for role in ('names-module','names-bootstrap','resident','core','worker','stage'):
        if role in meta['roles']:compile_role(role,source/role,meta,destination)
    stage=(destination/'stage/stage.bin').read_bytes();receipt=bytes.fromhex(meta['receipt_hex'])
    carrier=bytes(512)+receipt+stage
    if len(receipt)!=512 or sha(carrier)!=meta['carrier_sha256']:raise ValueError('Rebuilt carrier differs')
    (destination/'carrier.bin').write_bytes(carrier)
    print(json.dumps(dict(passed=True,carrier_sha256=sha(carrier),camera_access=False)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--flags',type=int,choices=(1,2,3),default=1)
    a=p.parse_args();rebuild(a.output,a.flags)
