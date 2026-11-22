"""Test ring decoding with stock CP writer bytes and explicit corruption cases.

GPL-3.0-only. No device backend, live addresses, DMA or camera operation.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
from unittest.mock import patch

import cp_native_log_records as decoder
import emulate_cp_native_log_protocol as protocol

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cp", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        p.error("Preserve previous evidence")
    cp = args.cp.read_bytes()
    assert protocol.sha(cp) == protocol.CP_SHA
    targets = ((0x67, 0, 0x16DC84, 2084, 20), (0x2AE, 1, 0x3BBA4C, 4132, 24))
    cases = []

    class Storage(protocol.CP):
        def __init__(self, index):
            self.index = index
            super().__init__(cp, capacity=targets[index][3])
            self.u.mem_write(protocol.RING, b"RBUF")
            self.u.mem_write(protocol.NODE + 14, struct.pack("<hh", 2, 1 if index == 0 else 0))
            self.put(protocol.NODE + 0x18, protocol.RING - protocol.POOL)
            self.put(protocol.NODE + 0x20, protocol.RING - protocol.POOL)

        def guard(self, uc, at, size, user):
            if at == 0xBD0942:
                assert self.r(0) == targets[self.index][0]
                self.ret(protocol.NODE)
            else:
                super().guard(uc, at, size, user)

    def emit(machine, number, payload=None):
        index = machine.index
        if payload is None:
            payload = (bytes.fromhex("66aabbcc55ff0400000000410000000002000400") if index == 0
                       else struct.pack("<6I", 0x10038 if number % 2 else 0x20038, 0, 1, 2, 3, 4))
        message = bytearray(24 + len(payload))
        struct.pack_into("<HHII", message, 0, 2, targets[index][0], 1, len(payload))
        struct.pack_into("<Q", message, 16, 1_000_000 + number)
        message[24:] = payload
        machine.emit(bytes(message))

    def snapshot(machine):
        header = bytearray(machine.u.mem_read(protocol.RING, 40))
        start = targets[machine.index][2]
        delta = start - (protocol.CONTENT - protocol.POOL)
        for offset in (4, 16, 20, 24):
            struct.pack_into("<I", header, offset, struct.unpack_from("<I", header, offset)[0] + delta)
        body = bytes(machine.u.mem_read(protocol.CONTENT, machine.capacity))
        return bytes(header), body

    def run(index, header, body, after=None):
        return decoder.decode_ring(index, header, body, header if after is None else after)

    def ok(name, condition):
        assert condition, name
        cases.append(name)

    fixtures = []
    with patch.object(protocol, "CONTENT", protocol.RING + 40):
        for index in (0, 1):
            machine = Storage(index)
            initial = 2040 if index == 1 else 0
            for offset in (16, 20, 24):
                machine.put(protocol.RING + offset, protocol.CONTENT - protocol.POOL + initial)
            for number in range(1, 4):
                emit(machine, number)
            header, body = snapshot(machine)
            result = run(index, header, body)
            ok("stock-writer-three-records-" + str(index),
               result["structural_decode_complete"] and result["parsed_records"] == 3)
            ok("original-request-area-one-stored-area-" + str(index),
               {row["stored_area"] for row in result["records"]} == {targets[index][1]})
            fixtures.append(dict(index=index, before_header_hex=header.hex(), body_hex=body.hex(),
                after_header_hex=header.hex(), expected_records=3,
                emitted_by="bounded original 0x55b044 dispatch and 0x55c2fc writer",
                pointer_rebasing="header body/oldest/write/auxiliary offsets rebased from emulated pool to fixed qualification layout"))
            emit(machine, 4)
            changed, _ = snapshot(machine)
            ok("changed-valid-controls-refuse-decoding-" + str(index),
               run(index, header, body, changed)["result"] == "unstable-controls")
            opaque = header[:32] + b"OPAQUE!!"
            ok("opaque-mutex-does-not-imply-publication-sequence-" + str(index),
               run(index, header, body, opaque)["structural_decode_complete"])
            for initial, kind in ((machine.capacity - 7, "header"), (machine.capacity - 21, "payload")):
                edge = Storage(index)
                for offset in (16, 20, 24):
                    edge.put(protocol.RING + offset, protocol.CONTENT - protocol.POOL + initial)
                emit(edge, 1)
                edge_header, edge_body = snapshot(edge)
                observed = run(index, edge_header, edge_body)
                ok("original-" + kind + "-wrap-" + str(index),
                   observed["structural_decode_complete"] and observed["records"][0][kind + "_crosses_ring_end"])
            evict = Storage(index)
            total = (evict.capacity - 1) // (16 + targets[index][4]) + 3
            for number in range(1, total + 1):
                emit(evict, number)
            eh, eb = snapshot(evict)
            observed = run(index, eh, eb)
            ok("original-complete-record-eviction-" + str(index),
               observed["structural_decode_complete"] and observed["parsed_records"] == total - 3 and
               int(observed["records"][0]["raw_timestamp"], 0) == 1_000_004)

        aba = Storage(0)
        observations = {}
        for number in range(1, 6):
            emit(aba, number, bytes(1026))
            if number in (3, 5):
                observations[number] = snapshot(aba)
        ah, ab = observations[3]
        bh, bb = observations[5]
        a, b = run(0, ah, ab), run(0, bh, bb)
        ok("equal-complete-fixed-capacity-headers-can-surround-changed-body",
           ah == bh and ab != bb and a["structural_decode_complete"] and b["structural_decode_complete"])
        ok("structural-success-never-proves-coherence-or-photo-or-callback",
           not any(result[key] for result in (a, b) for key in
                   ("coherent_snapshot_proven", "CP_cache_visibility_verified", "photo_events_attributed", "callback_drain_proven")))

    for fixture in fixtures:
        index = fixture["index"]
        header, body = bytes.fromhex(fixture["before_header_hex"]), bytes.fromhex(fixture["body_hex"])
        start = struct.unpack_from("<I", header, 4)[0]
        cursor = (struct.unpack_from("<I", header, 16)[0] - start) % len(body)

        def corrupt(offset, value, fmt):
            changed = bytearray(body)
            for number, byte in enumerate(struct.pack(fmt, value)):
                changed[(cursor + offset + number) % len(body)] = byte
            return bytes(changed)

        for label, offset, value, fmt in (("huge-payload", 0, 0xFFFFFFFF, "<I"),
            ("foreign-subsystem", 4, 0xFFFF, "<H"), ("wrong-stored-area", 6, 9, "<H"),
            ("shorter-record-leaves-unconsumed-range", 0, 0, "<I")):
            result = run(index, header, corrupt(offset, value, fmt))
            ok("reject-" + label + "-" + str(index),
               result["result"] == "invalid-record-stream" and not result["records"])
        fewer = bytearray(header)
        struct.pack_into("<I", fewer, 28, 2)
        result = run(index, bytes(fewer), body)
        ok("count-cursor-disagreement-not-resynchronized-" + str(index),
           result["result"] == "invalid-record-stream" and not result["records"])
        ok("truncated-body-refused-" + str(index), run(index, header, body[:-1])["result"] == "container-refused")
        ok("extra-body-refused-" + str(index), run(index, header, body + b"\0")["result"] == "container-refused")

    report = dict(schema="d6700/original-log-record-decoder-acceptance/1",
        recorded_utc=datetime.now(timezone.utc).isoformat(), result="passed", camera_access=False,
        backend_constructed=False, CP_sha256=protocol.CP_SHA,
        source_sha256={Path(__file__).name: protocol.sha(Path(__file__).read_bytes()),
            Path(decoder.__file__).name: protocol.sha(Path(decoder.__file__).read_bytes()),
            Path(protocol.__file__).name: protocol.sha(Path(protocol.__file__).read_bytes())},
        case_count=len(cases), cases=cases, fixtures=fixtures,
        named_fixtures=["registered subsystem lookup", "writer mutex", "message transport/free",
                        "libc copy/format", "message payloads and timestamps", "initial cursor positions",
                        "pool-relative pointer rebasing"],
        hardware_log_body_collected=False, coherent_snapshot_proven=False,
        next_step="Compiled fixed-body reader testing uses these independently emitted ring bytes")
    with args.out.open("x", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(result="passed", cases=len(cases), original_writer_fixtures=len(fixtures), camera_access=False)))


if __name__ == "__main__":
    main()
