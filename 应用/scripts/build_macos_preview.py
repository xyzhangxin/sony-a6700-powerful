"""Build the native development preview using an explicitly selected local Python."""
import argparse
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from a6700_unlock import __version__


def app_name(*, standalone):
    name = 'Powerful6700' if standalone else 'Powerful6700 预览'
    return f'{name} {__version__}' if '.dev' in __version__ else name


def build_ui(bundle, work, python=None, *, include_shutter_sound_import=False):
    """The standalone builder reuses this exact UI and application source copy."""
    if include_shutter_sound_import and python is None:
        raise ValueError('Shutter sound import assets are limited to development previews')
    executable = work/'A6700Preview'
    sources = []
    for source in sorted((ROOT/'desktop/macos').glob('*.swift')):
        if source.name.startswith('.'):
            continue
        if source.is_symlink() or not source.is_file():
            raise RuntimeError('Swift source must be a regular file')
        staged = work/source.name
        shutil.copyfile(source, staged)
        sources.append(staged)
    if not sources:
        raise RuntimeError('Native UI sources are missing')
    result = subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library',
        '-target', platform.machine() + '-apple-macosx13.0', '-module-cache-path', str(work/'modules'),
        '-O', *map(str, sources), '-o', str(executable)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Native UI compilation failed:\n' + result.stderr[-6000:])
    resources = bundle/'Contents/Resources';resources.mkdir(parents=True)
    macos = bundle/'Contents/MacOS';macos.mkdir()
    shutil.copyfile(executable, macos/'A6700Preview');(macos/'A6700Preview').chmod(0o755)
    def shipping_files(directory, names):
        return set(shutil.ignore_patterns('._*', '__pycache__', '*.pyc', '.DS_Store')(directory, names))
    shutil.copytree(ROOT/'src/a6700_unlock', resources/'python/a6700_unlock', copy_function=shutil.copyfile,
                   ignore=shipping_files)
    if include_shutter_sound_import:
        subprocess.run([str(python), '-I', '-B', '-c',
                        'import sys; sys.path.insert(0, sys.argv[1]); '
                        'from a6700_unlock.custom_sound_native import load_custom_sound_payload; '
                        'from a6700_unlock.custom_sound_capability_package import load_custom_sound_capability_package; '
                        'load_custom_sound_payload(); load_custom_sound_capability_package()',
                        str(resources/'python')], check=True)
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT/name, resources/name)
    sounds = resources / '默认音频'
    sounds.mkdir()
    for name in ('coin.wav', 'slap.wav', 'duck.wav', 'beep.wav'):
        shutil.copyfile(ROOT / 'desktop/macos/默认音频' / name, sounds / name)
    for name in ('Powerful6700.png', 'Powerful6700.icns'):
        shutil.copyfile(ROOT/'desktop/品牌'/name, resources/name)
    if python:
        (resources/'runtime.json').write_text(json.dumps(dict(kind='development', python=str(python)), indent=2)+'\n')
    standalone = python is None
    display_name = app_name(standalone=standalone)
    bundle_id = 'org.a6700unlock.desktop' if standalone else 'org.a6700unlock.preview'
    if '.dev' in __version__:
        bundle_id = f'{bundle_id}.{__version__}'
    (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleName=display_name,
        CFBundleDisplayName=display_name,
        CFBundleIconFile='Powerful6700.icns',
        CFBundleIdentifier=bundle_id,
        CFBundleExecutable='A6700Preview', CFBundlePackageType='APPL',
        CFBundleShortVersionString=__version__.split('.dev')[0],
        CFBundleVersion=__version__.split('.dev')[-1], A6700DevelopmentVersion=__version__,
        LSMinimumSystemVersion='13.0', NSHighResolutionCapable=True,
        NSHumanReadableCopyright='Copyright (C) 2026 Powerful6700 contributors; GPL-3.0-only')))
    return resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    name = app_name(standalone=False) + '.app'
    parser.add_argument('--output', type=Path, default=ROOT/'dist'/name)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--include-shutter-sound-import', action='store_true',
                        help='Include the verified slot writer assets in this development preview')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('The native preview requires macOS')
    if args.output.exists():
        parser.error('Output exists; choose a new preview path')
    python = args.python.absolute()
    subprocess.run([str(python), '-c', 'import sys; assert sys.version_info >= (3,10)'], check=True)
    with tempfile.TemporaryDirectory(prefix='a6700-swift-preview-') as folder:
        work=Path(folder);bundle=work/name
        build_ui(bundle, work, python, include_shutter_sound_import=args.include_shutter_sound_import)
        subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)], check=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle, args.output, copy_function=shutil.copyfile)
        (args.output/'Contents/MacOS/A6700Preview').chmod(0o755)
    print('Built development preview:', args.output)
    print('Uses local Python:', python)
    print('Default camera mode checks the local environment only; simulation remains available.')


if __name__ == '__main__':
    main()
