"""Execute stock JPEG encode-call diagnostics, producer and ring writer offline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
from unittest.mock import patch

import unicorn.arm_const as arm
import emulate_cp_native_log_protocol as stock
import cp_jpeg_log_records as decoder

TEMP = 0x16aba08

class Producer(stock.CP):
    def __init__(self, raw, *, submit_result=0, wait_result=0, **faults):
        super().__init__(raw, **faults)
        self.sent, self.allocations, self.native_calls = [], 0, []
        self.submit_result, self.wait_result = submit_result, wait_result
        self.u.mem_map(0x16ab000, 8192)
        self.u.mem_map(0x19c2000, 4096)
        self.tick = 0x123456789abcdef

    def on_write(self, uc, access, address, size, value, user):
        if TEMP <= address and address + size <= TEMP + 128 or address == 0x19c2010:
            self.writes.append({'address': hex(address), 'bytes': size, 'pc': hex(uc.reg_read(arm.UC_ARM_REG_PC))})
            return
        super().on_write(uc, access, address, size, value, user)

    def guard(self, uc, address, size, user):
        if 0xb5b578 <= address < 0xb5b6a4 or 0x5226ec <= address < 0x52278c:
            return
        if address == 0xbd16e0:
            self.u.reg_write(arm.UC_ARM_REG_R1, self.tick >> 32)
            self.ret(self.tick & 0xffffffff)
        elif address == 0x559190:
            key, output, length, wait = [self.r(number) for number in range(4)]
            assert key in (0x8f013a, 0x8f0313) and wait == 0
            result = self.faults.get('allocation' if key == 0x8f013a else 'fallback_allocation', 0)
            self.native_calls.append({'fixture': 'diagnostic_message_allocation', 'key': hex(key), 'bytes': length, 'result': result})
            if not result:
                self.put(output, stock.DATA + 0x4000 + self.allocations * 0x2000)
            self.allocations += 1
            self.ret(result)
        elif address == 0x558ac4:
            assert self.r(0) == 0x8f013a
            pointer = self.r(1)
            header = bytes(self.u.mem_read(pointer, 24))
            kind, sid, area, length = struct.unpack_from('<HHII', header)
            assert (kind, sid) == (2, 0x64)
            packet = header + bytes(self.u.mem_read(pointer + 24, length))
            self.sent.append(packet)
            self.native_calls.append({'fixture': 'diagnostic_message_send', 'area': area, 'payload_bytes': length, 'result': self.faults.get('send', 0)})
            self.ret(self.faults.get('send', 0))
        elif address in (0xbcfbc4, 0xbcfbbc, 0x55928c, 0x67acc0):
            self.native_calls.append({'fixture': 'message_prefix_fallback_disposal_or_text_diagnostic', 'api': hex(address)})
            self.ret(0)
        elif address == 0x52279c:
            assert self.r(0) == 3
            pointer = self.word(self.u.reg_read(arm.UC_ARM_REG_SP) + 16)
            if not self.submit_result:
                self.u.mem_write(pointer, b'\x07')
            self.native_calls.append({'fixture': 'JPEG_encoder_submission', 'result': self.submit_result, 'real_image_encoded': False})
            self.ret(self.submit_result)
        elif address == 0xb5a76e:
            self.native_calls.append({'fixture': 'JPEG_slot_parameter_lookup', 'slot': self.r(1)})
            self.ret(0x1234)
        elif address == 0xb59be0:
            self.native_calls.append({'fixture': 'JPEG_encoder_wait_or_result_endpoint', 'slot': self.r(0), 'result': self.wait_result})
            self.ret(self.wait_result)
        else:
            super().guard(uc, address, size, user)

    def packed(self, kind=0, instance=0x78, component=3, command=5, stage=1, result=0, value=0, tag=0xc3):
        for number, word in enumerate((stage, result, value, tag)):
            self.put(stock.TOP + number * 4, word)
        self.call(0xb5b632, kind, instance, component, command)
        return self.sent

    def encode(self):
        for number, word in enumerate((0, 0)):
            self.put(stock.TOP + number * 4, word)
        self.call(0x5226ec, 0x12345678, stock.DATA + 0x800, 0)
        return self.r(0)

class Storage(stock.CP):
    def __init__(self, raw):
        super().__init__(raw, capacity=decoder.CAPACITY)
        self.u.mem_write(stock.RING, b'RBUF')
        self.u.mem_write(stock.NODE + 14, struct.pack('<hh', 3, 1))
        self.put(stock.NODE + 0x18, stock.RING - stock.POOL)

    def guard(self, uc, address, size, user):
        if address == 0xbd0942:
            assert self.r(0) == 0x64
            self.ret(stock.NODE)
        else:
            super().guard(uc, address, size, user)

    def snapshot(self):
        header = bytearray(self.u.mem_read(stock.RING, 40))
        adjustment = decoder.BODY_START - (stock.CONTENT - stock.POOL)
        for offset in (4, 16, 20, 24):
            struct.pack_into('<I', header, offset, struct.unpack_from('<I', header, offset)[0] + adjustment)
        return bytes(header), bytes(self.u.mem_read(stock.CONTENT, decoder.CAPACITY))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cp', type=Path, required=True)
    parser.add_argument('--layout-report', type=Path, required=True, help='Output of emulate_cp_log_directory.py')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raw = args.cp.read_bytes()
    assert stock.sha(raw) == stock.CP_SHA
    layout = json.loads(args.layout_report.read_bytes())
    assert layout['schema'] == 'd6700/original-CP-log-directory-emulation/1'
    assert layout['CP_sha256'] == stock.CP_SHA and layout['original_lookup_entries_checked'] == 79
    node = next(row for row in layout['nodes'] if row['sid'] == '0x64')
    assert (int(node['rings'][0]['offset'], 0) + 40, int(node['rings'][0]['capacity'], 0)) == (decoder.BODY_START, decoder.CAPACITY)
    cases = []
    def ok(name, condition=True):
        assert condition, name
        cases.append(name)
    producer = Producer(raw)
    packets = producer.packed()
    expected = struct.pack('<BBHHHIHH', 0, 0x78, 3, 5, 1, 0, 0, 0xc3)
    ok('original_JPEG_packer_producer_SID64_area0_payload16', len(packets) == 1 and packets[0][24:] == expected)
    ok('original_timestamp_preserved', struct.unpack_from('<Q', packets[0], 16)[0] == producer.tick)
    duplicate = Producer(raw)
    duplicate.packed(kind=0x40)
    ok('original_type40_duplicates_to_area1', [struct.unpack_from('<I', packet, 4)[0] for packet in duplicate.sent] == [0, 1])
    encode = Producer(raw)
    ok('original_encoder_wrapper_returns_success_fixture', encode.encode() == 0)
    fields = [decoder.payload_fields(packet[24:]) for packet in encode.sent]
    ok('original_encoder_call_begin_and_end_patterns', len(fields) == 2 and fields[0]['matches_encoder_call_begin_pattern'] and fields[1]['matches_encoder_call_end_pattern'])
    ok('encoder_slot_low16_preserved', fields[1]['value_u16'] == 7)
    for fault in ('submit', 'wait'):
        machine = Producer(raw, **{fault + '_result': 7})
        ok('native_encoder_' + fault + '_failure_status', machine.encode() == 7 and decoder.payload_fields(machine.sent[-1][24:])['result_u32'] == '0x7')
    missing = Producer(raw, allocation=7)
    ok('allocation_failure_does_not_become_encoding_failure', missing.encode() == 0 and not missing.sent)
    ok('allocation_failure_uses_fallback', any(row.get('api') == '0xbcfbbc' for row in missing.native_calls))
    for invalid in (0, 0xfe1):
        machine = Producer(raw)
        machine.call(0xb5b578, 0, invalid)
        ok('producer_rejects_invalid_payload_size_' + str(invalid), machine.allocations == 0)
    fixture = None
    with patch.object(stock, 'CONTENT', stock.RING + 40):
        for initial in (0, decoder.CAPACITY - 8, 3320):
            machine = Storage(raw)
            for offset in (16, 20, 24):
                machine.put(stock.RING + offset, stock.CONTENT - stock.POOL + initial)
            for packet in encode.sent:
                machine.emit(packet)
            header, body = machine.snapshot()
            decoded = decoder.decode_ring(header, body, header)
            ok('original_writer_and_decoder_at_' + str(initial), decoded['structural_decode_complete'] and decoded['parsed_records'] == 2)
            ok('original_writer_stored_area0_at_' + str(initial), all(row['stored_area'] == 0 and row['subsystem'] == '0x64' for row in decoded['records']))
            if initial == decoder.CAPACITY - 8:
                ok('record_header_wrap_is_observed', decoded['records'][0]['header_crosses_ring_end'])
            if initial == 3320:
                fixture = {'before_header_hex': header.hex(), 'body_hex': body.hex(), 'after_header_hex': header.hex(), 'expected_records': 2}
        machine = Storage(raw)
        for number in range(390):
            packet = bytearray(encode.sent[number % 2])
            struct.pack_into('<Q', packet, 16, number + 1)
            machine.emit(bytes(packet))
        header, body = machine.snapshot()
        decoded = decoder.decode_ring(header, body, header)
        ok('original_writer_evicts_whole_records_at_real_capacity', decoded['structural_decode_complete'] and decoded['parsed_records'] == 385)
        ok('decoder_does_not_claim_full_history', decoded['records'][0]['raw_timestamp'] == hex(6))
    header = bytes.fromhex(fixture['before_header_hex'])
    body = bytes.fromhex(fixture['body_hex'])
    for label, offset, value, form in (('bad_magic', 0, 0, '<I'), ('bad_capacity', 8, decoder.CAPACITY + 1, '<I'), ('bad_free', 12, 0, '<I'), ('bad_count', 28, 1000, '<I')):
        bad = bytearray(header)
        struct.pack_into(form, bad, offset, value)
        ok(label, not decoder.decode_ring(bytes(bad), body, bytes(bad))['structural_decode_complete'])
    changed = bytearray(header)
    changed[12] ^= 1
    ok('changed_controls_reject_copy', not decoder.decode_ring(header, body, bytes(changed))['structural_decode_complete'])
    changed = bytearray(header)
    changed[32] ^= 1
    ok('opaque_mutex_is_preserved_not_sequence', decoder.decode_ring(header, body, bytes(changed))['structural_decode_complete'])
    cursor = (int(decoder.ring_header(header)['oldest_pool_relative_cursor'], 0) - decoder.BODY_START) % decoder.CAPACITY
    for label, offset, data in (('foreign_sid', 4, struct.pack('<H', 0x67)), ('foreign_area', 6, b'\1\0'), ('oversized_payload', 0, struct.pack('<I', decoder.CAPACITY))):
        bad = bytearray(body)
        for number, byte in enumerate(data):
            bad[(cursor + offset + number) % decoder.CAPACITY] = byte
        result = decoder.decode_ring(header, bytes(bad), header)
        ok(label, not result['structural_decode_complete'] and not result['records'])
    ok('short_body_rejected', not decoder.decode_ring(header, body[:-1], header)['structural_decode_complete'])
    output = {'schema': 'd6700/stock-JPEG-native-record-proof/1', 'recorded_utc': datetime.now(timezone.utc).isoformat(),
        'result': 'passed', 'camera_access': False, 'device_backend_constructed': False, 'CP_sha256': stock.CP_SHA,
        'source_sha256': {path.name: stock.sha(path.read_bytes()) for path in (Path(__file__), Path(decoder.__file__), Path(stock.__file__))},
        'case_count': len(cases), 'cases': cases, 'original_encoder_calls_executed': ['0x5226ec..0x52278c'],
        'original_producer_executed': ['0xb5b578..0xb5b6a4'], 'original_ring_dispatch_writer_executed': ['0x55b044', '0x55c2fc'],
        'JPEG_stock_directory': node, 'fixtures': [fixture], 'encoder_message_payloads': [packet[24:].hex() for packet in encode.sent],
        'limitations': ['JPEG engine submission/slot lookup/wait, time, message APIs, registered lookup and mutexes are named fixtures',
            'Original encode-call wrapper executes; no image is encoded or processed', 'Compatible markers do not prove a unique main JPEG, completion drain or lossless full history',
            'The fixed JPEG descriptor/RBUF addresses are stock-builder hypotheses pending native gates on camera']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'result': output['result'], 'cases': len(cases), 'JPEG_capacity': decoder.CAPACITY, 'stock_encoder_messages': output['encoder_message_payloads']}, ensure_ascii=False))

if __name__ == '__main__':
    main()
