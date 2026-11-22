"""Execute the original CP VEF look command, wrappers and user-MLUT APIs offline.

Virtual objects, resource ownership, physical-address maps, hardware operations
and the queue are named fixtures. No camera, image pixels or JPEG are involved.
The queue-draining branch of the precheck and the real scheduler do not execute.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_LITTLE_ENDIAN,
                     UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_PROT_READ,
                     UC_PROT_WRITE, UC_PROT_EXEC)
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
from emulate_lut_parameter_helpers import CP_BASE, CP_SHA256, check_hash

CHECK, VALIDATE, EXECUTE, CALLBACK = 0x6e6720, 0x6e683e, 0x6e6896, 0x6e01b4
USER, ART, PACK = 0x542108, 0x542344, 0xb5d3b8
RANGES = ((CHECK, 0x6e6832), (VALIDATE, 0x6e6896), (EXECUTE, 0x6e6a38),
          (0x6e1566, 0x6e16b6), (0x6e0188, 0x6e01f4),
          (USER, 0x54232c), (ART, 0x542604), (PACK, 0xb5d474))
STACK, TOP, DATA, RETURN, FIXTURES = 0x8000000, 0x800f000, 0x9000000, 0xa000000, 0xa100000
PARAM, COMMAND, PATH, EVENT, CONTROL, FLAGS = (DATA + n * 0x100 for n in range(6))
ACTIVE, VFX_STATE, VFX_LOOK, VFX_GEN, VFX_WAIT = 0x19ca108, 0x19b69f8, 0x19b69fc, 0x19b6a0c, 0x1a67a1b
MAP_TABLE = 0xcd0ee0
PHYSICAL = (0x112340000, 0x234560000, 0x356780000, 0x4789a0000)
VIRTUAL = (0x21000000, 0x22000000, 0x23000000, 0x24000000)
MAP_BYTES = 0x100000
ALLOWED = {
    0: [0, 1, 2, 6, 11, 15, 16, 17, 18, 19, 20, 21, 22, 24, 25, 27, 28],
    1: [9, 10, 12, 13, 14, 26],
    3: [3, 4, 5, 7, 8, 23],
}


class NativeDiagnostic(RuntimeError):
    """Stop at a firmware diagnostic; do not fake successful continuation."""


class VEF:
    def __init__(self, firmware, **options):
        self.options, self.events, self.fields = options, [], []
        self.pending = None
        self.instructions, self.visited, self.fixture_hits = 0, set(), set()
        self.endpoints = {}
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_LITTLE_ENDIAN)
        size = (len(firmware) + 4095) & ~4095
        self.uc.mem_map(CP_BASE, size)
        self.uc.mem_write(CP_BASE, firmware)
        self.uc.mem_protect(CP_BASE, size, UC_PROT_READ | UC_PROT_EXEC)
        for address, size in ((STACK, 0x10000), (DATA, 0x3000),
                              (0x19ca000, 0x1000), (0x19b6000, 0x1000), (0x1a67000, 0x1000)):
            self.uc.mem_map(address, size, UC_PROT_READ | UC_PROT_WRITE)
        for address in (RETURN, FIXTURES):
            self.uc.mem_map(address, 0x1000)
            self.uc.mem_write(address, b"\x70\x47" * 2048)
            self.uc.mem_protect(address, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        # Only this data page replaces firmware bytes. No executable page is writable.
        self.uc.mem_protect(MAP_TABLE & ~4095, 4096, UC_PROT_READ | UC_PROT_WRITE)
        for index, (physical, virtual) in enumerate(zip(PHYSICAL, VIRTUAL)):
            at = MAP_TABLE + 0x20 * index
            self.uc.mem_write(at, bytes(0x20))
            self.put(at, physical, "Q")
            self.put(at + 0x10, virtual)
            self.put(at + 0x18, MAP_BYTES, "Q")
        self.object(COMMAND, {8: "command.parameter", 0x34: "command.phase.get",
                             0x3c: "command.phase.set", 0x58: "command.event.request",
                             0x60: "command.field.write"})
        self.object(PATH, {0xc8: "path.handle", 0x1f8: "path.phase.get",
                          0x1fc: "path.phase.set", 0x200: "path.output_flags",
                          0x208: "path.parameter.get", 0x20c: "path.parameter.set"})
        self.object(EVENT, {0: "event.publish"})
        self.object(CONTROL, {0xc: "controller.phase", 0x50: "controller.restore.query",
                             0x54: "controller.restore.request"})
        self.put(COMMAND + 4, options.get("command_phase", 0))
        self.put(PATH + 4, options.get("path_phase", 0))
        self.put(PATH + 8, PARAM)
        self.uc.mem_write(FLAGS, struct.pack("<3I", *options.get("outputs", (0, 0, 0))))
        self.put(ACTIVE, options.get("active_handle", 42))
        for address, value in ((VFX_STATE, 2), (VFX_LOOK, 11), (VFX_GEN, 7), (VFX_WAIT, 1)):
            self.put(address, value, "B" if address == VFX_WAIT else "I")
        self.vfx_before = self.vfx_state()
        self.parameters(options.get("data_type", 1), options.get("ordinal", 10))
        self.uc.hook_add(UC_HOOK_CODE, self.guard)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write_guard)

    def put(self, address, value, fmt="I"):
        self.uc.mem_write(address, struct.pack("<" + fmt, value))

    def get(self, address, fmt="I"):
        return struct.unpack("<" + fmt, bytes(self.uc.mem_read(address, struct.calcsize("<" + fmt))))[0]

    def r(self, n):
        return self.uc.reg_read(UC_ARM_REG_R0 + n)

    def ret(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value & 0xffffffff)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def object(self, address, slots):
        table = DATA + 0x1000 + ((address - COMMAND) // 0x100) * 0x400
        self.put(address, table)
        for offset, name in slots.items():
            target = FIXTURES + 16 * len(self.endpoints)
            self.endpoints[target] = name
            self.put(table + offset, target | 1)

    def parameters(self, data_type, ordinal):
        self.uc.mem_write(PARAM, bytes(0x28))
        self.put(PARAM, data_type, "B")
        self.put(PARAM + 1, ordinal, "B")
        # The native command layout is bytes FIRST, followed by physical uint64.
        # Its wrappers reorder these into driver {uint64 physical, uint32 bytes}.
        self.put(PARAM + 4, self.options.get("lut_bytes", 48000))
        self.put(PARAM + 8, self.options.get("lut_physical", PHYSICAL[0] + 0x100), "Q")
        self.put(PARAM + 0x14, self.options.get("register_bytes", 124))
        self.put(PARAM + 0x18, self.options.get("register_physical", PHYSICAL[1] + 0x200), "Q")

    def descriptor(self, address):
        physical, size = struct.unpack("<QI", bytes(self.uc.mem_read(address, 12)))
        return {"physical": hex(physical), "bytes": size}

    def write_guard(self, _uc, _access, address, size, _value, _user):
        if STACK <= address and address + size <= STACK + 0x10000:
            return
        if address == ACTIVE and size == 4:
            return
        raise RuntimeError(f"Unexpected native write at {address:#x}, bytes={size}")

    def virtual(self, name):
        expected_this = (COMMAND if name.startswith("command.") else
                         PATH if name.startswith("path.") else
                         EVENT if name.startswith("event.") else CONTROL)
        assert self.r(0) == expected_this, (name, hex(self.r(0)))
        event = {"fixture": name}
        value = 0
        if name == "command.parameter":
            value = PARAM
        elif name == "command.phase.get":
            value = self.get(COMMAND + 4)
        elif name == "command.phase.set":
            value = self.r(1)
            self.put(COMMAND + 4, value)
            event["phase"] = value
        elif name == "command.event.request":
            event["event_id"] = self.r(1)
        elif name == "command.field.write":
            assert self.r(1) == 0x1011 and self.r(3) == 4
            event.update(field_id="0x1011", value=self.get(self.r(2)))
            self.fields.append(event["value"])
        elif name == "path.handle":
            assert self.r(1) == 0
            value = self.options.get("handle", 42)
        elif name == "path.phase.get":
            value = self.get(PATH + 4)
        elif name == "path.phase.set":
            value = self.r(1)
            self.put(PATH + 4, value)
            event["phase"] = value
        elif name == "path.output_flags":
            value = FLAGS
        elif name == "path.parameter.get":
            value = self.get(PATH + 8)
        elif name == "path.parameter.set":
            assert self.r(1) == PARAM
            self.put(PATH + 8, self.r(1))
        elif name == "controller.phase":
            # The native queue-draining branch (phase 15) is out of scope.
            value = 0
        elif name == "controller.restore.query":
            assert (self.r(1), self.r(2), self.r(3)) == (PATH, 0, 6)
            value = self.options.get("restore_query", 1)
            event["return"] = value
        elif name == "controller.restore.request":
            assert self.r(1) == PATH
        elif name == "event.publish":
            assert self.r(1) == 11 and self.r(3) == 8
            event.update(event_id=11, payload=list(struct.unpack("<2I", bytes(self.uc.mem_read(self.r(2), 8)))))
        else:
            raise RuntimeError(name)
        self.events.append(event)
        self.ret(value)

    def driver_entry(self, address):
        user = address == USER
        callback = self.r(2 if user else 3)
        handle_pointer = self.r(3) if user else self.get(self.uc.reg_read(UC_ARM_REG_SP))
        event = {"native": "User3D" if user else "User3DArtGen",
                 "lut_descriptor": self.descriptor(self.r(0)),
                 "internal_look": self.r(1 if user else 2),
                 "callback": hex(callback), "handle_value": self.get(handle_pointer)}
        assert callback == CALLBACK | 1
        expected = {"physical": hex(self.get(PARAM + 8, "Q")), "bytes": self.get(PARAM + 4)}
        assert event["lut_descriptor"] == expected, (event, expected)
        assert event["internal_look"] == self.get(PARAM + 1, "B") + 1
        if not user:
            event["register_descriptor"] = self.descriptor(self.r(1))
            assert event["register_descriptor"] == {
                "physical": hex(self.get(PARAM + 0x18, "Q")), "bytes": self.get(PARAM + 0x14)}
        self.events.append(event)

    def guard(self, _uc, address, _size, _user):
        self.instructions += 1
        if self.instructions > 500000:
            raise RuntimeError("Aggregate CP instruction budget exceeded")
        if address in self.endpoints:
            name = self.endpoints[address]
            self.fixture_hits.add(name)
            self.virtual(name)
            return
        if any(start <= address < end for start, end in RANGES):
            self.visited.add(address)
            if address in (USER, ART):
                self.driver_entry(address)
            return
        name = {
            0x6e7578: "logger.vef", 0x6e75b0: "logger.vef_error",
            0xb944e6: "logger.driver", 0xb6119a: "logger.message",
            0x612934: "memory.zero", 0x541734: "resource.handle_to_mask",
            0xb96862: "mlut.pre", 0xb96b04: "mlut.builtin_look",
            0xb96a6e: "hardware.prepare", 0xb97f72: "resource.mask_to_index",
            0xb5d820: "hardware.lut_payload", 0xb5caa2: "hardware.select_look",
            0xb5d8f6: "hardware.register_payload", 0xb96c44: "hardware.kick",
            0xb5deec: "hardware.operating_mode", 0xb670c4: "queue.submit",
            0x6e8dc4: "event.object", 0x6e9ad6: "controller.object",
            0x1531a0: "diagnostic.physical_map_miss",
        }.get(address)
        if name is None:
            raise RuntimeError(f"Unexpected CP execution at {address:#x}")
        self.fixture_hits.add(name)
        event, value = {"fixture": name}, 0
        if name.startswith("logger."):
            event["arguments"] = [self.r(i) for i in range(4)]
        elif name == "memory.zero":
            at, size = self.r(0), self.r(1)
            assert STACK <= at < at + size <= STACK + 0x10000 and size in (0x18, 0x38)
            self.uc.mem_write(at, bytes(size))
            value = at
        elif name == "resource.handle_to_mask":
            query = self.r(0)
            assert query in (0, 1) and self.r(1) == self.options.get("handle", 42)
            self.put(self.r(2), 8)
            value = self.options.get("lookup_returns", (0, 0))[query]
            event.update(query=query, handle=self.r(1), mask=8, return_value=value)
        elif name == "mlut.pre":
            event["handle_value"] = self.get(self.r(0))
            value = self.options.get("pre_return", 0)
            event["return_value"] = value
        elif name == "mlut.builtin_look":
            assert self.r(1) == CALLBACK | 1
            event.update(internal_look=self.r(0), callback=hex(self.r(1)), handle_value=self.get(self.r(2)))
            value = self.options.get("builtin_return", 0)
            if value == 0:
                assert self.pending is None
                self.pending = CALLBACK | 1
        elif name == "hardware.prepare":
            assert self.r(0) == 8
            value = self.options.get("hardware_return", 0)
        elif name == "resource.mask_to_index":
            assert self.r(0) == 8
            self.put(self.r(1), self.options.get("resource_index", 2))
            value = self.options.get("index_return", 1)
        elif name in ("hardware.lut_payload", "hardware.register_payload"):
            assert self.r(0) == self.options.get("resource_index", 2) and self.r(2) == 0
            virtual, size = struct.unpack("<2I", bytes(self.uc.mem_read(self.r(1), 8)))
            event.update(virtual=hex(virtual), bytes=size)
            value = self.options.get("lut_return" if name == "hardware.lut_payload" else "register_return", 0)
        elif name == "hardware.select_look":
            event.update(resource_index=self.r(0), internal_look=self.r(1))
            value = self.options.get("look_return", 0)
        elif name == "hardware.kick":
            assert self.r(0) == 8
            value = self.options.get("kick_return", 0)
        elif name == "hardware.operating_mode":
            value = 1
        elif name == "queue.submit":
            words = list(struct.unpack("<14I", bytes(self.uc.mem_read(self.r(0), 56))))
            assert words[:7] == [2, 30, self.options.get("resource_index", 2), CALLBACK | 1, 0x2ae085a, 0, 0]
            assert all(v in (0, 1) for v in words[7:13]) and words[13] == 0
            event["words"] = words
            value = self.options.get("queue_return", 0)
            if value == 0:
                assert self.pending is None
                self.pending = words[3]
        elif name == "event.object":
            value = EVENT
        elif name == "controller.object":
            value = CONTROL
        elif name == "diagnostic.physical_map_miss":
            self.events.append(event)
            raise NativeDiagnostic("Native physical address lookup reached its diagnostic endpoint")
        else:
            raise RuntimeError(name)
        event.setdefault("return_value", value)
        self.events.append(event)
        self.ret(value)

    def execute(self, address, args):
        before = bytes(self.uc.mem_read(PARAM, 0x28))
        saved = {n: 0x55000000 + n for n in range(4, 12)}
        for n, value in saved.items():
            self.uc.reg_write(UC_ARM_REG_R0 + n, value)
        for n, value in enumerate(args):
            self.uc.reg_write(UC_ARM_REG_R0 + n, value)
        self.uc.reg_write(UC_ARM_REG_SP, TOP)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        self.uc.emu_start(address | 1, RETURN, timeout=300000, count=5000)
        assert self.uc.reg_read(UC_ARM_REG_PC) == RETURN, "Native function did not reach bounded return"
        assert self.uc.reg_read(UC_ARM_REG_SP) == TOP, "Native stack not restored"
        assert all(self.r(n) == value for n, value in saved.items()), "Callee-saved register changed"
        assert bytes(self.uc.mem_read(PARAM, 0x28)) == before, "Native command changed parameter input"
        assert self.vfx_state() == self.vfx_before, "VEF unexpectedly changed guarded VFX state"
        return self.r(0)

    def validate(self):
        return self.execute(VALIDATE, (0, PARAM))

    def check(self):
        return self.execute(CHECK, (0, COMMAND, PATH))

    def command(self):
        return self.execute(EXECUTE, (0, COMMAND, PATH))

    def dispatch(self, argument):
        assert self.pending == CALLBACK | 1
        self.pending = None
        self.execute(CALLBACK, (argument,))
        event = self.select("event.publish")[-1]
        assert event["payload"] == [0, argument] and event["event_id"] == 11

    def select(self, name):
        return [event for event in self.events if event.get("fixture") == name or event.get("native") == name]

    def vfx_state(self):
        return [self.get(VFX_STATE), self.get(VFX_LOOK), self.get(VFX_GEN), self.get(VFX_WAIT, "B")]

    def result(self, name, **extra):
        return dict(name=name, options=self.options, command_phase=self.get(COMMAND + 4),
                    path_phase=self.get(PATH + 4), active_handle=self.get(ACTIVE),
                    callback_outstanding=self.pending is not None, fields=self.fields,
                    vfx_guarded_state_unchanged=self.vfx_state() == self.vfx_before,
                    instructions=self.instructions, events=self.events, **extra)


def verify(firmware):
    cases, coverage, fixtures = [], set(), set()

    def keep(runner, name, **extra):
        cases.append(runner.result(name, **extra))
        coverage.update(runner.visited)
        fixtures.update(runner.fixture_hits)

    runner = VEF(firmware)
    matrix = []
    for data_type in (0, 1, 2, 3, 4, 255):
        values = []
        for ordinal in range(256):
            runner.parameters(data_type, ordinal)
            result = runner.validate()
            assert result == (0 if ordinal in ALLOWED.get(data_type, []) else 2), (data_type, ordinal, result)
            values.append(result)
        matrix.append(dict(data_type=data_type, returns_by_ordinal_0_to_255=values,
                           accepted=[i for i, result in enumerate(values) if result == 0]))
    coverage.update(runner.visited)
    fixtures.update(runner.fixture_hits)

    # Every accepted combination reaches the expected command/wrapper route.
    for data_type, ordinals in ALLOWED.items():
        for ordinal in ordinals:
            r = VEF(firmware, data_type=data_type, ordinal=ordinal)
            assert r.check() == 0 and r.fields == [0]
            assert r.command() == 0 and r.get(COMMAND + 4) == 1 and r.get(PATH + 4) == 1
            assert r.command() == 0 and r.get(COMMAND + 4) == 2 and r.get(PATH + 4) == 2
            route = "mlut.builtin_look" if data_type == 0 else "User3D" if data_type == 1 else "User3DArtGen"
            assert len(r.select(route)) == 1 and r.pending == CALLBACK | 1
            if data_type != 0:
                assert r.get(ACTIVE) == 0 and len(r.select("queue.submit")) == 1
                assert r.select("hardware.lut_payload")[0]["virtual"] == hex(VIRTUAL[0] + 0x100)
                if data_type == 3:
                    assert r.select("hardware.register_payload")[0]["virtual"] == hex(VIRTUAL[1] + 0x200)
            r.dispatch(42)
            assert [e["event_id"] for e in r.select("command.event.request")] == [1, 11]
            keep(r, f"accepted_type{data_type}_ordinal{ordinal}", route=route)

    for data_type, ordinal in ((1, 3), (3, 10), (0, 10), (2, 10), (255, 255)):
        r = VEF(firmware, data_type=data_type, ordinal=ordinal)
        assert r.check() == 2 and r.fields == [2] and not r.select("path.parameter.set")
        assert not r.pending and not r.select("queue.submit")
        keep(r, f"precheck_rejects_type{data_type}_ordinal{ordinal}")

    for phase in range(4):
        r = VEF(firmware, path_phase=phase)
        assert r.command() == 0 and len(r.select("mlut.pre")) == int(phase in (0, 2))
        assert r.get(COMMAND + 4) == 1 and r.get(PATH + 4) == (1 if phase in (0, 2) else phase)
        keep(r, f"initial_path_phase{phase}")
    for code in (3, 8):
        r = VEF(firmware, pre_return=code)
        assert r.command() == 0 and r.get(COMMAND + 4) == 1 and r.get(PATH + 4) == 1
        keep(r, f"initial_command_ignores_pre_return{code}")

    for data_type, ordinal in ((1, 10), (3, 3)):
        for name, options, expected in (
            ("hardware_return3", {"hardware_return": 3}, 13),
            ("hardware_return8", {"hardware_return": 8}, 65535),
            ("both_handle_queries_fail", {"lookup_returns": (8, 8)}, 65535),
            ("different_active_handle", {"active_handle": 99}, 65535),
            ("index_lookup_fails", {"index_return": 0}, 65535),
            ("lut_stage_fails", {"lut_return": 1}, 65535),
            ("look_selection_fails", {"look_return": 1}, 65535),
            ("queue_rejects", {"queue_return": 1}, 65535),
            ("kick_fails_after_queue_accepted", {"kick_return": 8}, 65535),
            ("resource_index7_rejected_before_queue", {"resource_index": 7}, 65535),
        ):
            r = VEF(firmware, data_type=data_type, ordinal=ordinal, **options)
            assert r.check() == 0 and r.command() == 0
            result = r.command()
            assert result == expected and r.fields[-1] == expected and r.get(COMMAND + 4) == 1
            assert [e["event_id"] for e in r.select("command.event.request")] == [1]
            assert (r.pending is not None) == (name == "kick_fails_after_queue_accepted")
            keep(r, f"type{data_type}_{name}", native_command_return=result)

    r = VEF(firmware, data_type=3, ordinal=3, register_return=1)
    assert r.check() == 0 and r.command() == 0 and r.command() == 65535
    assert not r.pending and r.fields[-1] == 65535
    keep(r, "artgen_register_stage_fails")

    for code, expected in ((0, 0), (3, 13), (1, 65535), (8, 65535), (0xffffffff, 65535)):
        r = VEF(firmware)
        assert r.execute(0x6e0188, (code,)) == expected
        keep(r, f"driver_return_conversion_{code}", expected_return=expected)

    r = VEF(firmware, lookup_returns=(8, 0))
    assert r.check() == 0 and r.command() == 0 and r.command() == 0 and r.pending
    assert [e["query"] for e in r.select("resource.handle_to_mask")] == [0, 1]
    keep(r, "second_resource_handle_query_succeeds")

    r = VEF(firmware, handle=0)
    assert r.command() == 0 and r.fields == [0] and not r.pending
    assert not r.select("mlut.pre") and not r.select("User3D") and r.get(COMMAND + 4) == 0
    keep(r, "zero_handle_returns_zero_without_submission")

    for data_type, ordinal in ((0, 11), (1, 10), (3, 3)):
        r = VEF(firmware, data_type=data_type, ordinal=ordinal)
        assert r.check() == 0 and r.command() == 0 and r.command() == 0
        assert r.pending and not r.select("event.publish")
        keep(r, f"type{data_type}_missing_callback_no_event_is_invented")
    for argument in (0, 8, 42):
        r = VEF(firmware)
        assert r.check() == 0 and r.command() == 0 and r.command() == 0
        r.dispatch(argument)
        keep(r, f"callback_argument{argument}_forwarded_without_status_interpretation")

    for data_type, ordinal, options in (
        (1, 10, {"lut_bytes": 0}),
        (3, 3, {"lut_bytes": 0}),
        (3, 3, {"register_bytes": 0}),
        (3, 3, {"lut_bytes": 0, "register_bytes": 0}),
    ):
        r = VEF(firmware, data_type=data_type, ordinal=ordinal, **options)
        assert r.check() == 0 and r.command() == 0 and r.command() == 0 and r.pending
        assert len(r.select("hardware.lut_payload")) == int(options.get("lut_bytes", 48000) != 0)
        assert len(r.select("hardware.register_payload")) == int(data_type == 3 and options.get("register_bytes", 124) != 0)
        flags = r.select("queue.submit")[0]["words"][7:13]
        assert flags == [int(options.get("lut_bytes", 48000) != 0)] * 6
        keep(r, f"type{data_type}_zero_bytes_{len(cases)}", queued_payload_flags=flags)

    for index in range(4):
        r = VEF(firmware, lut_physical=PHYSICAL[index] + 0x300)
        assert r.check() == 0 and r.command() == 0 and r.command() == 0
        assert r.select("hardware.lut_payload")[0]["virtual"] == hex(VIRTUAL[index] + 0x300)
        keep(r, f"physical_mapping_entry{index}_preserves_64_bit_address")
    for data_type, ordinal in ((1, 10), (3, 3)):
        r = VEF(firmware, data_type=data_type, ordinal=ordinal, lut_physical=PHYSICAL[0] - 1)
        assert r.check() == 0 and r.command() == 0
        try:
            r.command()
        except NativeDiagnostic:
            pass
        else:
            raise AssertionError("Out-of-map physical address did not reach native diagnostic")
        assert not r.select("queue.submit")
        keep(r, f"type{data_type}_unmapped_physical_address_stops_at_diagnostic")

    r = VEF(firmware, lut_physical=PHYSICAL[0] + MAP_BYTES - 16, lut_bytes=48000)
    assert r.check() == 0 and r.command() == 0 and r.command() == 0
    assert r.select("hardware.lut_payload")[0]["bytes"] == 48000
    keep(r, "selected_native_api_only_checks_start_address_lower_length_validation_unverified",
         synthetic_hardware_accepts_descriptor=True, hardware_span_validation_verified=False)

    for outputs, query in (((0, 0, 0), 1), ((1, 0, 0), 1), ((0, 1, 0), 1),
                           ((0, 0, 1), 1), ((2, 0, 0), 1), ((1, 0, 0), 0)):
        r = VEF(firmware, command_phase=2, outputs=outputs, restore_query=query)
        assert r.command() == 0 and r.fields == [0]
        should_query = 1 in outputs
        assert len(r.select("controller.restore.query")) == int(should_query)
        assert len(r.select("controller.restore.request")) == int(should_query and query == 1)
        keep(r, f"synthetic_phase2_outputs_{''.join(map(str, outputs))}_query{query}",
             native_completion_or_scheduler_verified=False)

    return cases, matrix, coverage, fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cp", type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.out_dir.exists():
        parser.error("Output directory exists")
    firmware = args.cp.read_bytes()
    check_hash(firmware, CP_SHA256)
    def word(at):
        return struct.unpack_from("<I", firmware, at - CP_BASE)[0]
    assert word(0xd23d5c) == EXECUTE | 1 and word(0xd23d58) == CHECK | 1
    assert word(0xd23d44) == 0xd23d60
    name_at = word(0xd23d64) - CP_BASE
    assert firmware[name_at:name_at + 64].split(b"\0", 1)[0] == b"N3VEF21VefPathCmdSetLookNameE"
    cases, matrix, coverage, fixtures = verify(firmware)
    matrix_bytes = (json.dumps(matrix, indent=2) + "\n").encode()
    source = Path(__file__).read_bytes()
    dependency = Path(__file__).with_name("emulate_lut_parameter_helpers.py")
    report = dict(
        schema="a6700-lab/cp-vef-mlut-emulation/1", result="passed", camera_access=False,
        cp_sha256=CP_SHA256, tool_sha256=hashlib.sha256(source).hexdigest(),
        dependency_sha256={dependency.name: hashlib.sha256(dependency.read_bytes()).hexdigest()},
        parameter_matrix=dict(types=[0, 1, 2, 3, 4, 255], ordinals_per_type=256,
                              native_invocations=1536, accepted=ALLOWED,
                              report="parameter-matrix.json", sha256=hashlib.sha256(matrix_bytes).hexdigest()),
        case_count=len(cases), cases=cases, unique_native_instruction_addresses=len(coverage),
        native_ranges=[dict(start=hex(a), end_exclusive=hex(b),
                            sha256=hashlib.sha256(firmware[a-CP_BASE:b-CP_BASE]).hexdigest()) for a, b in RANGES],
        named_fixtures=sorted(fixtures),
        command_parameter_layout={"type": "+0:u8", "ordinal": "+1:u8", "lut_bytes": "+4:u32",
                                  "lut_physical": "+8:u64", "register_bytes": "+0x14:u32",
                                  "register_physical": "+0x18:u64",
                                  "driver_descriptor": "{physical:u64, bytes:u32}"},
        physical_map_fixture=dict(address=hex(MAP_TABLE), entry_bytes=32,
                                  physical_bases=[hex(a) for a in PHYSICAL], virtual_bases=[hex(a) for a in VIRTUAL],
                                  bytes_per_entry=MAP_BYTES, firmware_data_page_replaced=True),
        full_resolution_still_job_verified=False, jpeg_buffer_connection_verified=False,
        real_scheduler_verified=False, hardware_ready_verified=False,
        limitations=[
            "Original precheck executes its non-queue-draining path; controller phase 15, command cancellation and real scheduling remain untested",
            "All objects, virtual interfaces, resource handles, active ownership and four physical mappings are explicitly synthetic",
            "Original Execute, user/art wrappers, User3D/User3DArtGen APIs, asynchronous message packer and VEF callback execute; builtin driver is a fixture",
            "Pre-stage, low-level LUT/register data handling, MLUT hardware, queue, kick and loggers are named fixtures",
            "Callback r0 is forwarded as an opaque word; neither its real semantic meaning nor event consumption is established",
            "An accepted queue, zero return, phase 2 or event 11 does not establish hardware completion, payload readiness or JPEG processing",
            "No AP caller, VEF input/output-pin provenance, full-resolution image buffer ownership, lifetime or JPEG route is established",
            "Parameter matrix entries are offline native invocations, not physical-camera experiments or image-quality measurements",
            "The selected native API validates physical start addresses; the lower hardware fixture does not validate the full byte span",
            "No camera access, CP write, firmware patch, image pixels, DMA or JPEG encoding is performed",
        ])
    args.out_dir.mkdir(parents=True)
    (args.out_dir / "parameter-matrix.json").write_bytes(matrix_bytes)
    (args.out_dir / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    snapshot = args.out_dir / "source"
    snapshot.mkdir()
    (snapshot / Path(__file__).name).write_bytes(source)
    (snapshot / dependency.name).write_bytes(dependency.read_bytes())
    print(f"Passed {len(cases)} VEF scenarios and 1536 parameter checks. Hardware and still/JPEG route remain unverified.")


if __name__ == "__main__":
    main()
