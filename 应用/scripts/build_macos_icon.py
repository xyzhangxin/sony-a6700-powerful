"""Convert the checked-in brand PNG to the macOS icon, without extra packages."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    brand = ROOT / 'desktop/品牌'
    with tempfile.TemporaryDirectory(prefix='powerful6700-icon-') as folder:
        iconset = Path(folder) / 'Powerful6700.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = size * scale
                name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
                subprocess.run(['sips', '-z', str(pixels), str(pixels),
                                str(brand / 'Powerful6700.png'), '--out', str(iconset / name)],
                               check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o',
                        str(brand / 'Powerful6700.icns')], check=True)
    print('Built:', brand / 'Powerful6700.icns')


if __name__ == '__main__':
    main()
