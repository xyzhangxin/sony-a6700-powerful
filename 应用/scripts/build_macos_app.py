"""Build a relocatable Apple Silicon .app with Python, PyUSB, libusb and capture helper.

Build tools are developer dependencies only. No device is enumerated or accessed.
All downloads are pinned and checked before extraction or execution.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

from build_macos_preview import ROOT, build_ui
from macos_runtime import obtain_archive, stage_runtime, verify_digest, sign_bundle


def dependency(pin, cache, download):
    path = cache / pin['filename']
    if not path.exists():
        if not download:
            raise ValueError('Cached dependency missing: ' + pin['filename'])
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
            temporary = Path(output.name)
            try:
                with urllib.request.urlopen(pin['url'], timeout=60) as response:
                    shutil.copyfileobj(response, output)
                output.close()
                verify_digest(temporary, pin['sha256'])
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    verify_digest(path, pin['sha256'])
    return path


def stage_pyusb(wheel, resources):
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Wheel path escapes package')
            if name.startswith('usb/') and not name.endswith('/'):
                destination = resources / 'python' / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
        licenses = [name for name in archive.namelist() if '.dist-info/' in name and name.endswith('LICENSE')]
        if len(licenses) != 1:
            raise ValueError('PyUSB license missing')
        (resources/'runtime-licenses/PyUSB-LICENSE.txt').write_bytes(archive.read(licenses[0]))


def stage_libusb(archive, pin, resources, work):
    verify_digest(archive, pin['sha256'])
    source_root = work / 'libusb-source';source_root.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(source_root, filter='data')
    source = source_root / ('libusb-' + pin['version'])
    environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET='13.0',
                       CC=subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip(),
                       CXX=subprocess.check_output(['xcrun', '--find', 'clang++'], text=True).strip(),
                       SDKROOT=subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip(),
                       CFLAGS='-O2 -mmacosx-version-min=13.0',
                       LDFLAGS='-mmacosx-version-min=13.0',
                       # Libtool otherwise reads sysctl kern.argmax, which can
                       # be denied in restricted build environments. Use a
                       # conservative command-length cap, not an empty value.
                       lt_cv_sys_max_cmd_len='32768')
    # Keep build diagnostics outside the final app. No maintainer paths enter it.
    with (work/'libusb-build.log').open('w') as log:
        for command in ([str(source/'configure'), '--disable-static', '--enable-shared',
                         '--disable-dependency-tracking', '--disable-examples-build', '--disable-tests-build'],
                        ['make', '-j4']):
            result = subprocess.run(command, cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError('libusb build failed:\n' + (work/'libusb-build.log').read_text()[-4000:])
    library = resources/'lib/libusb-1.0.dylib';library.parent.mkdir()
    shutil.copyfile(source/'libusb/.libs/libusb-1.0.dylib', library)
    subprocess.run(['install_name_tool', '-id', '@rpath/libusb-1.0.dylib', str(library)], check=True)
    (resources/'runtime-licenses/libusb-COPYING.txt').write_bytes((source/'COPYING').read_bytes())
    # Include exact library source so recipients can rebuild/replace this LGPL component.
    sources = resources/'dependency-sources';sources.mkdir()
    shutil.copyfile(archive, sources/pin['filename'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/Powerful6700.app')
    parser.add_argument('--runtime-archive', type=Path)
    parser.add_argument('--cache', type=Path, default=ROOT/'.cache/desktop')
    parser.add_argument('--download-runtime', action='store_true', help='Allow fixed runtime/dependency downloads during build')
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        parser.error('This distribution targets Apple Silicon macOS')
    if args.output.exists():
        parser.error('Output exists; choose a new output path')
    cache = args.cache.resolve()
    archive = args.runtime_archive or obtain_archive(cache, download=args.download_runtime)
    pins = json.loads((ROOT/'desktop/runtime-dependencies.json').read_text())
    dependencies = {name: dependency(pin, cache, args.download_runtime) for name, pin in pins.items()}
    with tempfile.TemporaryDirectory(prefix='a6700-standalone-') as folder:
        work=Path(folder);bundle=work/'Powerful6700.app'
        print("Compiling native UI…", flush=True)
        resources=build_ui(bundle, work)
        print("Staging pinned runtime…", flush=True)
        pin=stage_runtime(archive, resources)
        stage_pyusb(dependencies['pyusb'], resources)
        print("Building bundled libusb for macOS 13…", flush=True)
        stage_libusb(dependencies['libusb'], pins['libusb'], resources, work)
        helper=resources/'helpers/mac-capture';helper.parent.mkdir()
        sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
        subprocess.run(['xcrun', 'clang', '-isysroot', sdk, '-fobjc-arc', '-mmacosx-version-min=13.0',
                        '-framework', 'Foundation', '-framework', 'IOUSBHost', '-framework', 'IOKit',
                        str(ROOT/'src/a6700_unlock/native/mac_capture.m'), '-o', str(helper)], check=True)
        helper.chmod(0o755)
        shutil.copyfile(ROOT/'desktop/worker.py', resources/'worker.py')
        (resources/'runtime.json').write_text(json.dumps(dict(kind='bundled', python=pin['python']), indent=2)+'\n')
        (resources/'runtime-licenses/dependencies.json').write_text(json.dumps(pins, indent=2)+'\n')
        def manifest():
            items={name:dict(path=path, sha256=hashlib.sha256((resources/path).read_bytes()).hexdigest())
                   for name,path in [('capture','helpers/mac-capture'),('libusb','lib/libusb-1.0.dylib')]}
            (resources/'bundle-manifest.json').write_text(json.dumps(dict(schema=1, resources=items), indent=2)+'\n')
        print("Checking dependencies and signing…", flush=True)
        count=sign_bundle(bundle, 'arm64', before_seal=manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # ditto preserves modes and symlinks; no absolute Python configuration is copied.
        subprocess.run(['/usr/bin/ditto', '--noextattr', '--norsrc', str(bundle), str(args.output)], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(args.output)], check=True)
    print('Built standalone app:', args.output)
    print('Bundled native resources:', count)
    print('Development signature only; not notarized. No camera accessed.')


if __name__ == '__main__':
    main()
