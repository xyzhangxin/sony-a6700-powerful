"""Build a Windows x64 preview with separate GUI and console worker runtimes.

No drivers are installed and no camera is enumerated by the builder. Output is
exclusive: never replace a distributed app. Use the existing source packager.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from a6700_unlock import __version__


def copy_licenses(destination, wdi_version):
    destination.mkdir(parents=True)
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT/name, destination/name)
    versions = {'Python':platform.python_version(),'libwdi':wdi_version}
    for name in ('PySide6-Essentials', 'shiboken6', 'pyusb', 'libusb-package', 'pyinstaller', 'cryptography', 'cffi', 'pycparser'):
        dist = importlib.metadata.distribution(name); versions[name] = dist.version
        target = destination/name; target.mkdir()
        for file in dist.files or []:
            if 'license' in str(file).lower() or 'copying' in str(file).lower():
                path = Path(dist.locate_file(file))
                if path.is_file():
                    dest = target/str(file).replace('..', '_')
                    dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path,dest)
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError('Python license missing')
    shutil.copyfile(python_license, destination/'Python-LICENSE.txt')
    (destination/'versions.json').write_text(json.dumps(versions,indent=2)+'\n',encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--libusb-source',type=Path,required=True,
                        help='libusb 1.0.30 source archive from desktop/runtime-dependencies.json')
    parser.add_argument('--libwdi-source',type=Path,required=True,
                        help='libwdi source archive from desktop/runtime-dependencies.json')
    args = parser.parse_args()
    if sys.platform!='win32' or platform.machine().upper() not in ('AMD64','X86_64') or sys.maxsize<=2**32:
        parser.error('Build on Windows x64 with 64-bit Python')
    if args.output.exists():
        parser.error('Output exists; use a new version directory')
    for requirement in (ROOT/'desktop/windows/requirements.txt').read_text(encoding='utf-8').splitlines():
        requirement=requirement.strip()
        if not requirement or requirement.startswith('#'):
            continue
        name,expected=requirement.split('==',1)
        if importlib.metadata.version(name)!=expected:
            raise RuntimeError('Build dependency version mismatch: '+name)
    import libusb_package
    path = libusb_package.get_library_path()
    if path is None:
        raise RuntimeError('Pinned libusb DLL missing')
    library = Path(path)
    pin=json.loads((ROOT/'desktop/runtime-dependencies.json').read_text(encoding='utf-8'))['libusb']
    if hashlib.sha256(args.libusb_source.read_bytes()).hexdigest()!=pin['sha256']:
        raise RuntimeError('libusb source archive hash mismatch')
    wdi_pin=json.loads((ROOT/'desktop/runtime-dependencies.json').read_text(encoding='utf-8'))['libwdi']
    if hashlib.sha256(args.libwdi_source.read_bytes()).hexdigest()!=wdi_pin['source_sha256']:
        raise RuntimeError('libwdi source archive hash mismatch')
    wdi=ROOT/'desktop/windows/driver/wdi-simple.exe'
    if hashlib.sha256(wdi.read_bytes()).hexdigest()!=wdi_pin['binary_sha256']:
        raise RuntimeError('wdi-simple binary hash mismatch')
    if not library.is_file():
        raise RuntimeError('Pinned libusb DLL missing')
    with tempfile.TemporaryDirectory(prefix='Powerful6700-build-') as folder:
        work=Path(folder); generated=work/'resources'; generated.mkdir()
        package=generated/'a6700_unlock'
        shutil.copytree(ROOT/'src/a6700_unlock',package,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc','._*','.DS_Store'))
        # Reuse the project's mark; Windows ICO supports PNG-compressed images.
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
        mark=QImage(str(ROOT/'desktop/品牌/Powerful6700.png'))
        if mark.isNull():
            raise RuntimeError('Application mark missing')
        mark=mark.scaled(256,256,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
        png=QByteArray(); buffer=QBuffer(png); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not mark.save(buffer,'PNG'):
            raise RuntimeError('Cannot encode application icon')
        data=bytes(png); icon=generated/'Powerful6700.ico'
        icon.write_bytes(struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',0,0,0,0,1,32,len(data),22)+data)
        # Both processes use their own sealed library and source resource set.
        dll_name='libusb_package/'+library.relative_to(Path(libusb_package.__file__).parent).as_posix()
        (generated/'runtime.json').write_text(json.dumps(dict(kind='bundled',platform='windows-x64',version=__version__)),encoding='utf-8')
        (generated/'bundle-manifest.json').write_text(json.dumps(dict(schema=1,resources=dict(
            libusb=dict(path=dll_name,sha256=hashlib.sha256(library.read_bytes()).hexdigest()),
            wdi=dict(path='driver/wdi-simple.exe',sha256=hashlib.sha256(wdi.read_bytes()).hexdigest())))),encoding='utf-8')
        common=[sys.executable,'-m','PyInstaller','--clean','--noconfirm','--onedir',
                '--distpath',str(work/'dist'),'--workpath',str(work/'build'),
                '--specpath',str(work/'spec'),'--paths',str(ROOT/'src'),
                '--add-data',str(package)+':a6700_unlock',
                '--add-data',str(generated/'runtime.json')+':.',
                '--add-data',str(generated/'bundle-manifest.json')+':.',
                '--add-binary',str(library)+':'+str(PurePosixPath(dll_name).parent),
                '--add-binary',str(wdi)+':driver',
                '--collect-submodules','a6700_unlock','--hidden-import','plistlib','--collect-all','libusb_package',
                '--collect-all','usb','--collect-all','cryptography','--collect-all','cffi','--collect-all','pycparser']
        subprocess.run(common+['--name','Powerful6700.Service','--console',
                               '--exclude-module','PySide6',str(ROOT/'desktop/windows/service.py')],check=True)
        subprocess.run(common+['--name','Powerful6700','--windowed',
                               '--icon',str(icon),
                               '--hidden-import','smoke',
                               '--add-data',str(ROOT/'desktop/品牌/Powerful6700.png')+':.',
                               str(ROOT/'desktop/windows/main.py')],check=True)
        bundle=work/'dist/Powerful6700'
        shutil.copytree(work/'dist/Powerful6700.Service',bundle/'service')
        shutil.copyfile(ROOT/'docs/WINDOWS.md',bundle/'Windows使用说明.md')
        copy_licenses(bundle/'licenses',wdi_pin['version'])
        shutil.copyfile(ROOT/'desktop/windows/driver/README.md',bundle/'licenses/libwdi-README.md')
        shutil.copytree(ROOT/'desktop/windows/licenses',bundle/'licenses/Windows-dependencies')
        sources=bundle/'dependency-sources'; sources.mkdir()
        shutil.copyfile(args.libusb_source,sources/pin['filename'])
        shutil.copyfile(args.libwdi_source,sources/wdi_pin['filename'])
        args.output.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(bundle,args.output)
    print('Built Windows preview:',args.output)
    print('No camera accessed. Driver setup and hardware acceptance are separate.')


if __name__=='__main__':
    main()
