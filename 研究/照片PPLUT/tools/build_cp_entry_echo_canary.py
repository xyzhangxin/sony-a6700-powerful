"""Compile the fixed private entry echo with pinned Zig; no device access."""
import argparse
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from elftools.elf.elffile import ELFFile

SELF=Path(__file__).resolve()
MARKER,TAG=0x31455043,0x314b4341


def sha(data):return hashlib.sha256(data).hexdigest()


def load(package):
    b=json.loads((package/'build.json').read_bytes())
    if (b['schema']!='d6700/CP-entry-echo-build/1' or b['entry']!='0x55a665'
        or b['bytes']!=236 or not b['fits_function_only'] or b['echo_marker']!=hex(MARKER)
        or b['echo_tag']!=hex(TAG) or b['IRQ_calls']!=0):raise ValueError('Fixed echo geometry changed')
    for name,row in b['files'].items():
        data=(package/name).read_bytes()
        if row!=dict(bytes=len(data),sha256=sha(data)):raise ValueError('Echo asset changed')
    src=SELF.parent/'native/cp_entry_echo_canary.c'
    if b['source_sha256']!=sha(src.read_bytes()) or b['builder_sha256']!=sha(SELF.read_bytes()):
        raise ValueError('Echo source changed')
    elf=ELFFile(io.BytesIO((package/'canary.elf').read_bytes()));section=elf.get_section_by_name('.text')
    text=section.data();code=(package/'canary.bin').read_bytes()
    if (elf['e_entry']!=0x55a665 or section['sh_addr']!=0x55a664 or len(text)!=b['text_bytes']
        or len(text)>236 or len(text)%2 or code!=text+b'\x00\xbf'*((236-len(text))//2)):
        raise ValueError('Compiled entry/padding mismatch')
    return code


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('compiler','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('Use a fresh package')
    src=SELF.parent/'native/cp_entry_echo_canary.c';compiler=a.compiler.resolve(strict=True)
    if subprocess.check_output([str(compiler),'version'],text=True).strip()!='0.13.0':raise ValueError('Pinned Zig0.13.0 required')
    linker='''ENTRY(cp_entry_echo_canary)
SECTIONS {
 . = 0x55a664;
 .text : { KEEP(*(.text.cp_entry_echo_canary)) *(.text*) *(.rodata*) }
 /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) }
}
'''
    with tempfile.TemporaryDirectory(prefix='CP-entry-echo-') as tmp:
        work=Path(tmp);shutil.copyfile(src,work/src.name);(work/'canary.ld').write_text(linker,encoding='ascii')
        command=[str(compiler),'cc','-target','thumb-freestanding-eabi','-mcpu=cortex_a9','-mthumb',
            '-mfloat-abi=soft','-mfpu=none','-fno-vectorize','-fno-slp-vectorize','-Oz','-ffreestanding',
            '-fno-builtin','-fno-stack-protector','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
            '-nostdlib','-static','-Wl,-T,canary.ld','-Wl,--build-id=none','-Wl,--no-gc-sections',
            '-Wl,-e,cp_entry_echo_canary',src.name,'-o','canary.elf']
        subprocess.run(command,cwd=work,check=True)
        elf=ELFFile(io.BytesIO((work/'canary.elf').read_bytes()));text=elf.get_section_by_name('.text')
        assert elf['e_machine']=='EM_ARM' and elf['e_type']=='ET_EXEC' and elf['e_entry']==0x55a665
        assert text['sh_addr']==0x55a664 and text['sh_size']<=236 and text['sh_size']%2==0
        assert not any(s['sh_size'] and s['sh_type'] in ('SHT_REL','SHT_RELA') for s in elf.iter_sections())
        a.out.mkdir(parents=True)
        for name in ('canary.elf','canary.ld',src.name):shutil.copyfile(work/name,a.out/name)
        (a.out/'canary.bin').write_bytes(text.data()+b'\x00\xbf'*((236-text['sh_size'])//2))
        names=('canary.elf','canary.ld','canary.bin',src.name)
        b=dict(schema='d6700/CP-entry-echo-build/1',recorded_utc=datetime.now(timezone.utc).isoformat(),
            camera_access=False,installable_candidate=False,entry='0x55a665',address='0x55a664',bytes=236,
            text_bytes=text['sh_size'],padding='Thumb NOPs; original last2 function bytes remain outside ownership',
            fits_function_only=True,echo_marker=hex(MARKER),echo_tag=hex(TAG),IRQ_calls=0,
            source_sha256=sha(src.read_bytes()),builder_sha256=sha(SELF.read_bytes()),command=command,
            files={n:dict(bytes=(a.out/n).stat().st_size,sha256=sha((a.out/n).read_bytes())) for n in names})
        (a.out/'build.json').write_text(json.dumps(b,indent=2)+'\n',encoding='utf-8')
    load(a.out)
    print(json.dumps(dict(result='built',code_bytes=b['text_bytes'],owned_bytes=236,IRQ_calls=0,camera_access=False)))


if __name__=='__main__':main()
