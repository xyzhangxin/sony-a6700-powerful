"""Rebuild the fixed read-only observer without firmware or research inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--setting',action='store_true',help='Rebuild the fixed native reset payload')
    args=parser.parse_args()
    if args.output.exists():parser.error('Output exists; never overwrite a distributed payload')
    root=ROOT/'src/a6700_unlock/data/native'
    stem='close-setting' if args.setting else 'close-state'
    manifest=json.loads((root/(stem+'.json')).read_text())
    sha=lambda b:hashlib.sha256(b).hexdigest()
    for name,digest in manifest['sources'].items():
        if Path(name).name!=name or sha((root/name).read_bytes())!=digest:raise ValueError('Observer source mismatch')
    native=(ROOT/'src/a6700_unlock/native/close_state.c').read_bytes()
    expected=manifest['native_sources']['close_state.c'] if args.setting else manifest['native_source_sha256']
    if sha(native)!=expected or native not in (root/(stem+'-source.c')).read_bytes():
        raise ValueError('Implementation changed')
    if args.setting and sha((ROOT/'src/a6700_unlock/native/close_setting.c').read_bytes())!=manifest['native_sources']['close_setting.c']:
        raise ValueError('Setting implementation changed')
    clang=shutil.which('clang');linker=shutil.which('aarch64-elf-ld') or shutil.which('ld.lld')
    if not clang or not linker:raise RuntimeError('AArch64 clang/linker required')
    with tempfile.TemporaryDirectory(prefix='a6700-close-reader-') as directory:
        work=Path(directory)
        for name in manifest['sources']:
            shutil.copyfile(root/name,work/('probe.c' if name==stem+'-source.c' else name))
        subprocess.run([clang,'-target','aarch64-none-elf','-O2','-mgeneral-regs-only','-mstrict-align','-fPIC',
            '-ffreestanding','-fno-builtin','-fno-stack-protector','-c',str(work/'probe.c'),'-o',str(work/'probe.o')],check=True)
        subprocess.run([linker,'-shared','-Bsymbolic','-z','defs','-z','noexecstack','--build-id=none','-o',str(work/'probe.elf'),str(work/'probe.o')],check=True)
        binary=(work/'probe.elf').read_bytes()
    if sha(binary)!=manifest['payload_sha256']:raise ValueError('Rebuilt observer differs; review before replacing')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as out:out.write(binary)
    print('Fixed observer rebuilt byte-for-byte:',sha(binary))


if __name__=='__main__':main()
