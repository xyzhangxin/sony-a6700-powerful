"""Decode the two fixed VFX/MLUT ring headers offline."""
import struct


def ring_header(data, index):
    if len(data) != 40 or index not in (0, 1):
        raise ValueError("Invalid fixed RBUF sample")
    magic, start, capacity, free, oldest, write, auxiliary, count = struct.unpack_from("<4s7I", data)
    expected_start, expected_capacity = ((0x16DC84, 2084), (0x3BBA4C, 4132))[index]
    identity = magic == b"RBUF" and (start, capacity) == (expected_start, expected_capacity)
    end = start + capacity
    used = (write - oldest) % capacity if capacity else None
    bounds = identity and all(start <= v <= end for v in (oldest, write, auxiliary))
    metadata = bool(bounds and free < capacity and free == capacity - 1 - used and
                    ((count == 0 and used == 0) or (count > 0 and count <= used // 16)))
    return dict(index=index, raw_hex=data.hex(), approved_identity=identity,
        approved_metadata=metadata, body_pool_relative_offset=hex(start), body_capacity=capacity,
        free_bytes=free, oldest_pool_relative_cursor=hex(oldest), write_pool_relative_cursor=hex(write),
        auxiliary_cursor_0x18=hex(auxiliary), retained_record_count=count,
        opaque_mutex_0x20_hex=data[32:40].hex(), used_bytes_from_normalized_cursors=used,
        any_cursor_at_inclusive_end=any(v == end for v in (oldest, write, auxiliary)),
        coherent_snapshot_proven=False, cache_visibility_verified=False)
