"""校验固定RAM程序身份并查询分区表；写入不从此入口执行。"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path


def query(device, command):
    if command not in ('getvar:product', 'getvar:version', 'oem partition'):
        raise ValueError('Command outside the published query allowlist')
    encoded = command.encode('ascii')
    if device.write(0x01, encoded, timeout=5000) != len(encoded):
        raise IOError('Incomplete fastboot command write')
    info = []
    for _ in range(256):
        response = bytes(device.read(0x81, 64, timeout=15000))
        tag, body = response[:4], response[4:].decode(errors='replace')
        if tag == b'INFO':
            info.append(body)
        elif tag == b'OKAY':
            return {'info': info, 'okay': body}
        elif tag == b'FAIL':
            raise RuntimeError('RAM service rejected query: ' + body)
        else:
            raise ValueError('Unexpected fastboot response; query stopped')
    raise RuntimeError('Too many fastboot info responses')


def inspect(device, table=False, mode="只读"):
    identity = query(device, 'getvar:product')
    from .记录协议 import payload_catalog
    if identity['okay'] != payload_catalog()[mode]['产品标识']:
        raise ValueError('RAM payload identity mismatch; no partition query sent')
    result = {'product': identity['okay'], 'version': query(device, 'getvar:version')['okay']}
    if table:
        result['partitions'] = query(device, 'oem partition')
    return result


@contextmanager
def connect(libusb=None):
    """只连接唯一匹配接口；由调用者明确选择查询或读取动作。"""
    import usb.core
    import usb.util
    import usb.backend.libusb1
    if libusb and not libusb.is_file():
        raise ValueError('USB库参数必须指向实际存在的系统库')
    backend = usb.backend.libusb1.get_backend(find_library=(lambda _: str(libusb.resolve())) if libusb else None)
    if backend is None:
        raise RuntimeError('System libusb missing; see 操作手册.md')
    devices = list(usb.core.find(find_all=True, idVendor=0x18D1, idProduct=0x4EE0, backend=backend))
    if len(devices) != 1:
        raise RuntimeError(f'Expected exactly one RAM fastboot device, found {len(devices)}')
    device = devices[0]
    claimed = False
    try:
        try:
            config = device.get_active_configuration()
        except usb.core.USBError:
            device.set_configuration()
            config = device.get_active_configuration()
        interface = config[(0, 0)]
        if (interface.bInterfaceClass, interface.bInterfaceSubClass, interface.bInterfaceProtocol) != (0xff, 0x42, 3):
            raise ValueError('Unexpected USB interface')
        endpoints = {endpoint.bEndpointAddress for endpoint in interface}
        if not {0x01, 0x81}.issubset(endpoints):
            raise ValueError('Expected bulk endpoints missing')
        usb.util.claim_interface(device, 0)
        claimed = True
        yield device
    finally:
        if claimed:
            usb.util.release_interface(device, 0)
        usb.util.dispose_resources(device)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--分区表','--partitions',dest='partitions',action='store_true',help='身份匹配后初始化eMMC并查表')
    parser.add_argument('--USB库','--libusb',dest='libusb',type=Path,help='自动发现失败时指定系统libusb')
    args=parser.parse_args()
    with connect(args.libusb) as device:
        print(json.dumps(inspect(device,args.partitions),ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
