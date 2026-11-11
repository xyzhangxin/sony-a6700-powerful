"""复用已验证的原始区间读取协议；每次最多16MiB，不声称全盘备份。"""
import argparse
import hashlib
import json
from pathlib import Path

from .设备查询 import connect, inspect, query

TARGETS = ('nflasha', 'nflashaB0', 'nflashaB1')


def read_region(device, target, offset, sectors):
    if target not in TARGETS or not 0 <= offset <= 0xffffffff or not 1 <= sectors <= 32768:
        raise ValueError('目标或扇区范围无效，每次限1..32768个512字节扇区')
    if offset + sectors > 0x100000000:
        raise ValueError('扇区地址溢出')
    # 包括引导区在内，都先沿用历史已验证的查表初始化顺序。
    query(device, 'oem partition')
    command = f'oem partition dump {target} {offset:x} {sectors:x}'.encode('ascii')
    if len(command) > 64 or device.write(0x01, command, timeout=5000) != len(command):
        raise IOError('区间读取命令发送不完整')

    def reply(expect_data=None):
        for _ in range(256):
            data = bytes(device.read(0x81, 64, timeout=15000))
            if data[:4] == b'INFO':
                continue
            if data[:4] == b'FAIL':
                raise RuntimeError('相机拒绝读取：' + data[4:].decode(errors='replace'))
            if expect_data is None and data[:4] == b'OKAY':
                return
            if expect_data is not None and data[:4] == b'DATA' and len(data) == 12:
                if int(data[4:], 16) == expect_data:
                    return
            raise ValueError('读取回复类型或长度不符，已停止')
        raise ValueError('读取回复过多')

    reply()
    expected = sectors * 512
    if device.write(0x01, b'upload', timeout=5000) != 6:
        raise IOError('upload命令发送不完整')
    reply(expected)
    result = bytearray()
    while len(result) < expected:
        chunk = bytes(device.read(0x81, min(0x100000, expected-len(result)), timeout=30000))
        if not chunk or len(chunk) > expected-len(result):
            raise ValueError('区间数据长度不符')
        result.extend(chunk)
    reply()
    return bytes(result)


def save_region(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # 读完且最终OKAY后才落盘，同名镜像不覆盖。
    with path.open('xb') as output:
        output.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--目标',choices=TARGETS,required=True)
    parser.add_argument('--起始扇区',type=lambda value:int(value,0),default=0)
    parser.add_argument('--扇区数',type=lambda value:int(value,0),required=True)
    parser.add_argument('--输出',type=Path,required=True)
    parser.add_argument('--双读校验',action='store_true',help='再读一次，逐字节一致后才保存')
    parser.add_argument('--USB库',type=Path)
    args=parser.parse_args()
    if args.输出.exists():
        parser.error('输出文件已存在，不能覆盖')
    if not 1 <= args.扇区数 <= 32768 or not 0 <= args.起始扇区 <= 0xffffffff:
        parser.error('每次限1..32768个扇区，起始扇区必须为非负32位数')
    with connect(args.USB库) as device:
        inspect(device)
        data=read_region(device,args.目标,args.起始扇区,args.扇区数)
        if args.双读校验 and data != read_region(device,args.目标,args.起始扇区,args.扇区数):
            raise ValueError('两次独立读取不一致，未保存为有效备份')
    save_region(args.输出,data)
    print(json.dumps({'目标':args.目标,'起始扇区':args.起始扇区,'扇区数':args.扇区数,
                      '字节数':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                      '双读一致':bool(args.双读校验),'整机备份':False},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
