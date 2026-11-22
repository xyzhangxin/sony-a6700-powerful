"""Offline framing for the fixed stock JPEG (SID 0x64, area 0) log ring.

No device I/O. Compatible payload fields are observations, not proof of a main
JPEG, unique task, complete event history, cache freshness or image processing.
"""
import hashlib
import struct

BODY_START = 0x14820c
CAPACITY = 0x3024
RECORD = struct.Struct('<IHHQ')

def ring_header(raw):
    if len(raw) != 40:
        raise ValueError('Fixed JPEG RBUF header must be 40 bytes')
    magic, start, capacity, free, oldest, write, auxiliary, count = struct.unpack_from('<4s7I', raw)
    identity = magic == b'RBUF' and (start, capacity) == (BODY_START, CAPACITY)
    end = start + capacity
    bounds = identity and all(start <= value <= end for value in (oldest, write, auxiliary))
    used = (write - oldest) % capacity if capacity else None
    metadata = bool(bounds and free < capacity and free == capacity - 1 - used
                    and ((count == 0 and used == 0) or (count > 0 and count <= used // RECORD.size)))
    return {'raw_hex': raw.hex(), 'approved_identity': identity, 'approved_metadata': metadata,
            'body_pool_relative_offset': hex(start), 'body_capacity': capacity, 'free_bytes': free,
            'oldest_pool_relative_cursor': hex(oldest), 'write_pool_relative_cursor': hex(write),
            'auxiliary_cursor_0x18': hex(auxiliary), 'retained_record_count': count,
            'used_bytes_from_normalized_cursors': used, 'opaque_mutex_0x20_hex': raw[32:40].hex()}

def payload_fields(payload):
    if len(payload) != 16:
        return {'format': 'unknown-payload-preserved', 'payload_bytes': len(payload)}
    kind, instance, component, command, stage, result, value, tag = struct.unpack('<BBHHHIHH', payload)
    return {'format': 'compatible-stock-16-byte-JPEG-diagnostic', 'type_byte': kind,
            'caller_first_argument_low8': instance, 'component_u16': component,
            'command_u16': command, 'stage_u16': stage, 'result_u32': hex(result),
            'value_u16': value, 'tag_u16': hex(tag),
            'matches_encoder_call_begin_pattern': (kind, component, command, stage, result, value, tag) == (0, 3, 5, 1, 0, 0, 0xc3),
            'matches_encoder_call_end_pattern': (kind, component, command, stage, tag) == (0, 3, 6, 2, 0xd2),
            'unique_callsite_or_main_photo_proven': False}

def decode_ring(before, body, after):
    result = {'result': 'not-decoded', 'reason': None, 'records': [], 'untrusted_parsed_prefix': [],
              'structural_decode_complete': False, 'body_sha256': hashlib.sha256(body).hexdigest(),
              'header_controls_identical': before[:32] == after[:32], 'opaque_mutex_changed': before[32:] != after[32:],
              'coherent_snapshot_proven': False, 'CP_cache_visibility_verified': False,
              'photo_events_attributed': False, 'JPEG_pixels_verified': False}
    try:
        first, last = ring_header(before), ring_header(after)
    except (ValueError, struct.error) as error:
        result.update(result='container-refused', reason=str(error))
        return result
    result.update(before_header=first, after_header=last)
    if not all(value['approved_identity'] and value['approved_metadata'] for value in (first, last)):
        result.update(result='container-refused', reason='Fixed JPEG identity or occupancy inconsistent')
        return result
    if len(body) != CAPACITY:
        result.update(result='container-refused', reason='JPEG body differs from fixed capacity')
        return result
    if not result['header_controls_identical']:
        result.update(result='unstable-controls', reason='JPEG control bytes changed across copy')
        return result
    cursor = (int(first['oldest_pool_relative_cursor'], 0) - BODY_START) % CAPACITY
    expected_write = (int(first['write_pool_relative_cursor'], 0) - BODY_START) % CAPACITY
    remaining = first['used_bytes_from_normalized_cursors']
    records = []
    def wrapped(offset, length):
        if not 0 <= offset < CAPACITY or not 0 <= length <= CAPACITY:
            raise ValueError('Fixed JPEG ring read exceeded bounds')
        initial = min(length, CAPACITY - offset)
        return body[offset:offset+initial] + body[:length-initial]
    def refuse(reason):
        result.update(result='invalid-record-stream', reason=reason, untrusted_parsed_prefix=records, records=[])
        return result
    for ordinal in range(first['retained_record_count']):
        if remaining < RECORD.size:
            return refuse('Retained range ends before record header')
        length, sid, area, stamp = RECORD.unpack(wrapped(cursor, RECORD.size))
        if length > remaining - RECORD.size:
            return refuse('Record payload exceeds retained range')
        if (sid, area) != (0x64, 0):
            return refuse('Record is outside the approved JPEG subsystem/area')
        position = (cursor + RECORD.size) % CAPACITY
        payload = wrapped(position, length)
        records.append({'ordinal': ordinal, 'body_offset': cursor, 'payload_bytes': length,
            'subsystem': hex(sid), 'stored_area': area, 'raw_timestamp': hex(stamp),
            'header_crosses_ring_end': cursor + RECORD.size > CAPACITY,
            'payload_crosses_ring_end': position + length > CAPACITY,
            'payload_hex': payload.hex(), 'decoded_payload': payload_fields(payload)})
        cursor = (cursor + RECORD.size + length) % CAPACITY
        remaining -= RECORD.size + length
    if remaining or cursor != expected_write:
        return refuse('Record count, occupied bytes and write cursor disagree')
    result.update(result='structurally-decoded-observation', structural_decode_complete=True, records=records,
                  parsed_records=len(records), consumed_bytes=first['used_bytes_from_normalized_cursors'],
                  raw_timestamps_nondecreasing=all(int(a['raw_timestamp'], 0) <= int(b['raw_timestamp'], 0) for a, b in zip(records, records[1:])),
                  acceptance_scope='Stock record framing and compatible fields only; no complete/main-photo attribution')
    return result
