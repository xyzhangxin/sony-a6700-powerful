"""Package the standalone source tree without research data or macOS metadata."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import stat
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from a6700_unlock import __version__

INTERNAL_SAMPLE_PATHS = frozenset({
    "verified/payloads/plain_readonly.c", "verified/payloads/plain_flasher.c",
    "verified/payloads/plain_restore.c",
})
KNOWN_FIRMWARE_BLOCKS = frozenset({
    "1a4eb43ba6cc9d97da20e4914f3857b9a2583aea042b32600fd3f63ba8dec41d",
    "4737200922367dd1e96ed64bf07d40eafe60ede7e3c5dcf60c47e22aad21e09d",
})


def validate_public_file(path, relative):
    """Reject retired samples and renamed copies of their known firmware arrays.

    This is a guard against known copies, not a complete copyright audit.
    Only block hashes are retained here, never copied firmware bytes.
    """
    if relative.as_posix() in INTERNAL_SAMPLE_PATHS:
        raise RuntimeError('Internal firmware sample cannot be distributed: ' + relative.as_posix())
    if path.suffix in ('.c', '.h'):
        source = path.read_text(encoding='utf-8')
        pattern = r'(?:unsigned\s+char|uint8_t)\s+\w+\s*\[\s*(?:512|0x200)\s*\]\s*=\s*\{([^}]+)\}'
        for body in re.findall(pattern, source, flags=re.DOTALL):
            tokens = [token.strip() for token in body.split(',') if token.strip()]
            if len(tokens) != 512 or any(not re.fullmatch(r'(?:0x[0-9a-fA-F]+|[0-9]+)', token) for token in tokens):
                continue
            values = [int(token, 16 if token.lower().startswith('0x') else 10) for token in tokens]
            if any(value > 255 for value in values):
                continue
            if hashlib.sha256(bytes(values)).hexdigest() in KNOWN_FIRMWARE_BLOCKS:
                raise RuntimeError('Known firmware block cannot be distributed: ' + relative.as_posix())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    # Explicit source roots, independent of the maintainer's Git index. No
    # backups, validation reports, virtualenvs, build trees or firmware images.
    paths = [ROOT / name for name in ('README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'CHANGELOG.md', 'pyproject.toml', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md',
                                      '.gitignore', 'start.sh', '启动工具.command')]
    suffixes = {'.py', '.json', '.elf', '.bin', '.c', '.h', '.S', '.ld', '.m', '.txt', '.md', '.sh', '.yml', '.yaml', '.swift', '.png', '.icns'}
    for folder in ('src/a6700_unlock', 'tests', 'docs', 'scripts', 'verified', '.github', 'desktop'):
        for path in (ROOT / folder).rglob('*'):
            relative = path.relative_to(ROOT)
            if any(p.startswith('._') or p in ('__pycache__', '.DS_Store') for p in relative.parts):
                continue
            if path.is_file() and path.suffix in suffixes:
                paths.append(path)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('Source must be a regular file: ' + str(path.relative_to(ROOT)))
        validate_public_file(path, path.relative_to(ROOT))
        if path.suffix == '.elf' and path.parent != ROOT / 'src/a6700_unlock/data/native':
            if path.parent!=ROOT/'src/a6700_unlock/data/fl23' or path.name not in ('reader.elf','writer.elf','files.elf'):
                raise RuntimeError('Unexpected executable resource')
        if path.suffix=='.bin' and path!=ROOT/'src/a6700_unlock/data/fl23/carrier.bin':
            raise RuntimeError('Unexpected binary resource')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / f'a6700-unlock-{__version__}-source.zip'
    with destination.open('xb') as output:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(set(paths)):
                info = zipfile.ZipInfo(f'a6700-unlock-{__version__}/' + path.relative_to(ROOT).as_posix())
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | (0o755 if path.stat().st_mode & 0o111 else 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    print(f'Built {destination} ({len(set(paths))} files)')
    print('SHA-256 ' + hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
