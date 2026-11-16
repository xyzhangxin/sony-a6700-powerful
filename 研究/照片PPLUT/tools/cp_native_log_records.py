"""Conservative fixed VFX/MLUT ring decoding; no device operations.

GPL-3.0-only. Structural decoding never establishes cache visibility, a
coherent snapshot, complete logging, photo identity or callback completion.
"""
import hashlib
import struct

from cp_native_log_ring_headers import ring_header

RINGS = ((0x67, 0), (0x2AE, 1))
RECORD_HEADER = struct.Struct("<IHHQ")


def payload_fields(index, payload):
    if index == 0 and len(payload) == 20:
        selector, event, argument, stack_word = struct.unpack_from("<HIII", payload, 6)
        return dict(format="stock-20-byte-VFX-diagnostic",
            time_formatter_prefix_hex=payload[:5].hex(), caller_byte=payload[5],
            caller_low16_at_6=selector, event_word=hex(event),
            caller_word_at_12=hex(argument), caller_stack_word_at_16=hex(stack_word),
            stack_word_is_verified_photo_handle=False)
    if index == 1 and len(payload) == 24:
        words = struct.unpack("<6I", payload)
        return dict(format="24-byte-MLUT-payload", words_u32=[hex(word) for word in words],
            matches_known_begin_marker=words[0] == 0x10038,
            matches_known_end_marker=words[0] == 0x20038,
            matches_known_marker=words[0] in (0x10038, 0x20038),
            matching_photo_task_or_callback_generation_proven=False)
    return dict(format="unknown-payload-preserved", payload_bytes=len(payload))


def decode_ring(index, before, body, after):
    if index not in (0, 1):
        raise ValueError("Only the two fixed diagnostic rings are supported")
    result = dict(index=index, result="not-decoded", reason=None, records=[],
        untrusted_parsed_prefix=[], structural_decode_complete=False,
        coherent_snapshot_proven=False, CP_cache_visibility_verified=False,
        photo_events_attributed=False, callback_drain_proven=False,
        body_sha256=hashlib.sha256(body).hexdigest(),
        header_controls_identical=before[:32] == after[:32],
        opaque_mutex_changed=before[32:] != after[32:])
    try:
        first, last = ring_header(before, index), ring_header(after, index)
    except (ValueError, struct.error) as error:
        result.update(result="container-refused", reason=str(error))
        return result
    result.update(before_header=first, after_header=last)
    if not all(header["approved_identity"] and header["approved_metadata"] for header in (first, last)):
        result.update(result="container-refused", reason="Header identity, bounds or occupancy is invalid")
        return result
    capacity = first["body_capacity"]
    if len(body) != capacity:
        result.update(result="container-refused", reason="Body length differs from the fixed capacity")
        return result
    if not result["header_controls_identical"]:
        result.update(result="unstable-controls", reason="Control bytes changed across the fixed body copy")
        return result
    base = int(first["body_pool_relative_offset"], 0)
    cursor = (int(first["oldest_pool_relative_cursor"], 0) - base) % capacity
    expected_write = (int(first["write_pool_relative_cursor"], 0) - base) % capacity
    remaining = first["used_bytes_from_normalized_cursors"]
    count = first["retained_record_count"]
    expected_sid, expected_area = RINGS[index]
    records = []

    def wrapped(offset, length):
        assert 0 <= offset < capacity and 0 <= length <= capacity
        initial = min(length, capacity - offset)
        return body[offset:offset + initial] + body[:length - initial]

    def refuse(reason):
        result.update(result="invalid-record-stream", reason=reason,
                      untrusted_parsed_prefix=records, records=[])
        return result

    for ordinal in range(count):
        if remaining < RECORD_HEADER.size:
            return refuse("Retained range ends before the next complete record header")
        length, sid, area, timestamp = RECORD_HEADER.unpack(wrapped(cursor, RECORD_HEADER.size))
        if length > remaining - RECORD_HEADER.size:
            return refuse("Record payload length exceeds the remaining retained range")
        if (sid, area) != (expected_sid, expected_area):
            return refuse("Record subsystem or stored area differs from its qualified ring")
        payload_cursor = (cursor + RECORD_HEADER.size) % capacity
        payload = wrapped(payload_cursor, length)
        records.append(dict(ordinal=ordinal, body_offset=cursor, payload_bytes=length,
            subsystem=hex(sid), stored_area=area, raw_timestamp=hex(timestamp),
            header_crosses_ring_end=cursor + RECORD_HEADER.size > capacity,
            payload_crosses_ring_end=payload_cursor + length > capacity,
            payload_hex=payload.hex(), decoded_payload=payload_fields(index, payload)))
        cursor = (cursor + RECORD_HEADER.size + length) % capacity
        remaining -= RECORD_HEADER.size + length
    if remaining or cursor != expected_write:
        return refuse("Record count, consumed bytes and final write cursor do not agree")
    result.update(result="structurally-decoded-observation", structural_decode_complete=True,
        records=records, parsed_records=len(records), consumed_bytes=first["used_bytes_from_normalized_cursors"],
        raw_timestamps_nondecreasing=all(int(a["raw_timestamp"], 0) <= int(b["raw_timestamp"], 0)
            for a, b in zip(records, records[1:])),
        acceptance_scope="record framing only; equal control bytes do not exclude ABA, stale cache or concurrent body changes")
    return result
