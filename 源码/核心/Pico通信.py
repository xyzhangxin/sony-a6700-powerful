"""Pico同步数据包接口；仅实现版本、载波、记录、关闭上拉和电平采样。"""
import struct

from .记录协议 import PICO_VERSION


class Pico:
    def __init__(self, serial_port):
        self.port = serial_port

    def _read(self, length):
        data = bytearray()
        while len(data) < length:
            chunk = self.port.read(length - len(data))
            if not chunk:
                raise TimeoutError('Pico response timed out')
            data.extend(chunk)
        return bytes(data)

    def _exchange(self, command, body=b''):
        packet = bytes([command]) + struct.pack('<H', len(body)) + body
        if self.port.write(packet) != len(packet):
            raise IOError('Incomplete Pico packet write')
        header = self._read(3)
        length, = struct.unpack('<H', header[1:])
        if length > 4100:
            raise ValueError('Oversized Pico reply')
        response = self._read(length)
        if header[0] == 0x82:
            raise RuntimeError('Pico rejected command: ' + response.decode(errors='replace'))
        if header[0] != 0x81:
            raise ValueError('Unexpected Pico event')
        return response

    def ping(self):
        if self._exchange(1) != PICO_VERSION:
            raise ValueError('Expected Pico firmware a6700_pio_od 2')

    def carrier(self, enabled):
        self._exchange(3, bytes([bool(enabled)]))

    def disable_pull(self):
        self._exchange(7, b'\x00')

    def sense(self):
        result = self._exchange(8)
        if result not in (b'\x00', b'\x01'):
            raise ValueError('Invalid GP28 sense response')
        return result == b'\x01'

    def send_record(self, record):
        if not 14 <= len(record) <= 4100:
            raise ValueError('Invalid record size')
        self._exchange(4, record)
