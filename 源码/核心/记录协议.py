"""BootROM记录长度、地址与CRC编码；协议常量不含机身唯一数据，无设备访问。"""
import hashlib
import json
from pathlib import Path
import struct
import zlib

LOAD = 0xFE020000
UNLOCK = 0x2CF25621
PAYLOAD_SHA256 = '8d8b968940a868116bb3cd77c0f040795c1aae28d2a5d96d119d992498473fcb'
PAYLOAD_SIZE = 20992
PICO_VERSION = b'a6700_pio_od 2'


def encode(kind, value, payload=b''):
    if len(payload) > 4086:
        raise ValueError('Record body exceeds 4096 bytes')
    body = struct.pack('<IHI', 10 + len(payload), kind, value) + payload
    return body + struct.pack('<I', zlib.crc32(body))


def decode(stream):
    result = []
    offset = 0
    while offset < len(stream):
        if len(stream) - offset < 14:
            raise ValueError('Truncated record')
        size, kind, value = struct.unpack_from('<IHI', stream, offset)
        if not 10 <= size <= 4096 or offset + size + 4 > len(stream):
            raise ValueError('Invalid record length')
        body = stream[offset:offset + size]
        checksum, = struct.unpack_from('<I', stream, offset + size)
        if zlib.crc32(body) != checksum:
            raise ValueError('CRC mismatch')
        result.append((kind, value, body[10:]))
        offset += size + 4
    return result


def boot_records(payload):
    if not 0 < len(payload) <= 0x10000:
        raise ValueError('Payload must fit the fixed 64 KiB RAM window')
    records = [encode(0x0123, 0)]
    records.extend(encode(1, LOAD + offset, payload[offset:offset + 512])
                   for offset in range(0, len(payload), 512))
    records.extend((encode(0xFFFF, LOAD), encode(0, UNLOCK), encode(0x0123, 0)))
    return records


def payload_catalog():
    return json.loads(Path(__file__).with_name('载荷清单.json').read_text(encoding='utf-8'))


def verified_payload(path, mode='只读'):
    catalog = payload_catalog()
    if mode not in catalog:
        raise ValueError('未知引导模式')
    expected = catalog[mode]
    payload = Path(path).read_bytes()
    if len(payload) != expected['字节数'] or hashlib.sha256(payload).hexdigest() != expected['sha256']:
        raise ValueError('载荷哈希或长度不符，已阻止发送（blocked）')
    return payload
