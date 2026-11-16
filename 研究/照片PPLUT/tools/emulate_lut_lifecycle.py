"""Execute original A6700 2.00 LUT selection/loading/transfer code offline.

The CPU executes hash-checked AP instructions. File I/O, OS, C++ runtime and
interchip endpoints are explicit synthetic fixtures, never camera operations.
In particular, a simulated callback is not evidence of CP/ISP/JPEG readiness.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
import numpy as np
from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_READ, UC_PROT_READ,
                     UC_PROT_WRITE, UC_PROT_EXEC)
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_LR, UC_ARM64_REG_PC)

AP_SHA256 = "e65fd595052be5d0e98ce8000dfcb94f90a2f8309de70ef140375052009b0ef3"
STACK, CALLBACK_STACK = 0x70000000, 0x70020000
DATA, HEAP, BUFFER, STAGING = 0x71000000, 0x72000000, 0x73000000, 0x74000000
RETURN, VIRTUAL_NOOP = 0x7f000000, 0x7f000100
REQUEST, OUT, NAME = DATA, DATA + 0x1000, DATA + 0x2000
AP_OBJECT, CP_OBJECT = DATA + 0x3000, DATA + 0x3100
STATE, VTABLE, RESULT = DATA + 0x4000, DATA + 0x4100, DATA + 0x4200
FORMAT_ARRAY = 0x8d22f98 + 0x7f000 + 0x74c
AP_HANDLE, CP_HANDLE = 0x1234, 0x8629
RANGES = (
    (0x3215fc0, 0x3216168), (0x3216168, 0x32161b8),
    (0x3210adc, 0x3210dd8), (0x320fe78, 0x320fe9c),
    (0x2f99b78, 0x2f99b8c), (0x3440aec, 0x3440d74),
    (0x349f724, 0x349f7a4), (0x3498864, 0x34988a4),
    (0x350e08c, 0x350e224), (0x34d2190, 0x34d24b8),
    (0x34d1ec4, 0x34d2004), (0x34d1d24, 0x34d1db0),
    (0x3418d98, 0x3418e24), (0x3419010, 0x341902c),
    (0x349c604, 0x349c644), (0x34f4608, 0x34f46b4),
    (0x34e9630, 0x34e9674),
)
XREF_TARGETS = {
    "resolve_by_pp": 0x3215fc0, "eligibility": 0x3216168,
    "initialize_handles": 0x3210a18, "free_handles": 0x3210adc,
    "read_file": 0x3210cd0, "upload": 0x3210b70,
    "send_lut": 0x3440aec, "send_descriptor": 0x3440c18,
    "sequence_execute": 0x3440d28, "transfer_wrapper": 0x34d2190,
    "completion_callback": 0x34d1ec4, "descriptor_wrapper": 0x349c604,
}


class Firmware:
    def __init__(self, path):
        self.path = path
        self.data = path.read_bytes()
        if hashlib.sha256(self.data).hexdigest() != AP_SHA256:
            raise ValueError("AP SHA-256 mismatch")
        self.elf = ELFFile(io.BytesIO(self.data))
        self.loads = [s for s in self.elf.iter_segments() if s["p_type"] == "PT_LOAD"]

    def bytes_at(self, address, size):
        matches = [s for s in self.loads if s["p_vaddr"] <= address and
                   address + size <= s["p_vaddr"] + s["p_filesz"]]
        if len(matches) != 1:
            raise ValueError(f"Not one file-backed LOAD: {address:#x}+{size:#x}")
        s = matches[0]
        at = address - s["p_vaddr"] + s["p_offset"]
        return self.data[at:at + size]

    def direct_xrefs(self):
        section = self.elf.get_section_by_name(".text")
        words = np.frombuffer(section.data(), dtype="<u4")
        sites = section["sh_addr"] + 4 * np.arange(len(words), dtype=np.int64)
        branches = (words & 0x7c000000) == 0x14000000
        delta = (words & 0x3ffffff).astype(np.int64)
        delta = np.where(delta & 0x2000000, delta - 0x4000000, delta)
        destinations = sites + 4 * delta
        result = {}
        for name, target in XREF_TARGETS.items():
            result[name] = {"target_va": hex(target), "direct_branches": [
                {"site": hex(int(sites[i])), "kind": "bl" if words[i] & 0x80000000 else "b"}
                for i in np.flatnonzero(branches & (destinations == target))]}
        return result


class NativeAP:
    """Narrow real-code runner with named, fail-closed external fixtures."""
    def __init__(self, firmware, **options):
        self.firmware, self.options = firmware, options
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.pages, self.log, self.executed = set(), [], set()
        self.extra_ranges = []  # Only offline ELF LOADs explicitly supplied by a caller.
        self.nodes, self.heap_next = {}, HEAP
        self.pending = self.dispatch = None
        self.blocked = False
        self.callback_count = self.wait_count = self.instructions = 0
        self.mapping_live = False
        self.descriptors = []
        self.slot = None
        self.payload = bytes((i * 37 + 11) & 255 for i in range(options.get("payload_bytes", 0xbc00)))
        for start, size in ((STACK, 0x10000), (CALLBACK_STACK, 0x10000),
                            (DATA, 0x10000), (HEAP, 0x20000),
                            (BUFFER, 0x20000), (STAGING, 0x20000), (RETURN, 0x1000)):
            self.map_fixture(start, size)
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        self.uc.hook_add(UC_HOOK_CODE, self._code)
        self.uc.hook_add(UC_HOOK_MEM_READ, self._memory_read)
        self.put(0x4e8b048, "Q", AP_OBJECT)
        self.put(0x4f195e8, "Q", CP_OBJECT)
        self.put(AP_OBJECT, "IIQI", 1578, options.get("ap_handle", AP_HANDLE), 0x100000, options.get("capacity", 0xc000))
        self.put(CP_OBJECT, "IIQI", 1578, options.get("cp_handle", CP_HANDLE), 0x200000, 0xc000)
        self.put(0x4ec3820, "Q", BUFFER)
        self.put(0x4ea4370, "Q", BUFFER + 0x10000)
        self.write(FORMAT_ARRAY, bytes([options.get("format", 1)] * 16))
        self.put(0x4eea5b0, "Q", 1)
        self.put(0x14164820 + 8, "Q", 1)
        self.put(STATE, "QQII", VTABLE, RESULT, 0, 0)
        self.put(VTABLE + 0x10, "Q", VIRTUAL_NOOP)
        self.write(NAME, b"SYNTHETIC.cube\0" + bytes(49))
        self.externals = self._external_fixtures()

    def map_fixture(self, start, size):
        self.uc.mem_map(start, size, UC_PROT_READ | UC_PROT_WRITE | UC_PROT_EXEC)
        self.pages.update(range(start, start + size, 0x1000))

    def ensure(self, address, size):
        for page in range(address & ~0xfff, (address + size + 0xfff) & ~0xfff, 0x1000):
            if page in self.pages:
                continue
            containing = [s for s in self.firmware.loads if
                          s["p_vaddr"] < page + 0x1000 and page < s["p_vaddr"] + s["p_memsz"]]
            if not containing:
                raise RuntimeError(f"Unmapped fixture or firmware address: {page:#x}")
            self.uc.mem_map(page, 0x1000)
            self.pages.add(page)
            for s in containing:
                a, b = max(page, s["p_vaddr"]), min(page + 0x1000, s["p_vaddr"] + s["p_filesz"])
                if a < b:
                    at = a - s["p_vaddr"] + s["p_offset"]
                    self.uc.mem_write(a, self.firmware.data[at:at + b - a])

    def _unmapped(self, _uc, _access, address, size, _value, _user):
        self.ensure(address, size)
        return True

    def read(self, address, size):
        self.ensure(address, size)
        return bytes(self.uc.mem_read(address, size))

    def write(self, address, value):
        self.ensure(address, len(value))
        self.uc.mem_write(address, value)

    def get(self, address, fmt="I"):
        values = struct.unpack("<" + fmt, self.read(address, struct.calcsize("<" + fmt)))
        return values[0] if len(values) == 1 else values

    def put(self, address, fmt, *values):
        self.write(address, struct.pack("<" + fmt, *values))

    def x(self, n):
        return self.uc.reg_read(UC_ARM64_REG_X0 + n)

    def ret(self, value=0):
        self.uc.reg_write(UC_ARM64_REG_X0, value & 0xffffffffffffffff)
        self.uc.reg_write(UC_ARM64_REG_PC, self.uc.reg_read(UC_ARM64_REG_LR))

    def cstring(self, address, bound=256):
        value = self.read(address, bound)
        if b"\0" not in value:
            raise RuntimeError("Unterminated fixture string")
        return value.split(b"\0", 1)[0].decode("ascii")

    def _memory_read(self, _uc, _access, address, size, _value, _user):
        if STAGING <= address < STAGING + 0x20000 and not self.mapping_live:
            raise RuntimeError("Native code used the synthetic mapping after munmap")

    def _code(self, _uc, address, _size, _user):
        self.instructions += 1
        if self.instructions > 150000:
            raise RuntimeError("Aggregate CPU instruction budget exceeded")
        if address in self.externals:
            self.executed.add(address)
            self.externals[address]()
        elif any(a <= address < b for a, b in (*RANGES, *self.extra_ranges)):
            self.executed.add(address)
            if address == 0x34f46a8:
                self.log.append({"event": "native_descriptor_wrapper_status",
                                 "value": self.get(self.x(20))})
        else:
            raise RuntimeError(f"Unexpected execution: {address:#x}")

    def invoke(self, address, args=(), stack_arguments=(), callback=False):
        stack_top = (CALLBACK_STACK if callback else STACK) + 0xf000
        self.ensure(address, 4)
        for n, value in enumerate(args):
            self.uc.reg_write(UC_ARM64_REG_X0 + n, value & 0xffffffffffffffff)
        self.uc.reg_write(UC_ARM64_REG_SP, stack_top)
        self.uc.reg_write(UC_ARM64_REG_LR, RETURN)
        for n, value in enumerate(stack_arguments):
            self.put(stack_top + n * 8, "Q", value)
        pc = address
        while True:
            self.uc.emu_start(pc, RETURN, timeout=500000, count=100000)
            if self.dispatch:
                kind = self.dispatch
                self.dispatch = None
                context = self.uc.context_save()
                request = self.pending
                self.pending = None
                self.callback_count += 1
                self.log.append({"event": "synthetic_callback_dispatch", "timing": kind})
                self.invoke(request["callback"], (self.options.get("callback_arg0", 0), request["cookie"]), callback=True)
                self.uc.context_restore(context)
                if kind == "at_futex_wait":
                    self.ret(1)
                pc = self.uc.reg_read(UC_ARM64_REG_PC)
                continue
            if self.blocked:
                return None
            if self.uc.reg_read(UC_ARM64_REG_PC) != RETURN:
                raise RuntimeError("CPU did not return within its time/instruction budget")
            return self.x(0) & 0xffffffff

    def _external_fixtures(self):
        def noop():
            self.ret()

        def memcpy():
            destination, source, size = self.x(0), self.x(1), self.x(2)
            if size > 0x20000:
                raise RuntimeError("Unbounded memcpy")
            self.write(destination, self.read(source, size))
            self.ret(destination)

        def memset():
            destination, value, size = self.x(0), self.x(1) & 255, self.x(2)
            if size > 0x20000:
                raise RuntimeError("Unbounded memset")
            self.write(destination, bytes([value]) * size)
            self.ret(destination)

        def snprintf():
            destination, capacity = self.x(0), self.x(1)
            value = (self.cstring(self.x(2)) % (self.cstring(self.x(3)), self.x(4))).encode("ascii")
            self.write(destination, value[:capacity - 1] + b"\0")
            self.ret(len(value))

        def read_file():
            path, buffer, capacity, result = self.x(0), self.x(1), self.x(2), self.x(3)
            self.log.append({"event": "synthetic_file_read", "path": self.cstring(path), "capacity": capacity,
                             "buffer": hex(buffer), "slot": self.slot})
            if self.options.get("read_fail"):
                self.ret(1)
                return
            # An oversized reported length is a deliberate bad-reader fixture;
            # it exercises the native upload bound, not a real filesystem.
            self.write(buffer, self.payload[:capacity])
            self.put(result, "Q", len(self.payload))
            self.ret()

        def open_mapping():
            self.log.append({"event": "synthetic_open_mapping", "path": self.cstring(self.x(0))})
            self.ret(-1 if self.options.get("open_fail") else 17)

        def mmap():
            if self.x(0) != 0 or self.x(2) != 3 or self.x(3) != 1 or self.x(4) != 17:
                raise RuntimeError("Unexpected native mmap arguments")
            self.mapping_live = True
            self.log.append({"event": "synthetic_mmap", "bytes": self.x(1), "offset": self.x(5)})
            self.ret(STAGING)

        def munmap():
            if self.x(0) != STAGING:
                raise RuntimeError("Unexpected native munmap address")
            self.log.append({"event": "synthetic_munmap", "bytes": self.x(1)})
            failed = self.options.get("unmap_fail", False)
            self.mapping_live = failed
            self.ret(-1 if failed else 0)

        def close():
            if self.x(0) != 17:
                raise RuntimeError("Unexpected native close fd")
            self.log.append({"event": "synthetic_close"})
            self.ret(-1 if self.options.get("close_fail") else 0)

        def new():
            address, size = self.heap_next, self.x(0)
            self.heap_next += (size + 15) & ~15
            if self.heap_next > HEAP + 0x20000:
                raise RuntimeError("Synthetic allocation budget exceeded")
            self.ret(address)

        def promise_create():
            self.put(self.x(0), "QQQ", STATE, DATA + 0x4300, 0)
            self.put(STATE + 0x10, "II", 0, 0)
            self.log.append({"event": "synthetic_promise_create"})
            self.ret()

        def find_bucket():
            # std::unordered_map internals are outside the tested code scope.
            self.ret(next(iter(self.nodes.values())) if self.nodes else 0)

        def insert():
            key, node = self.x(2), self.x(3)
            if key in self.nodes or self.get(node + 8) != key:
                raise RuntimeError("Unexpected synthetic correlation key")
            self.nodes[key] = node
            self.ret(node)

        def lookup():
            self.ret(self.nodes.get(self.get(self.x(1)), 0))

        def erase():
            key = self.get(self.x(1) + 8)
            if self.nodes.pop(key, None) is None:
                raise RuntimeError("Erased missing synthetic correlation key")
            self.ret()

        def fulfill():
            if self.get(self.x(0), "Q") != STATE:
                raise RuntimeError("Native callback fulfilled the wrong synthetic promise")
            self.put(STATE + 0x10, "I", 1)
            self.log.append({"event": "synthetic_promise_fulfill"})
            self.ret()

        def transfer():
            request = self.x(0)
            source, destination, size, flag, callback, cookie = self.get(request, "QQIIQQ")
            source_handle, source_offset = self.get(source, "II")
            destination_handle, destination_offset = self.get(destination, "II")
            if (source_handle, source_offset, destination_handle, destination_offset, flag, callback) != (
                    self.get(AP_OBJECT + 4), 0, self.get(CP_OBJECT + 4), 0, 1, 0x34d1ec4):
                raise RuntimeError("Unexpected interchip descriptor")
            if self.mapping_live or self.read(STAGING, size) != self.payload:
                raise RuntimeError("Interchip submission before unmap or with incorrect copied payload")
            value = {"source_handle": source_handle, "destination_handle": destination_handle,
                     "bytes": size, "callback": callback, "cookie": cookie}
            self.log.append({"event": "synthetic_interchip_submit", **value})
            rc = self.options.get("async_return", 0)
            self.ret(rc)
            if rc == 0:
                self.pending = value
                if self.options.get("callback_timing") == "before_wait":
                    self.dispatch = "before_wait"
                    self.uc.emu_stop()

        def wait():
            self.wait_count += 1
            self.log.append({"event": "synthetic_futex_wait", "ready": self.get(STATE + 0x10) & 0x7fffffff})
            if self.x(0) != STATE + 0x10 or self.x(3) != 0:
                raise RuntimeError("Unexpected future wait arguments")
            if self.options.get("withhold_callback"):
                self.blocked = True
                self.uc.emu_stop()
            elif self.pending:
                self.dispatch = "at_futex_wait"
                self.uc.emu_stop()
            else:
                raise RuntimeError("Native code waited without a pending synthetic completion")

        def exception_equal():
            self.ret(self.get(self.x(0), "Q") == self.get(self.x(1), "Q"))

        def sender():
            raw = self.read(self.x(1), self.x(2))
            if len(raw) != 0x60 or self.get(self.x(1) + 2, "H") != 0x4155:
                raise RuntimeError("Unexpected imaging message")
            self.descriptors.append({"raw_hex": raw.hex(), "fields": list(struct.unpack_from("<6I", raw, 8)),
                                     "name": raw[0x20:0x5f].split(b"\0", 1)[0].decode("ascii"),
                                     "synthetic_sender_return": self.options.get("sender_return", 0)})
            self.log.append({"event": "synthetic_imaging_sender", "message_id": "0x4155"})
            self.ret(self.options.get("sender_return", 0))

        def metadata():
            self.log.append({"event": "synthetic_mpr_metadata_endpoint"})
            self.put(self.x(1), "I", 0)
            self.ret()

        def free_handle():
            handle = self.get(self.x(0))
            self.log.append({"event": "synthetic_free_handle", "handle": handle})
            self.put(self.x(1), "I", 2 if self.options.get("free_fail_handle") == handle else 0)
            self.ret()

        def fatal():
            raise RuntimeError("Native assertion/fatal path reached")

        return {
            0x176a1c0: memcpy, 0x176a070: memset, 0x176ab80: snprintf,
            0x3137990: noop, 0x313759c: noop, 0x36156ac: noop,
            0x3618384: noop, 0x3618444: noop, 0x361766c: noop,
            0x320f524: read_file, 0x176cb60: lambda: self.ret(4096),
            0x176a1a0: open_mapping, 0x176b730: mmap, 0x176aad0: munmap,
            0x176bfb0: close, 0x176bbe0: lambda: self.ret(DATA + 0x4400),
            0x176c5f0: new, 0x176a2b0: noop,
            0x328b098: promise_create, 0x328b588: noop, 0x328c9e0: noop,
            0x1907dec: noop, 0x176ca00: noop,
            0x34d1c60: find_bucket, 0x34d2004: insert,
            0x34d1db0: lookup, 0x34d1de8: erase, 0x34ca584: fulfill,
            0x176a090: transfer, 0x176ca70: wait,
            0x176a210: exception_equal, 0x176a6a0: noop, VIRTUAL_NOOP: noop,
            0x34f2360: sender, 0x3494684: metadata, 0x349f624: free_handle,
            0x356ecd4: fatal, 0x36199e0: fatal, 0x176c120: fatal,
        }

    def sequence(self, selection=17, exists=1):
        self.slot = selection - 3
        self.put(REQUEST + 0x1bc, "II", 42, selection)
        self.write(REQUEST + 0x1c4, self.read(NAME, 64))
        self.put(REQUEST + 0x204, "I", exists)
        result = self.invoke(0x3440d28, (REQUEST,))
        return {"returned": result is not None, "blocked_at_wait": self.blocked,
                "native_callback_invocations": self.callback_count,
                "futex_wait_calls": self.wait_count, "events": self.log,
                "descriptors": self.descriptors,
                "instructions": self.instructions,
                "external_fixture_addresses_used": [hex(i) for i in sorted(self.executed & self.externals.keys())]}


def verify(firmware):
    cases = []
    for pp, expected in ((0, 0), (1, 0), (11, 0), (12, 17), (13, 18), (14, 3), (15, 4)):
        runner = NativeAP(firmware)
        result = runner.invoke(0x3215fc0, (0, pp, 17, 18, 3, 4, 1, 1))
        if result != expected:
            raise AssertionError((pp, result, expected))
        cases.append({"name": f"original_pp_resolver_{pp}", "pp": pp, "selected": result})
    for pp, expected in ((0, 0), (1, 0), (12, 1), (15, 1)):
        runner = NativeAP(firmware)
        # Pipeline enum 3 is synthetic. It is not general_shooting_mode.
        result = runner.invoke(0x3216168, (3, 0, pp, 1, 1))
        if result != expected:
            raise AssertionError((pp, result, expected))
        cases.append({"name": f"original_eligibility_pipeline3_pp{pp}", "result": result})

    specifications = (
        ("slot17_deferred_callback", {}, True, 1, 14),
        ("slot18_callback_before_wait", {"selection": 18, "callback_timing": "before_wait"}, True, 1, 15),
        ("plain_binary_slot3", {"selection": 3, "format": 0, "payload_bytes": 0x400}, True, 1, 0),
        ("empty_slot_no_file_io", {"exists": 0}, True, 0, None),
        ("read_failure", {"read_fail": True}, True, 0, 14),
        ("source_handle_absent", {"ap_handle": 0}, True, 0, 14),
        ("destination_handle_absent", {"cp_handle": 0}, True, 0, 14),
        ("zero_bytes", {"payload_bytes": 0}, True, 0, 14),
        ("capacity_exceeded", {"payload_bytes": 0xc001}, True, 0, 14),
        ("mapping_open_failure", {"open_fail": True}, True, 0, 14),
        ("unmap_failure", {"unmap_fail": True}, True, 0, 14),
        ("close_failure", {"close_fail": True}, True, 0, 14),
        ("async_submission_failure_1", {"async_return": 1}, True, 0, 14),
        ("async_submission_failure_8", {"async_return": 8}, True, 0, 14),
        ("callback_absent_still_waiting", {"withhold_callback": True}, False, 0, 14),
        ("callback_arg0_not_interpreted", {"callback_arg0": 0xdeadbeef}, True, 1, 14),
        ("descriptor_sender_failure_not_propagated", {"sender_return": 7}, True, 1, 14),
    )
    for name, options, returned, callbacks, file_slot in specifications:
        options = dict(options)
        selection, exists = options.pop("selection", 17), options.pop("exists", 1)
        runner = NativeAP(firmware, **options)
        result = runner.sequence(selection, exists)
        if result["returned"] != returned or result["native_callback_invocations"] != callbacks:
            raise AssertionError((name, result))
        reads = [e for e in result["events"] if e["event"] == "synthetic_file_read"]
        if (file_slot is None and reads) or (file_slot is not None and (
                len(reads) != 1 or not reads[0]["path"].endswith(f"LUT{file_slot + 1:03d}.LUT"))):
            raise AssertionError((name, reads))
        if not returned:
            if result["descriptors"] or result["futex_wait_calls"] != 1:
                raise AssertionError((name, result))
        else:
            status = [e["value"] for e in result["events"] if e["event"] == "native_descriptor_wrapper_status"]
            if len(result["descriptors"]) != 1 or status != [0]:
                raise AssertionError((name, result))
            descriptor = result["descriptors"][0]
            ok_data = callbacks == 1
            expected_handle = CP_HANDLE if ok_data else 0xffffffff
            if descriptor["fields"][0:2] != [1578, expected_handle]:
                raise AssertionError((name, descriptor))
        cases.append({"name": name, "fixture_options": options, "selection": selection,
                      "slot_exists": exists, **result})

    runner = NativeAP(firmware)
    runner.slot = 14
    if runner.invoke(0x3210cd0, (14, OUT)) != 1:
        raise AssertionError("First shared-buffer read failed")
    pointer_a = runner.get(OUT + 8, "Q")
    runner.payload = bytes([0x5a]) * 0xbc00
    runner.slot = 15
    if runner.invoke(0x3210cd0, (15, OUT)) != 1:
        raise AssertionError("Second shared-buffer read failed")
    pointer_b = runner.get(OUT + 8, "Q")
    if pointer_a != pointer_b or runner.read(pointer_a, len(runner.payload)) != runner.payload:
        raise AssertionError("Unexpected shared-buffer behavior")
    cases.append({"name": "successive_reads_reuse_format_buffer", "same_pointer": True,
                  "second_payload_visible_at_first_pointer": True, "events": runner.log})
    runner = NativeAP(firmware)
    runner.write(OUT, b"\xa5" * 16)
    if runner.invoke(0x3210cd0, (16, OUT)) != 0 or runner.get(OUT) != 0 or runner.get(OUT + 8, "Q") != 0 or runner.log:
        raise AssertionError("Invalid file index did not clear and reject")
    cases.append({"name": "invalid_file_index_rejected_before_io", "file_index": 16})

    for failed_handle in (None, AP_HANDLE, CP_HANDLE):
        runner = NativeAP(firmware, free_fail_handle=failed_handle)
        returned = runner.invoke(0x3210adc)
        actual = [runner.get(AP_OBJECT + 4), runner.get(CP_OBJECT + 4)]
        expected = [0, 0] if failed_handle is None else [AP_HANDLE, CP_HANDLE] if failed_handle == AP_HANDLE else [0, CP_HANDLE]
        if actual != expected or returned != (1 if failed_handle is None else 0):
            raise AssertionError((failed_handle, actual, returned))
        cases.append({"name": f"free_handles_failure_{failed_handle}", "return": returned,
                      "handles_after": actual, "events": runner.log})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ap", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output exists")
    firmware = Firmware(args.ap)
    cases = verify(firmware)
    report = {"schema": "a6700-lab/native-lut-lifecycle-emulation/1", "ap_sha256": AP_SHA256,
              "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "result": "passed", "case_count": len(cases), "cases": cases,
              "direct_xrefs": firmware.direct_xrefs(),
              "executed_native_ranges": [{"start": hex(a), "end_exclusive": hex(b),
                  "sha256": hashlib.sha256(firmware.bytes_at(a, b - a)).hexdigest()} for a, b in RANGES],
              "method": "Original AP instructions in bounded Unicorn CPU execution; named synthetic external fixtures",
              "limitations": [
                  "Pipeline enum inputs, file bytes, handles, OS and C++ runtime state are synthetic",
                  "C++ allocation/container/promise fulfillment and OS waits are explicit substituted endpoints",
                  "The original callback body and original future wait loop execute; scheduling is synthetic",
                  "Callback arrival does not establish successful CP application, current ownership or JPEG routing",
                  "A missing callback is deliberately stopped at the wait; no real elapsed-time timeout is inferred",
                  "Direct B/BL references omit virtual/indirect calls; no camera connection, firmware modification or installer",
              ]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Passed {len(cases)} native lifecycle cases. Synthetic I/O; CP/ISP/JPEG unverified.")


if __name__ == "__main__":
    main()
