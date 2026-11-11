"""从仓库公开的Pico v2源码准备Arduino sketch，无需上游仓库或相机。"""
import argparse
import hashlib
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    'pico_boot.ino': 'acc13bfc67e78534ffcb1ec5164a1e45b43ce572a51f71d89b9bb489cee2cfd7',
    'serialboot.pio': '951422c52af2dd6f2431fc65cf0df9e9b2102f88b60924d6fb172ad2307c3d22',
    'serialboot.pio.h': '20195e2bec487489df27dd9adb594ea1ce17f4f3d7dc2eac819d0fb7d9c25278',
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--输出目录', '--output', dest='output', type=Path, default=Path('构建产物/pico_boot'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出目录已存在，请选择新目录')
    source = ROOT / '硬件/Pico注入/pico_boot'
    for name, expected in EXPECTED.items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Pico源码已变化，需要更新对应验证：' + name)
    args.output.mkdir(parents=True)
    for name in EXPECTED:
        target = args.output / (args.output.name + '.ino' if name == 'pico_boot.ino' else name)
        shutil.copyfile(source/name, target)
    print('已准备Pico v2源码。编译必须启用-DOPEN_DRAIN，不能启用UART_TX；没有访问设备。')

if __name__ == '__main__':
    main()
