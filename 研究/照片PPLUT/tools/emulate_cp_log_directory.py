"""Offline stock CP directory construction, lookup and writer-prefix execution.

Source TLVs are the stock file's contiguous candidate configuration, not a
measured live directory. The default 60-byte header is a local fixture.
Allocator, reset-state check, zero-fill and mutex initialization are named
fixtures. No device backend, live pointer, USB or DMA operation is used.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_LITTLE_ENDIAN
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_EXEC
import unicorn.arm_const as reg

BASE = 0x108000
CP_SHA = "61b7fe5c0ce227ea865d799b81fe77ae359b7c5dcd39d13c9c2241d9cad23559"
CFG, CFG_BYTES = 0x05000000, 0x800000
STACK, TOP, HEAP, MSG, RETURN = 0x08000000, 0x0800F000, 0x09000000, 0x09002000, 0x0A000000
G_CFG, G_POOL, TABLE, COUNT = 0x19CA488, 0x19CA490, 0x1ACD864, 0x1ACD868
BUILDER = (0x55C6D8, 0x55CA46)
LOOKUP = (0xBD0942, 0xBD09B8)
PREFIX = (0x55C2FC, 0x55C37C)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def u32(n):
    return struct.pack("<I", n & 0xFFFFFFFF)


def source_candidates(raw):
    matches = []
    at = 0
    while True:
        at = raw.find(b"SUBS", at)
        if at < 0:
            break
        if at >= 4 and at + 12 <= len(raw):
            length = struct.unpack_from("<I", raw, at - 4)[0]
            sid, capacity = struct.unpack_from("<II", raw, at + 4)
            if (20 <= length <= 0x200 and length % 4 == 0 and
                    sid < 0x10000 and 0x30 < capacity <= CFG_BYTES):
                sizes = struct.unpack_from("<" + "I" * ((length - 16) // 4), raw, at + 12)
                matches.append(dict(address=at - 4 + BASE, length=length, sid=sid,
                                    capacity=capacity, sizes=sizes))
        at += 4
    assert len(matches) == 79
    assert len({m["sid"] for m in matches}) == 79
    assert all(a["address"] + a["length"] == b["address"] for a, b in zip(matches, matches[1:]))
    return matches


class Machine:
    def __init__(self, raw, header, warm=False):
        self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_LITTLE_ENDIAN)
        image_size = (len(raw) + 4095) & ~4095
        self.u.mem_map(BASE, image_size)
        self.u.mem_write(BASE, raw)
        self.u.mem_protect(BASE, image_size, UC_PROT_READ | UC_PROT_EXEC)
        for address, size in ((CFG, CFG_BYTES), (STACK, 0x10000), (HEAP, 0x4000),
                              (RETURN, 4096), (0x19CA000, 4096), (0x1ACD000, 8192)):
            self.u.mem_map(address, size)
        self.u.mem_write(CFG, b"\xA5" * CFG_BYTES)
        self.u.mem_write(CFG, header)
        self.put(G_CFG, CFG)
        self.put(G_POOL, CFG)
        self.warm = warm
        self.events = []
        self.writes = []
        self.instructions = 0
        self.stop_at = set()
        self.stopped = None
        self.u.hook_add(UC_HOOK_CODE, self.guard)
        self.u.hook_add(UC_HOOK_MEM_WRITE, self.on_write)

    def r(self, n):
        return self.u.reg_read(getattr(reg, "UC_ARM_REG_R" + str(n)))

    def put(self, address, value):
        self.u.mem_write(address, u32(value))

    def get(self, address):
        return struct.unpack("<I", self.u.mem_read(address, 4))[0]

    def ret(self, value=0):
        self.u.reg_write(reg.UC_ARM_REG_R0, value & 0xFFFFFFFF)
        self.u.reg_write(reg.UC_ARM_REG_PC, self.u.reg_read(reg.UC_ARM_REG_LR))

    def on_write(self, uc, access, address, size, value, user):
        assert any(start <= address < address + size <= start + length for start, length in
                   ((CFG + 60, CFG_BYTES - 60), (STACK, 0x10000), (HEAP, 0x4000),
                    (0x19CA000, 4096), (0x1ACD000, 8192))), hex(address)
        if not STACK <= address < STACK + 0x10000:
            self.writes.append((address, size))

    def guard(self, uc, address, size, user):
        self.instructions += 1
        if address in self.stop_at:
            self.stopped = address
            uc.emu_stop()
            return
        if any(a <= address < z for a, z in (BUILDER, LOOKUP, PREFIX)):
            return
        if address == 0xBCFFA6:
            self.events.append(dict(fixture="reset_state", returns=int(self.warm)))
            self.ret(int(self.warm))
        elif address == 0x60C770:
            assert self.r(0) == 1024
            self.events.append(dict(fixture="allocate_hash_table", bytes=self.r(0), address=hex(HEAP)))
            self.ret(HEAP)
        elif address == 0x612934:
            address, count = self.r(0), self.r(1)
            assert 0 <= count <= 1024
            assert (HEAP <= address <= address + count <= HEAP + 1024 or
                    CFG + 60 <= address <= address + count <= CFG + CFG_BYTES)
            self.u.mem_write(address, bytes(count))
            self.events.append(dict(fixture="zero_fill", address=hex(address), bytes=count))
            self.ret(address)
        elif address == 0xBCF376:
            target = self.r(0)
            assert (target == 0x19CA4C8 or CFG + 60 <= target < CFG + CFG_BYTES)
            self.events.append(dict(fixture="mutex_init_noop", address=hex(target),
                                    mutex_bytes_and_behavior_not_executed=True))
            self.ret(0)
        else:
            raise AssertionError("Unexpected original execution: " + hex(address))

    def call(self, address, *arguments, stop_at=()):
        self.stop_at, self.stopped = set(stop_at), None
        self.u.reg_write(reg.UC_ARM_REG_SP, TOP)
        self.u.reg_write(reg.UC_ARM_REG_LR, RETURN | 1)
        for n, arg in enumerate(arguments):
            self.u.reg_write(getattr(reg, "UC_ARM_REG_R" + str(n)), arg)
        self.u.emu_start(address | 1, RETURN, timeout=2_000_000, count=300000)
        if not self.stopped:
            assert self.u.reg_read(reg.UC_ARM_REG_PC) == RETURN
            assert self.u.reg_read(reg.UC_ARM_REG_SP) == TOP
        return self.r(0)


def expected_descriptors(entries):
    cursor, result = 60, {}
    for source in entries:
        sizes = source["sizes"]
        metadata_bytes = 20 + 8 * len(sizes)
        assert metadata_bytes + sum(n & 0x7FFFFFFF for n in sizes) == source["capacity"]
        rings, ring_cursor = [], (cursor + metadata_bytes + 3) & ~3
        for raw_capacity in sizes:
            capacity = raw_capacity & 0x7FFFFFFF
            rings.append(dict(bytes=capacity + 40, offset=ring_cursor, capacity=capacity))
            ring_cursor = (ring_cursor + capacity + 40 + 3) & ~3
        span = source["capacity"] + 40 * len(sizes)
        assert ring_cursor == cursor + span
        result[source["sid"]] = dict(offset=cursor, span=span, area_count=len(sizes),
            tail_area_count=sum(bool(n & 0x80000000) for n in sizes), rings=rings)
        cursor += span
    return result, cursor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cp", type=Path, required=True)
    p.add_argument("--header-evidence", type=Path, help="Optional locally retained hardware evidence")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        p.error("Output already exists")
    raw = args.cp.read_bytes()
    assert sha(raw) == CP_SHA
    if args.header_evidence:
        evidence_bytes = args.header_evidence.read_bytes()
        evidence = json.loads(evidence_bytes)
        assert evidence["result"] == "fixed_ULOG_header_read_and_native_cleanup_passed"
        assert evidence["headers_identical"]
        header = bytes.fromhex(evidence["config_headers"][0]["raw_hex"])
        header_source = dict(kind="prior hardware sample", sha256=sha(evidence_bytes))
    else:
        fixture = bytearray(60)
        fixture[:4] = b"ULOG"
        fixture[16:20] = b"4.0\0"
        struct.pack_into("<I", fixture, 12, 79)
        struct.pack_into("<I", fixture, 56, 0x777FC4)
        header = bytes(fixture)
        header_source = dict(kind="synthetic header", sha256=sha(header))
    assert len(header) == 60 and header[:4] == b"ULOG"
    entries = source_candidates(raw)
    assert struct.unpack_from("<I", header, 12)[0] == len(entries)
    expected, total_used = expected_descriptors(entries)
    assert total_used < struct.unpack_from("<I", header, 56)[0] + 60 <= CFG_BYTES
    source_address, source_bytes = entries[0]["address"], sum(e["length"] for e in entries)
    machine = Machine(raw, header)
    assert machine.call(BUILDER[0], source_address, source_bytes) == 0
    assert bytes(machine.u.mem_read(CFG, 60)) == header
    assert machine.get(TABLE) == HEAP and machine.get(COUNT) == 128
    assert any(e["fixture"] == "allocate_hash_table" for e in machine.events)
    all_nodes = []
    for source in entries:
        sid, model = source["sid"], expected[source["sid"]]
        address = machine.call(LOOKUP[0], sid)
        assert address == CFG + model["offset"]
        node = bytes(machine.u.mem_read(address, 20 + 8 * model["area_count"]))
        magic, stored_sid, span = struct.unpack_from("<III", node)
        assert (magic, stored_sid, span) == (0x53425553, sid, model["span"])
        assert struct.unpack_from("<hh", node, 14) == (model["area_count"], model["tail_area_count"])
        assert struct.unpack_from("<H", node, 18)[0] == 0
        assert node[12:14] == b"\xA5\xA5", "Original builder did not initialize these two bytes"
        for index, ring in enumerate(model["rings"]):
            assert struct.unpack_from("<II", node, 20 + 8 * index) == (ring["bytes"], ring["offset"])
            rb = bytes(machine.u.mem_read(CFG + ring["offset"], 32))
            assert rb[:4] == b"RBUF"
            assert struct.unpack_from("<II", rb, 4) == (ring["offset"] + 40, ring["capacity"])
        all_nodes.append(dict(sid=hex(sid), source_TLV=hex(source["address"]),
            descriptor_CP_address_hypothesis=hex(address), offset=hex(model["offset"]),
            span=hex(span), area_count=model["area_count"], tail_area_count=model["tail_area_count"],
            rings=[{k: hex(v) for k, v in ring.items()} for ring in model["rings"]]))
    assert machine.call(LOOKUP[0], 0xDEAD) == 0
    machine.put(TABLE, 0)
    assert machine.call(LOOKUP[0], 0x67) == 0
    machine.put(TABLE, HEAP)
    machine.put(COUNT, 0)
    assert machine.call(LOOKUP[0], 0x67) == 0
    machine.put(COUNT, 128)

    prefixes = []
    for sid, requested, normalized in ((0x67, 1, 0), (0x2AE, 1, 1),
                                        (0x67, 99, 0), (0x2AE, 99, 0)):
        message = bytearray(24)
        struct.pack_into("<HII", message, 2, sid, requested, 20 if sid == 0x67 else 24)
        machine.u.mem_write(MSG, bytes(message))
        machine.call(PREFIX[0], MSG, stop_at=(PREFIX[1], 0x55C6A6))
        assert machine.stopped == PREFIX[1]
        selected = machine.r(2)
        actual_ring = machine.r(10)
        assert selected == normalized
        assert actual_ring == CFG + expected[sid]["rings"][normalized]["offset"]
        prefixes.append(dict(sid=hex(sid), requested_area=requested, normalized_area=selected,
            selected_ring_CP_address_hypothesis=hex(actual_ring), ring_written=False,
            executed="0x55c2fc..0x55c37c exclusive, plus actual stock 0xbd0942 lookup"))

    warm = Machine(raw, header, warm=True)
    assert warm.call(BUILDER[0], source_address, source_bytes) == 0
    for sid in (0x67, 0x2AE):
        ring = expected[sid]["rings"][0]["offset"]
        assert bytes(warm.u.mem_read(CFG + ring, 32)) == b"\xA5" * 32
        assert warm.call(LOOKUP[0], sid) == CFG + expected[sid]["offset"]
    report = dict(
        schema="d6700/original-CP-log-directory-emulation/1",
        recorded_utc=datetime.now(timezone.utc).isoformat(), result="passed", camera_access=False,
        device_backend_constructed=False, CP_sha256=CP_SHA,
        source_script_sha256=sha(Path(__file__).read_bytes()),
        header_source=header_source,
        original_code_executed=["0x55c6d8..0x55ca46 bounded builder", "0xbd0942..0xbd09b8 lookup",
                                "0x55c2fc..0x55c37c writer selection prefix"],
        named_fixtures=["reset_state", "allocate_hash_table", "zero_fill", "mutex_init_noop"],
        stock_source_TLV_address=hex(source_address), stock_source_TLV_bytes=source_bytes,
        stock_source_TLV_count=len(entries), contiguous_source_TLVs=True,
        stock_input_is_unaligned=bool(source_address & 3),
        actual_runtime_configuration_source_verified=False,
        runtime_directory_measured=False, target_descriptors_measured=False,
        original_builder_nodes_checked=len(all_nodes), original_lookup_entries_checked=len(all_nodes),
        hash_table_capacity_fixture=128, hash_table_bytes_fixture=1024,
        stock_layout_used_bytes=hex(total_used),
        header_reported_capacity=hex(struct.unpack_from("<I", header, 56)[0] + 60),
        target_nodes=[n for n in all_nodes if int(n["sid"], 0) in (0x67, 0x2AE)],
        writer_area_selection_prefixes=prefixes,
        uninitialized_node_bytes_0x0c_0x0d_preserved=True,
        warm_path_does_not_initialize_ring_headers=True,
        native_instructions_in_cold_run=machine.instructions,
        nodes=all_nodes,
        next_bounded_read_plan="Fresh-root-qualified fixed 36-byte descriptors at stock-layout hypotheses, with SUBS/SID/span/area/bounds gates. No pointer-derived reads or ring body in that stage.",
        CP_cache_visibility_verified=False, snapshot_consistency_proven=False,
        log_records_collected=False, photo_events_attributed=False, JPEG_pixels_verified=False,
        limitations=[
            "These are executions of stock instructions in an offline model. Source TLVs, heap, pool, globals, reset-state and mutex initialization are fixtures; no live directory was read.",
            "The modeled 60-byte header is not a reusable live pointer or permission to read its reported capacity.",
            "Matching 79 entries and a sufficient capacity does not prove the current boot uses this stock source, order, descriptor offsets or filter configuration.",
            "Node bytes +0x0c..+0x0d are not initialized by this builder and must not be hardcoded to zero by a future descriptor gate.",
            "A future fixed address hypothesis must be requalified by current roots and each descriptor's own bounds and identity. Failure must refuse any followup read.",
            "The writer selection prefix stops before timestamp adjustment, locking and publication; it proves no live ring event, callback drain, photo ownership or LUT/JPEG effect.",
        ],
    )
    with args.out.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("result", "camera_access", "original_builder_nodes_checked",
        "original_lookup_entries_checked", "stock_layout_used_bytes", "target_nodes",
        "writer_area_selection_prefixes", "target_descriptors_measured")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
