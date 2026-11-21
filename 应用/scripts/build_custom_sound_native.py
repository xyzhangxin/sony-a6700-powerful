"""Build the offline AArch64 custom shutter-sound import payload."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/a6700_unlock/native/custom_sound_import.c"
DESTINATION = ROOT / "src/a6700_unlock/data/custom-sound"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clang", default=shutil.which("clang"))
    parser.add_argument("--linker", default=shutil.which("aarch64-elf-ld") or shutil.which("ld.lld"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--reader", action="store_true", help="Build fixed-path slot observation only")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--binding", action="store_true", help="Rebuild the fixed installed-loader observer")
    args = parser.parse_args()
    if args.binding:
        source = ROOT / 'src/a6700_unlock/native/custom-sound-binding'
        package = ROOT / 'src/a6700_unlock/data/native/custom-sound-binding'
        manifest = json.loads((package/'manifest.json').read_text(encoding='utf-8'))
        for name, digest in manifest['source_files'].items():
            if hashlib.sha256((source/name).read_bytes()).hexdigest() != digest:
                raise SystemExit('Binding observer source changed: '+name)
        if args.output is None:
            raise SystemExit('--binding requires a separate --output directory')
        args.output.mkdir(parents=True, exist_ok=True)
        binary = args.output/'probe.elf'
        if binary.exists():
            raise SystemExit('Refusing to overwrite observer rebuild')
        with tempfile.TemporaryDirectory() as temporary:
            obj = Path(temporary)/'probe.o'
            subprocess.run([args.clang,'-target','aarch64-none-elf','-O2','-mgeneral-regs-only',
                '-mstrict-align','-fPIC','-ffreestanding','-fno-builtin','-fno-stack-protector',
                '-c',str(source/'probe.c'),'-o',str(obj)],check=True)
            subprocess.run([args.linker,'-shared','-Bsymbolic','-z','defs','-z','noexecstack',
                '--build-id=none','-o',str(binary),str(obj)],check=True)
        if hashlib.sha256(binary.read_bytes()).hexdigest() != manifest['files']['probe.elf']:
            raise SystemExit('Observer rebuild differs from verified payload')
        print('Binding observer rebuilt identically')
        return
    if args.output is None:
        args.output = DESTINATION.parent / 'native' if args.reader else DESTINATION
    if not args.clang or not args.linker:
        raise SystemExit("Need clang plus aarch64-elf-ld or ld.lld")
    source = SOURCE.with_name('custom_sound_read.c') if args.reader else SOURCE
    payload_name = 'custom-sound-reader' if args.reader else 'custom-sound-writer'
    binary = args.output / (payload_name + '.elf')
    manifest_path = args.output / ('custom-sound-reader.json' if args.reader else 'manifest.json')
    if (binary.exists() or manifest_path.exists()) and not args.force:
        raise SystemExit("Refusing to overwrite the custom-sound payload; pass --force after source review")

    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="a6700-custom-sound-") as temporary:
        work = Path(temporary)
        obj = work / "custom_sound_import.o"
        built = work / "custom-sound-writer.elf"
        subprocess.run([
            args.clang, "--target=aarch64-unknown-elf", "-fPIC", "-O2",
            "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
            "-fno-pic", "-fno-pie", "-mno-outline-atomics",
            "-Wall", "-Wextra", "-Werror", "-c", str(source), "-o", str(obj),
        ], check=True)
        subprocess.run([
            args.linker, "-shared", "-Bsymbolic", "-z", "noexecstack",
            "-o", str(built), str(obj),
        ], check=True)
        data = built.read_bytes()
        if data[:4] != b"\x7fELF":
            raise SystemExit("Custom-sound payload is not ELF")
        nm = shutil.which("aarch64-elf-nm")
        if nm:
            undefined = subprocess.run([nm, "-u", str(built)], capture_output=True,
                                       text=True, check=True).stdout.strip()
            if undefined:
                raise SystemExit("Undefined custom-sound symbols:\n" + undefined)
        binary.write_bytes(data)

    manifest = {
        "schema": "a6700.custom-sound-native-payload/1",
        "payload_id": "custom-shutter-sound-slot-import",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "hardware_validation": "pending",
        "request_header_bytes": 72,
        "response_bytes": 44,
        "syscalls": {
            "mkdirat": 34, "unlinkat": 35, "renameat": 38,
            "openat": 56, "close": 57, "getdents64": 61,
            "read": 63, "write": 64, "fsync": 82,
        },
        "payloads": {
            payload_name: {
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "bytes": binary.stat().st_size,
            }
        },
        "camera_io": False,
        "not_installable": True,
    }
    if args.reader:
        manifest.update(schema='a6700.custom-sound-slot-observer/1',
                        payload_id='custom-shutter-sound-slot-observer',
                        response_bytes=None, maximum_response_bytes=44 + 128 + 255 + 512*1024,
                        slot_count=10, persistent_writes=False,
                        syscalls={'openat':56,'close':57,'read':63,'write':64,'fsync':82})
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
