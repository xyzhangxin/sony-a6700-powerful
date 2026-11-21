"""Create the detached Ed25519 signature for a reviewed Mac release ZIP."""
import argparse
from hashlib import sha256
from pathlib import Path, PurePosixPath
import base64
import plistlib
import re
import stat
from zipfile import ZipFile

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--private-key', required=True, type=Path)
    args = parser.parse_args()
    pinned = Path(__file__).resolve().parents[1] / 'desktop/macos/update-installer/public-key.txt'
    private = load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey) or private.public_key().public_bytes_raw() != base64.b64decode(pinned.read_text(encoding='utf-8')):
        raise ValueError('The signing key does not match the installed Mac updater trust root')
    with ZipFile(args.archive) as archive:
        entries = archive.infolist()
        roots = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename:
                raise ValueError('Archive entry escapes the app bundle')
            roots.add(path.parts[0])
            if stat.S_ISLNK(entry.external_attr >> 16):
                target = PurePosixPath(archive.read(entry).decode('utf-8'))
                if target.is_absolute():
                    raise ValueError('Absolute symlink in app bundle')
                depth = len(path.parent.parts)
                for part in target.parts:
                    depth += -1 if part == '..' else 1
                    if depth < 1:
                        raise ValueError('Symlink leaves the app bundle')
        root, = roots
        if len({entry.filename.casefold() for entry in entries}) != len(entries) or not root.endswith('.app'):
            raise ValueError('Expected one app and unique paths in ZIP')
        info = plistlib.loads(archive.read(f'{root}/Contents/Info.plist'))
    identifier = info['CFBundleIdentifier']
    version = info['CFBundleShortVersionString']
    if not re.fullmatch(r'org\.a6700unlock\.desktop(?:\.[A-Za-z0-9.-]+)?', identifier) or not re.fullmatch(r'(0|[1-9][0-9]{0,5})(\.(0|[1-9][0-9]{0,5})){2}', version):
        raise ValueError('Unexpected Mac app identifier or release version')
    digest = sha256()
    size = 0
    with args.archive.open('rb') as source:
        while data := source.read(1024 * 1024):
            digest.update(data)
            size += len(data)
    if not 0 < size <= 512 * 1024 * 1024:
        raise ValueError('Release archive exceeds installer limit')
    payload = f'Powerful6700.AppUpdate/v1\nmacOS-arm64\n{identifier}\n{version}\n{size}\n{digest.hexdigest()}\n'.encode()
    signature_path = args.archive.with_name(args.archive.name + '.ed25519')
    with signature_path.open('xb') as stream:
        stream.write(private.sign(payload))
    print(signature_path.name)


if __name__ == '__main__':
    main()
