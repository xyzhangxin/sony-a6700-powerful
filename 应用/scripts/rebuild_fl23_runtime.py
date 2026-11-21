"""Rebuild the shipped frozen FL23 runtime without research data or camera.

Uses included C/assembly/link scripts, clang, aarch64-elf-ld and pyelftools.
Refuses source or generated-byte mismatch instead of silently replacing the
installed package. Build output is always a separate new directory.

The /setting/fl23 carrier flags select boot-resident features:
1 FL2/FL3, 2 Creative Look names, 4 custom shutter sound. Flags 1..3 are the
original single stages; 4..7 link the unchanged custom-sound stage after the
1..3 stage through one fixed sequencing wrapper (combined/wrapper.c).

``--refresh-manifest`` is a maintainer action: it recompiles the reviewed
sources and rewrites only the generated digests, receipt and carrier.bin of
that variant. Normal rebuilds never modify package data.
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
import zlib

from elftools.elf.elffile import ELFFile
from fl23_container import pack

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'src/a6700_unlock/data/fl23'
FLAGS=(1,2,3,4,5,6,7)
CUSTOM_BIT=4
CARRIER_CAPACITY=2097664
sha=lambda data:hashlib.sha256(data).hexdigest()
STAGE_FLAGS=['-Oz','-Wall','-Werror','-Wno-logical-op-parentheses','-ffunction-sections','-fdata-sections',
    '-fPIC','-fvisibility=hidden','-ffreestanding','-fno-builtin','-fno-stack-protector',
    '-mgeneral-regs-only','-mstrict-align','-mno-outline-atomics']


def run(args,work):subprocess.run(args,cwd=work,check=True)


def tools():
    clang=shutil.which('clang');linker=shutil.which('aarch64-elf-ld') or shutil.which('ld.lld')
    if not clang or not linker:raise RuntimeError('clang and an AArch64 ELF linker are required')
    return clang,linker


def objcopy_tool():
    tool=shutil.which('aarch64-elf-objcopy') or shutil.which('llvm-objcopy')
    if not tool:raise RuntimeError('aarch64-elf-objcopy or llvm-objcopy is required')
    return tool


def flat(raw):
    elf=ELFFile(io.BytesIO(raw))
    sections=[s for s in elf.iter_sections() if s['sh_flags']&2 and s['sh_size']]
    if any(s['sh_flags']&1 or s['sh_type']=='SHT_NOBITS' for s in sections):raise ValueError('Mutable flat section')
    if any(s['sh_type'] in ('SHT_REL','SHT_RELA','SHT_RELR') and s['sh_size'] for s in sections):raise ValueError('Flat stage relocation')
    if any(p['p_type'] in ('PT_INTERP','PT_TLS','PT_DYNAMIC') for p in elf.iter_segments()):raise ValueError('Flat stage dynamic segment')
    start=min(s['sh_addr'] for s in sections);end=max(s['sh_addr']+s['sh_size'] for s in sections);blob=bytearray(end-start)
    for s in sections:blob[s['sh_addr']-start:s['sh_addr']-start+s['sh_size']]=s.data()
    if start or elf.header.e_entry!=start:raise ValueError('Flat stage entry is not at offset 0')
    return bytes(blob)


def compile_role(role,source,meta,output,check=True):
    clang,linker=tools()
    with tempfile.TemporaryDirectory(prefix='powerful-fl23-'+role+'-') as tmp:
        work=Path(tmp)
        for f in source.iterdir():
            if f.is_file() and not f.name.startswith('._'):shutil.copyfile(f,work/f.name)
        if role in ('resident','stage'):
            name='resident' if role=='resident' else 'stage'
            run([clang,'-target','aarch64-none-elf',*STAGE_FLAGS,'-c',name+'.c','-o',name+'.o'],work)
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
            if check and sha(data)!=meta['roles'][role]['outputs'][name]:raise ValueError('Rebuilt runtime differs: '+role+'/'+name)
            target=output/role;target.mkdir(exist_ok=True);(target/name).write_bytes(data)
        return generated


def part_object(clang,objcopy,source,work,name,entry,renamed):
    """Compile one unchanged stage and keep only its renamed entry global."""
    part=work/name;part.mkdir()
    for f in source.iterdir():
        if f.is_file() and not f.name.startswith('._'):shutil.copyfile(f,part/f.name)
    run([clang,'-target','aarch64-none-elf',*STAGE_FLAGS,'-c','stage.c','-o','stage.o'],part)
    run([objcopy,'--redefine-sym',entry+'='+renamed,'--rename-section','.text.stage_entry=.text.part_'+name,
        'stage.o','renamed.o'],part)
    run([objcopy,'--keep-global-symbol='+renamed,'renamed.o','part.o'],part)
    return part/'part.o'


def primary_source(flags):
    primary=flags&3
    return None if not primary else (BASE/'runtime/stage' if primary==1 else BASE/'variants'/str(primary)/'runtime/stage')


def compile_combined(flags,meta,output,check=True):
    clang,linker=tools();objcopy=objcopy_tool()
    with tempfile.TemporaryDirectory(prefix='powerful-fl23-combined-') as tmp:
        work=Path(tmp);objects=[]
        for f in (BASE/'combined').iterdir():
            if f.is_file() and not f.name.startswith('._'):shutil.copyfile(f,work/f.name)
        run([clang,'-target','aarch64-none-elf',*STAGE_FLAGS,'-DCOMBINED_FLAGS=%du'%flags,
            '-DCOMBINED_PRIMARY=%d'%(1 if flags&3 else 0),'-c','wrapper.c','-o','wrapper.o'],work)
        objects.append(work/'wrapper.o')
        source=primary_source(flags)
        if source:objects.append(part_object(clang,objcopy,source,work,'primary','fl23_a1_stage_entry','fl23_part_primary'))
        objects.append(part_object(clang,objcopy,BASE/'variants/4/runtime/custom-stage',work,'custom',
            'fl23_custom_sound_stage_entry','fl23_part_custom'))
        run([linker,'-T','stage.ld','--gc-sections','--build-id=none','-e','fl23_combined_stage_entry',
            '-o','stage.elf',*map(str,objects)],work)
        raw=(work/'stage.elf').read_bytes();generated={'stage.elf':raw,'stage.bin':flat(raw)}
        for name,data in generated.items():
            if check and sha(data)!=meta['roles']['combined']['outputs'][name]:raise ValueError('Rebuilt runtime differs: combined/'+name)
            target=output/'stage';target.mkdir(parents=True,exist_ok=True);(target/name).write_bytes(data)
        return generated


def variant_dir(flags):
    return BASE/'runtime' if flags==1 else BASE/'variants'/str(flags)/'runtime'


def source_root(flags,meta):
    """Combined variants pin files relative to the FL23 data root."""
    return BASE if meta.get('file_root')=='fl23' else variant_dir(flags)


def combined_files(flags):
    roots=[BASE/'combined']
    if flags&3:roots.append(primary_source(flags))
    roots.append(BASE/'variants/4/runtime/custom-stage')
    return {str(p.relative_to(BASE)):sha(p.read_bytes()) for root in roots for p in sorted(root.iterdir())
        if p.is_file() and not p.name.startswith('._')}


def receipt_for(stage,template):
    """Flat schema-2 receipt: identical geometry/identity, new stage digest."""
    raw=bytearray(template)
    if len(raw)!=512 or raw[:8]!=b'FL23A1R2' or raw[16:24]!=b'A1READY1':raise ValueError('Receipt template differs')
    if zlib.crc32(raw[:508])!=struct.unpack_from('<I',raw,508)[0]:raise ValueError('Receipt template CRC differs')
    if len(stage)>CARRIER_CAPACITY-1024 or len(stage)<128:raise ValueError('Stage exceeds the /setting/fl23 carrier capacity')
    digest=bytes.fromhex(sha(stage))
    struct.pack_into('<Q',raw,40,len(stage))
    raw[160:192]=digest;raw[192:224]=digest;raw[224:240]=digest[:16]
    struct.pack_into('<4Q',raw,240,0,0,0,0)
    struct.pack_into('<I',raw,508,zlib.crc32(raw[:508]))
    return bytes(raw)


def rebuild(destination,flags=1,refresh=False):
    if flags not in FLAGS:raise ValueError('Unknown /setting/fl23 feature flags')
    source=variant_dir(flags);manifest_path=source/'manifest.json';meta=json.loads(manifest_path.read_bytes())
    if meta.get('flags',1)!=flags:raise ValueError('Variant manifest flags differ')
    root=source_root(flags,meta)
    if refresh and flags&CUSTOM_BIT:meta['files']=combined_files(flags)
    for name,digest in meta['files'].items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Runtime source path differs: '+name)
        if not refresh and sha((root/path).read_bytes())!=digest:raise ValueError('Runtime source differs: '+name)
    if refresh and not flags&CUSTOM_BIT:
        meta['files']={name:sha((root/name).read_bytes()) for name in meta['files']}
    if destination.exists():raise ValueError('Choose a new output directory')
    destination.mkdir(parents=True)
    if flags&CUSTOM_BIT:
        generated=compile_combined(flags,meta,destination,check=not refresh)
        if refresh:meta['roles']['combined']['outputs']={k:sha(v) for k,v in generated.items()}
    else:
        for role in ('names-module','names-bootstrap','resident','core','worker','stage'):
            if role in meta['roles']:
                generated=compile_role(role,source/role,meta,destination,check=not refresh or role!='stage')
                if refresh and role=='stage':meta['roles']['stage']['outputs']={k:sha(v) for k,v in generated.items()}
    stage=(destination/'stage/stage.bin').read_bytes()
    if refresh:
        template=json.loads((BASE/'runtime/manifest.json').read_bytes())['receipt_hex']
        meta['receipt_hex']=receipt_for(stage,bytes.fromhex(template)).hex()
    receipt=bytes.fromhex(meta['receipt_hex'])
    carrier=bytes(512)+receipt+stage
    if len(receipt)!=512 or len(carrier)>CARRIER_CAPACITY:raise ValueError('Rebuilt carrier geometry differs')
    if refresh:
        meta['carrier_sha256']=sha(carrier)
        manifest_path.write_text(json.dumps(meta,indent=2,ensure_ascii=False)+'\n')
        packaged=BASE/'carrier.bin' if flags==1 else BASE/'variants'/str(flags)/'carrier.bin'
        packaged.parent.mkdir(parents=True,exist_ok=True);packaged.write_bytes(carrier)
    if sha(carrier)!=meta['carrier_sha256']:raise ValueError('Rebuilt carrier differs')
    (destination/'carrier.bin').write_bytes(carrier)
    print(json.dumps(dict(passed=True,flags=flags,carrier_sha256=sha(carrier),carrier_bytes=len(carrier),
        refreshed=refresh,camera_access=False)))
    return carrier


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--flags',type=int,choices=FLAGS,default=1)
    p.add_argument('--refresh-manifest',action='store_true',help='maintainer: rewrite generated digests, receipt and carrier.bin')
    a=p.parse_args();rebuild(a.output,a.flags,a.refresh_manifest)
