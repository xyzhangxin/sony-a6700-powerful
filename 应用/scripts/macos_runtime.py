"""Pinned, relocatable macOS runtime assembly; never copies a developer venv.

The full upstream archive supplies dependency licenses and PYTHON.json, which
are absent from install-only archives. Only install/, licenses/ and metadata
enter the app. Source/build artifacts stay outside the bundle.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'desktop/runtime-macos-arm64.json'
MACHO = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe'}


def verify_digest(archive, expected):
    digest = hashlib.sha256()
    with Path(archive).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('Runtime archive SHA-256 mismatch; refusing extraction or execution')


def obtain_archive(cache, *, download=False):
    pin = json.loads(LOCK.read_text())
    destination = Path(cache) / pin['filename']
    if not destination.exists():
        if not download:
            raise ValueError('Runtime archive missing; use --download-runtime or --runtime-archive')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                with urllib.request.urlopen(pin['url'], timeout=60) as response:
                    shutil.copyfileobj(response, output)
                output.close()
                verify_digest(temporary, pin['sha256'])
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    verify_digest(destination, pin['sha256'])
    return destination


def stage_runtime(archive, resources):
    pin = json.loads(LOCK.read_text())
    verify_digest(archive, pin['sha256'])  # Must precede tar or any runtime execution.
    metadata = subprocess.check_output(['/usr/bin/tar', '-xOf', str(archive), 'python/PYTHON.json'])
    distribution = json.loads(metadata)
    if distribution['python_version'] != pin['version'] or distribution['target_triple'] != pin['target']:
        raise ValueError('Runtime distribution does not match the pinned version/architecture')
    runtime = resources / 'runtime'
    runtime.mkdir()
    subprocess.run(['/usr/bin/tar', '-xf', str(archive), '-C', str(runtime),
                    '--strip-components', '2', 'python/install'], check=True)
    for path in runtime.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(runtime.resolve()):
            raise ValueError('Runtime symlink escapes its installation')
    # The product UI is SwiftUI. Tcl/Tk plugins add bare-name dylib loads and
    # are unused by the fixed worker, so do not distribute a second GUI stack.
    for parent, patterns in ((runtime/'lib', ('itcl*', 'tcl*', 'tk*', 'tdbc*', 'thread*', 'libtcl*', 'libtk*')),
                             (runtime/'bin', ('tclsh*', 'wish*')),
                             (runtime/'lib/python3.13/lib-dynload', ('_tkinter*',))):
        for pattern in patterns:
            for path in parent.glob(pattern):
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
    notices = resources / 'runtime-licenses'
    notices.mkdir()
    subprocess.run(['/usr/bin/tar', '-xf', str(archive), '-C', str(notices),
                    '--strip-components', '2', 'python/licenses'], check=True)
    (notices / 'PYTHON.json').write_bytes(metadata)
    (notices / 'runtime-pin.json').write_text(json.dumps(pin, indent=2) + '\n')
    if not (notices / 'LICENSE.cpython.txt').is_file():
        raise ValueError('Runtime license missing')
    return pin


def macho_files(root):
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as source:
                if source.read(4) in MACHO:
                    yield path


def inspect_macho(path, architecture='arm64'):
    """Reject Homebrew/user paths and a runtime requiring a newer OS than UI."""
    arches = subprocess.check_output(['lipo', '-archs', str(path)], text=True).split()
    if arches != [architecture]:
        raise ValueError('Unexpected Mach-O architecture: ' + path.name)
    libraries = subprocess.check_output(['otool', '-L', str(path)], text=True)
    for row in libraries.splitlines()[1:]:
        library = row.strip().split(' (', 1)[0]
        if not library.startswith(('/usr/lib/', '/System/Library/', '@loader_path/', '@executable_path/', '@rpath/')):
            raise ValueError('Nonportable library dependency: ' + library)
    commands = subprocess.check_output(['otool', '-l', str(path)], text=True).splitlines()
    for index, line in enumerate(commands):
        if line.strip() in ('cmd LC_BUILD_VERSION', 'cmd LC_VERSION_MIN_MACOSX'):
            for value in commands[index + 1:index + 7]:
                parts = value.split()
                if parts and parts[0] in ('minos', 'version') and tuple(map(int, parts[1].split('.')[:2])) > (13, 0):
                    raise ValueError('Runtime requires macOS newer than 13: ' + path.name)
        if line.strip() == 'cmd LC_RPATH':
            rpath = commands[index + 2].strip().split()[1]
            if not rpath.startswith(('@loader_path', '@executable_path', '/usr/lib/', '/System/Library/')):
                raise ValueError('Nonportable runtime search path: ' + rpath)


def sign_bundle(bundle, architecture, before_seal=None):
    # Sign nested native code first, then seal the containing app. Work on a
    # clean local filesystem; AppleDouble files on shared volumes break sealing.
    binaries = list(macho_files(bundle))
    for path in binaries:
        inspect_macho(path, architecture)
        subprocess.run(['codesign', '--force', '--sign', '-', str(path)],
                       check=True, capture_output=True)
    if before_seal is not None:
        before_seal()
    subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    return len(binaries)
