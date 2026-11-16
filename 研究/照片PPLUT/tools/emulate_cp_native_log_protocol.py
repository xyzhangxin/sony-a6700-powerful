"""Offline execution of stock AP/CP log retrieval and CP ring publication.

OSAL allocation/transport/free, backup storage, cache maintenance, mutexes,
subsystem lookup and libc are named fixtures. No device API or backend is
imported. Addresses belong only to the checked firmware and emulated memory.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from unicorn import (Uc, UC_ARCH_ARM64, UC_ARCH_ARM, UC_MODE_ARM,
                     UC_MODE_THUMB, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE,
                     UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_EXEC)
import unicorn.arm64_const as a64
import unicorn.arm_const as arm

CP_BASE = 0x108000
CP_SHA = '61b7fe5c0ce227ea865d799b81fe77ae359b7c5dcd39d13c9c2241d9cad23559'
ULOG_SHA = 'ce8ecde7e4826cad86edf2000e3330c6234dbdf127474751ecb61305abfc2497'
AP_BASE, STACK, TOP, DATA, RETURN = 0x1000000, 0x8000000, 0x800f000, 0x9000000, 0xa000000
MSG, OUT_ADDR, OUT_SIZE = DATA + 0x100, DATA + 0x200, DATA + 0x210
CFG, NODE, POOL = DATA + 0x1000, DATA + 0x2000, DATA + 0x10000
RING, CONTENT = POOL + 0x100, POOL + 0x200
G_CFG, G_POOL, G_STOP = 0x19ca488, 0x19ca490, 0x19ca4a0
CP_RANGES = ((0x55a60c, 0x55a63e), (0x55a664, 0x55a752),
             (0x55bc64, 0x55bca2), (0x55b044, 0x55b142),
             (0x55c2fc, 0x55c6ae))
AP_RANGES = ((0x7148, 0x7248), (0x75c4, 0x7600), (0x6c08, 0x6c88),
             (0x59a0, 0x59e4), (0x5bcc, 0x5c3c))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def u32(value):
    return struct.pack('<I', value & 0xffffffff)


class AP:
    def __init__(self, raw, **faults):
        self.faults, self.events, self.writes = faults, [], []
        self.u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.u.mem_map(AP_BASE, 0x20000)
        elf = ELFFile(io.BytesIO(raw))
        for seg in elf.iter_segments():
            if seg['p_type'] == 'PT_LOAD':
                self.u.mem_write(AP_BASE + seg['p_vaddr'], seg.data())
        rel = elf.get_section_by_name('.rela.dyn')
        symbols = elf.get_section(rel['sh_link'])
        for item in rel.iter_relocations():
            kind = item['r_info_type']
            if kind == 1027:
                value = AP_BASE + item['r_addend']
            elif kind in (257, 1025):
                sym = symbols.get_symbol(item['r_info_sym'])
                if not sym['st_value']:
                    continue
                value = AP_BASE + sym['st_value'] + item['r_addend']
            else:
                raise AssertionError(kind)
            self.u.mem_write(AP_BASE + item['r_offset'], struct.pack('<Q', value))
        self.imports = {}
        rel = elf.get_section_by_name('.rela.plt')
        symbols = elf.get_section(rel['sh_link'])
        plt = elf.get_section_by_name('.plt')['sh_addr']
        for n, item in enumerate(rel.iter_relocations()):
            self.imports[AP_BASE + plt + 32 + 16*n] = symbols.get_symbol(item['r_info_sym']).name
        self.u.mem_protect(AP_BASE, 0x9000, UC_PROT_READ | UC_PROT_EXEC)
        for address, size in ((STACK, 0x10000), (DATA, 0x40000), (RETURN, 4096)):
            self.u.mem_map(address, size)
        self.u.mem_write(DATA, b'\xa5' * 0x40000)
        self.u.hook_add(UC_HOOK_CODE, self.guard)
        self.u.hook_add(UC_HOOK_MEM_WRITE, self.on_write)

    def reg(self, n):
        return getattr(a64, f'UC_ARM64_REG_X{n}')

    def r(self, n):
        return self.u.reg_read(self.reg(n))

    def ret(self, value=0):
        self.u.reg_write(self.reg(0), value & ((1 << 64)-1))
        self.u.reg_write(a64.UC_ARM64_REG_PC, self.u.reg_read(a64.UC_ARM64_REG_LR))

    def on_write(self, uc, access, at, size, value, user):
        assert (STACK <= at < at+size <= STACK+0x10000 or
                DATA <= at < at+size <= DATA+0x40000 or
                AP_BASE+0x19000 <= at < at+size <= AP_BASE+0x1a000), hex(at)
        if not STACK <= at < STACK+0x10000:
            self.writes.append(dict(address=hex(at), size=size))

    def guard(self, uc, at, size, user):
        if any(AP_BASE+s <= at < AP_BASE+e for s, e in AP_RANGES):
            return
        name = self.imports.get(at)
        args = [self.r(n) for n in range(4)]
        if name == 'osal_valloc_msg_wait':
            assert args[0] == 0x808f084a and args[2:] == [24, 1]
            rc = self.faults.get('allocation', 0)
            if not rc:
                self.u.mem_write(args[1], struct.pack('<Q', MSG))
            self.events.append(dict(fixture=name, key=hex(args[0]), bytes=24,
                                    wait_argument=args[3], return_code=rc))
            self.ret(rc)
        elif name == 'osal_snd_sync_msg':
            assert args[:2] == [0x808f084a, MSG]
            request = bytes(self.u.mem_read(MSG, 24))
            assert request[1] == 0 and request[8:20] == bytes(12)
            rc = self.faults.get('send', 0)
            if not rc:
                self.u.mem_write(MSG+1, bytes([self.faults.get('status', 1)]))
                self.u.mem_write(MSG+8, struct.pack('<QI', 0x0123456789abcdef, 0x1234))
            self.events.append(dict(fixture=name, selector=request[0],
                uninitialized_request_bytes_not_assumed_zero=True, return_code=rc))
            self.ret(rc)
        elif name == 'osal_free_msg':
            assert args[:2] == [0x808f084a, MSG]
            rc = self.faults.get('free', 0)
            self.events.append(dict(fixture=name, return_code=rc))
            self.ret(rc)
        elif name == 'ulogio_read_backup':
            assert args[0] == 0x51000e and args[2] == 1
            rc = self.faults.get('backup', 0)
            if not rc:
                self.u.mem_write(args[1], bytes([self.faults.get('recovery_enabled', 0)]))
            self.events.append(dict(fixture=name, id=hex(args[0]), return_code=rc))
            self.ret(rc)
        elif name == 'osal_create_thread':
            assert args[0] == AP_BASE+0x194f8
            entry = struct.unpack('<Q', self.u.mem_read(args[0]+16, 8))[0]
            assert entry == AP_BASE+0x70d4
            self.events.append(dict(fixture=name, entry_offset=hex(entry-AP_BASE),
                                    thread_not_executed=True))
            self.ret(0)
        elif name in ('printf', 'puts'):
            self.events.append(dict(fixture='libc_diagnostic', name=name))
            self.ret(0)
        else:
            raise AssertionError(f'Unexpected AP execution {at:#x}: {name}')

    def call(self, entry, *args):
        self.u.reg_write(a64.UC_ARM64_REG_SP, TOP)
        self.u.reg_write(a64.UC_ARM64_REG_LR, RETURN)
        for n, value in enumerate(args):
            self.u.reg_write(self.reg(n), value)
        self.u.emu_start(AP_BASE+entry, RETURN, timeout=300000, count=30000)
        assert self.u.reg_read(a64.UC_ARM64_REG_PC) == RETURN
        assert self.u.reg_read(a64.UC_ARM64_REG_SP) == TOP
        return self.r(0) & 0xffffffff


class CP:
    def __init__(self, raw, capacity=128, **faults):
        self.faults, self.events, self.writes, self.locked = faults, [], [], False
        self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_LITTLE_ENDIAN)
        size = (len(raw)+4095) & ~4095
        self.u.mem_map(CP_BASE, size)
        self.u.mem_write(CP_BASE, raw)
        self.u.mem_protect(CP_BASE, size, UC_PROT_READ | UC_PROT_EXEC)
        for address, size in ((STACK, 0x10000), (DATA, 0x40000),
                              (RETURN, 4096), (0x19ca000, 0x1000)):
            self.u.mem_map(address, size)
        self.put(G_CFG, CFG)
        self.put(G_POOL, POOL)
        self.put(CFG+0x38, 0x18000-0x3c)
        self.u.mem_write(NODE+0xe, struct.pack('<hh', 2, 0))
        self.put(NODE+0x20, RING-POOL)
        self.u.mem_write(CONTENT, b'\xcc' * capacity)
        self.u.mem_write(RING+4, struct.pack('<7I', CONTENT-POOL, capacity,
                             capacity-1, CONTENT-POOL, CONTENT-POOL, CONTENT-POOL, 0))
        self.capacity = capacity
        self.u.hook_add(UC_HOOK_CODE, self.guard)
        self.u.hook_add(UC_HOOK_MEM_WRITE, self.on_write)

    def r(self, n):
        return self.u.reg_read(arm.UC_ARM_REG_R0+n)

    def put(self, at, value):
        self.u.mem_write(at, u32(value))

    def word(self, at):
        return struct.unpack('<I', self.u.mem_read(at, 4))[0]

    def ret(self, value=0):
        self.u.reg_write(arm.UC_ARM_REG_R0, value & 0xffffffff)
        self.u.reg_write(arm.UC_ARM_REG_PC, self.u.reg_read(arm.UC_ARM_REG_LR))

    def on_write(self, uc, access, at, size, value, user):
        assert (STACK <= at < at+size <= STACK+0x10000 or
                DATA <= at < at+size <= DATA+0x40000 or
                0x19ca000 <= at < at+size <= 0x19cb000), hex(at)
        if not STACK <= at < STACK+0x10000:
            self.writes.append(dict(address=hex(at), size=size,
                pc=hex(self.u.reg_read(arm.UC_ARM_REG_PC))))

    def guard(self, uc, at, size, user):
        if any(s <= at < e for s, e in CP_RANGES):
            return
        if at == 0x558884:
            assert (self.r(0), self.r(1), self.r(2), self.r(3)) == (
                0x908f084b, 0x19ca46c, 0x55a665, 0)
            self.events.append(dict(fixture='OSAL_callback_registration',
                key=hex(self.r(0)), callback=hex(self.r(2))))
            self.ret()
        elif at == 0xbccb2e:
            rc = self.faults.get('backup_size', 8)
            self.events.append(dict(fixture='backup_get_datasize', id=hex(self.r(0)), result=rc))
            self.ret(rc)
        elif at == 0xbccc92:
            key = self.r(0)
            value = 0x90010003 if key in (0xfa000b, 0xfa001f, 0xfa006e) else 0x135
            self.u.mem_write(self.r(1), struct.pack('<Q', value))
            rc = self.faults.get('backup_read', 8)
            self.events.append(dict(fixture='backup_read', id=hex(key), result=rc))
            self.ret(rc)
        elif at == 0x5fa164:
            self.events.append(dict(fixture='cache_clean_invalidate_API',
                start=hex(self.r(0)), bytes=self.r(1), hardware_cache_not_executed=True))
            self.ret()
        elif at == 0xbcfbca:
            assert self.r(0) == MSG
            self.ret(0x8f013a)
        elif at == 0xbd0942:
            assert self.r(0) == 0x2ae
            self.events.append(dict(fixture='registered_subsystem_lookup', sid=hex(self.r(0))))
            self.ret(0 if self.faults.get('missing_subsystem') else NODE)
        elif at == 0xbcf37c:
            assert self.r(0) == RING+0x20 and not self.locked
            self.locked = True
            self.events.append(dict(fixture='ring_mutex_acquire', real_scheduling=False))
            self.ret()
        elif at == 0xbcf3d8:
            assert self.r(0) == RING+0x20 and self.locked
            self.locked = False
            self.events.append(dict(fixture='ring_mutex_release'))
            self.ret()
        elif at == 0x612240:
            self.u.mem_write(self.r(0), bytes(self.u.mem_read(self.r(1), self.r(2))))
            self.ret()
        elif at == 0x612934:
            self.u.mem_write(self.r(0), bytes(self.r(1)))
            self.ret()
        elif at == 0x559190:
            assert self.r(0) == 0x8f013a and self.r(2) == 20 and self.r(3) == 1
            self.put(self.r(1), DATA+0x3000)
            self.events.append(dict(fixture='oversize_notice_message_allocation', bytes=20))
            self.ret()
        elif at == 0xbcfbc4:
            self.events.append(dict(fixture='message_prefix_setter'))
            self.ret()
        elif at == 0x558ac4:
            self.events.append(dict(fixture='oversize_notice_message_send'))
            self.ret()
        elif at == 0x55928c:
            assert self.r(0) == 0x8f013a and self.r(1) == MSG
            self.events.append(dict(fixture='original_received_message_free'))
            self.ret()
        elif at in (0x5f99a8, 0x61b660):
            self.events.append(dict(fixture='libc_diagnostic_or_format', entry=hex(at)))
            self.ret()
        else:
            raise AssertionError(f'Unexpected CP execution {at:#x}')

    def call(self, entry, *args):
        self.u.reg_write(arm.UC_ARM_REG_SP, TOP)
        self.u.reg_write(arm.UC_ARM_REG_LR, RETURN | 1)
        for n, value in enumerate(args):
            self.u.reg_write(arm.UC_ARM_REG_R0+n, value)
        self.u.emu_start(entry | 1, RETURN, timeout=400000, count=50000)
        assert self.u.reg_read(arm.UC_ARM_REG_PC) == RETURN
        assert self.u.reg_read(arm.UC_ARM_REG_SP) == TOP
        assert not self.locked

    def retrieve(self, selector):
        self.u.mem_write(MSG, bytes([selector])+bytes(23))
        self.call(0x55a664, MSG)
        raw = bytes(self.u.mem_read(MSG, 24))
        return dict(status=raw[1], address=struct.unpack_from('<Q', raw, 8)[0],
                    bytes=struct.unpack_from('<I', raw, 16)[0])

    def emit(self, message):
        self.u.mem_write(MSG, message)
        self.call(0x55b044, MSG, 0x8f013a)

    def records(self):
        ring = bytes(self.u.mem_read(CONTENT, self.capacity))
        cursor = self.word(RING+0x10) - (CONTENT-POOL)
        result = []

        def read_wrapped(start, n):
            assert n <= self.capacity
            return (ring+ring)[start:start+n]

        for _ in range(self.word(RING+0x1c)):
            length, sid, area, stamp = struct.unpack('<IHHQ', read_wrapped(cursor, 16))
            assert length == 24 and sid == 0x2ae and area == 1
            payload = read_wrapped((cursor+16) % self.capacity, length)
            result.append(dict(payload=payload.hex(), raw_timestamp=hex(stamp)))
            cursor = (cursor+16+length) % self.capacity
        assert cursor == self.word(RING+0x14) - (CONTENT-POOL)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cp', type=Path, required=True)
    parser.add_argument('--ulog-lib', type=Path, required=True, help='Locally extracted libosal_ulogio.so')
    parser.add_argument('--trace-report', type=Path, required=True, help='Output of emulate_cp_photo_native_trace_clock.py')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Use a new report path')
    cp = args.cp.read_bytes()
    lib = args.ulog_lib.read_bytes()
    trace_raw = args.trace_report.read_bytes()
    assert sha(cp) == CP_SHA and sha(lib) == ULOG_SHA
    trace = json.loads(trace_raw)
    assert trace['schema'] == 'd6700/native-photo-trace-clock-feasibility/1'
    assert trace['cp_sha256'] == CP_SHA and trace['result'] == 'passed' and trace['camera_access'] is False
    traces = trace['cases']
    markers = {c['marker']: bytes.fromhex(next(e['message'] for e in c['events']
               if e.get('fixture') == 'message_send')) for c in traces if c.get('marker')}
    cases = []

    for selector in (1, 2, 3, 4, 0x101):
        m = AP(lib)
        rc = m.call(0x7148, selector, OUT_ADDR, OUT_SIZE)
        assert rc == 0
        assert bytes(m.u.mem_read(OUT_ADDR, 8)) == struct.pack('<Q', 0x0123456789abcdef)
        assert bytes(m.u.mem_read(OUT_SIZE, 4)) == u32(0x1234)
        assert [e['fixture'] for e in m.events] == ['osal_valloc_msg_wait', 'osal_snd_sync_msg', 'osal_free_msg']
        cases.append(dict(name='AP_sync_address_size_request', selector=selector,
                          wire_selector=selector & 255, result=rc, events=m.events))
    for fault in ({'allocation': 7}, {'send': 7}, {'status': 0}, {'status': 0x80},
                  {'status': 0x81}, {'free': 7}):
        m = AP(lib, **fault)
        rc = m.call(0x7148, 1, OUT_ADDR, OUT_SIZE)
        success = 'free' in fault
        assert rc == (0 if success else 0xffffffff)
        assert (bytes(m.u.mem_read(OUT_ADDR, 8)) != b'\xa5'*8) == success
        assert (bytes(m.u.mem_read(OUT_SIZE, 4)) != b'\xa5'*4) == success
        assert sum(e['fixture'] == 'osal_free_msg' for e in m.events) == int('allocation' not in fault)
        cases.append(dict(name='AP_retrieval_failure_and_cleanup_boundary', fault=fault,
                          result=hex(rc), output_copied=success, events=m.events))
    for enabled, backup_rc in ((0, 0), (1, 0), (0, 7)):
        m = AP(lib, recovery_enabled=enabled, backup=backup_rc)
        m.call(0x75c4)
        assert sum(e['fixture'] == 'osal_create_thread' for e in m.events) == int(enabled and not backup_rc)
        cases.append(dict(name='AP_recovery_may_create_a_thread', enabled=enabled,
                          backup_result=backup_rc, events=m.events))

    # Original AP filter and its hash-table lookup execute; these are NOT a
    # model of CP filter state or proof of the callers' acceptance semantics.
    for label, global_level, sub_level, comp_level, level, expected, initialized in (
        ('uninitialized', 0, None, None, 4, 0, False),
        ('global_accept_return', 2, None, None, 2, 0, True),
        ('global_reject_return', 2, None, None, 3, 0xffffffff, True),
        ('subsystem_override', 0, 3, None, 2, 0, True),
        ('component_override', 4, 4, 1, 2, 0xffffffff, True),
        ('invalid_level', 4, 4, 4, 5, 0xffffffff, True)):
        m = AP(lib)
        sub_table, comp_table, sub, comp = DATA+0x4000, DATA+0x5000, DATA+0x6000, DATA+0x6100
        m.u.mem_write(sub_table, bytes(0x2000))
        m.u.mem_write(sub, bytes(0x200))
        m.u.mem_write(AP_BASE+0x19ae8, struct.pack('<Q', sub_table if initialized else 0))
        m.u.mem_write(AP_BASE+0x19ad0, struct.pack('<Q', comp_table if initialized else 0))
        m.u.mem_write(AP_BASE+0x194f4, bytes([global_level]))
        sid, cid = 0x2ae, 0x12345678
        if sub_level is not None:
            m.u.mem_write(sub_table+8*((sid ^ sid >> 8) & 255), struct.pack('<Q', sub))
            m.u.mem_write(sub+8, struct.pack('<H', sid))
            m.u.mem_write(sub+0x18, bytes([sub_level]))
        if comp_level is not None:
            bucket = (sid ^ sid >> 8 ^ cid ^ cid >> 8 ^ cid >> 16 ^ cid >> 24) & 255
            m.u.mem_write(comp_table+8*bucket, struct.pack('<Q', comp))
            m.u.mem_write(comp+0x20, struct.pack('<IHB', cid, sid, comp_level))
        rc = m.call(0x6c08, sid, cid, level)
        assert rc == expected and not m.events and not m.writes
        cases.append(dict(name='AP_local_filter_return_value', branch=label,
            input_level=level, result=hex(rc), no_CP_filter_claim=True))

    m = CP(cp)
    m.call(0x55a60c)
    cases.append(dict(name='CP_registers_matching_payload_callback', events=m.events,
                      AP_CP_key_translation_not_emulated=True))
    for selector in (0, 1, 2, 3, 4, 5):
        m = CP(cp)
        reply = m.retrieve(selector)
        valid = 1 <= selector <= 4
        assert reply['status'] == (1 if valid else 0x80)
        assert reply['address'] == (CFG if selector == 1 else 0x90010003 if valid else 0)
        assert reply['bytes'] == (0x18000 if selector == 1 else 0x135 if valid else 0)
        assert all(MSG <= int(w['address'], 16) < MSG+24 for w in m.writes)
        caches = [e for e in m.events if e['fixture'] == 'cache_clean_invalidate_API']
        assert len(caches) == int(valid)
        if selector == 1:
            assert caches[0]['start'] == hex(CFG) and caches[0]['bytes'] == 0x18000
        elif valid:
            assert caches[0]['start'] == '0x90010000' and caches[0]['bytes'] == 0x140
        cases.append(dict(name='CP_address_selector_and_cache_boundary', selector=selector,
                          reply=reply, events=m.events, native_ring_writes=False))
    for fault in ({'backup_size': 7}, {'backup_read': 7}):
        m = CP(cp, **fault)
        reply = m.retrieve(2)
        assert reply == dict(status=0x81, address=0, bytes=0)
        assert not any(e['fixture'] == 'cache_clean_invalidate_API' for e in m.events)
        cases.append(dict(name='CP_backup_read_failure', fault=fault, reply=reply, events=m.events))

    sequence = [markers['MLUT_begin'], markers['MLUT_end_success'],
                markers['MLUT_begin'], markers['MLUT_end_wait_failure']]
    for capacity, count in ((256, 2), (112, 3), (128, 4)):
        m = CP(cp, capacity=capacity)
        for msg in sequence[:count]:
            m.emit(msg)
        records = m.records()
        retained = min(count, (capacity-1)//40)
        assert len(records) == retained
        for record, msg in zip(records, sequence[count-retained:count]):
            assert record['payload'] == msg[24:48].hex()
            assert record['raw_timestamp'] == hex(struct.unpack_from('<Q', msg, 16)[0])
        cases.append(dict(name='CP_original_dispatch_and_ring_publication',
            capacity_fixture=capacity, submitted=count, retained=retained,
            old_records_evicted=count-retained, ring_header_format='<IHHQ',
            records=records, events=m.events, global_configuration_not_live=True))
    m = CP(cp)
    m.put(G_STOP, 1)
    m.emit(markers['MLUT_begin'])
    assert m.records() == [] and not m.writes
    cases.append(dict(name='CP_dispatch_stop_flag_suppresses_storage', events=m.events,
                      initial_stop_flag_is_a_fixture_not_a_camera_setting=True))
    m = CP(cp, capacity=32)
    m.emit(markers['MLUT_begin'])
    assert m.records() == [] and not m.locked
    assert any(e['fixture'] == 'oversize_notice_message_send' for e in m.events)
    cases.append(dict(name='CP_record_exceeds_ring_capacity', events=m.events))
    m = CP(cp)
    before = m.retrieve(1)
    old = bytes(m.u.mem_read(before['address'], before['bytes']))
    m.emit(markers['MLUT_begin'])
    after = m.retrieve(1)
    new = bytes(m.u.mem_read(after['address'], after['bytes']))
    assert before == after and old != new and len(m.records()) == 1
    cases.append(dict(name='CP_retrieval_returns_mutable_source_not_frozen_snapshot',
        pointer_and_size_unchanged=True, bytes_changed_by_later_original_publication=True,
        no_real_concurrent_reader_executed=True, events=m.events))

    report = dict(schema='d6700/native-AP-CP-log-protocol/1', result='passed',
        recorded_utc=datetime.now(timezone.utc).isoformat(), camera_access=False,
        device_backend_constructed=False, installable_candidate=False,
        sources=dict(cp_sha256=sha(cp), libosal_ulogio_sha256=sha(lib),
            trace_message_report_sha256=sha(trace_raw), tool_sha256=sha(Path(__file__).read_bytes())),
        native_CP_slices={f'{s:#x}:{e-s:#x}': sha(cp[s-CP_BASE:e-CP_BASE]) for s,e in CP_RANGES},
        native_AP_slices={f'{s:#x}:{e-s:#x}': sha(lib[s:e]) for s,e in AP_RANGES},
        case_count=len(cases), cases=cases,
        limitations=[
            'AP and CP original functions execute in separate virtual machines. OSAL routing/key translation, scheduling and transport ownership are not emulated end-to-end.',
            'Backup store, mutex, registered subsystem, cache maintenance, message APIs and libc are fixtures. No target log capacity or enabled filter was measured.',
            'The bounded original get-address handler has no log cursor write, stop operation or retained copy; this does not prove a coherent AP read while CP produces new records.',
            'AP filter cases execute the local AP return convention only, without proving its callers or CP storage-filter behavior.',
            'Native ring rollover was executed against fabricated configuration; no missing hardware log or historical shutdown cause is inferred.',
            'No event hook, persistent flag, camera session, LUT effect, callback drain or JPEG pixel proof is implemented.'],
        findings=[
            'AP 7148 requests a24-byte message using osal_valloc_msg_wait, synchronously asks for address/size, then frees. A successful reply followed by free failure still returns0.',
            'CP a60c registers callback55a665. Original55a664 handles selector1 as current configuration allocation and2/3/4 as backed-up address/size pairs; it invokes cache clean/invalidate.',
            'AP recovery75c4 may create a thread; the associated70d4 path includes file creation and other callbacks. It is not a passive log read.',
            'Original55b044 accepts type2 into55c2fc unless a stop flag suppresses storage. Ring records contain payload length, subsystem, area and raw timestamp before the unchanged24-byte MLUT payload.',
            'The stock writer wraps headers/payloads, evicts complete oldest records for capacity and can reject a record larger than its ring.',
            'Existing markers and mutable log storage are partial timing evidence, not a complete task/owner/generation observation protocol.'],
        photo_LUT_preview_verified=False, JPEG_pixels_verified=False,
        callback_drain_proved=False, actual_log_read_verified=False,
        new_observer_implemented=False)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps(dict(result='passed', case_count=len(cases), camera_access=False)))


if __name__ == '__main__':
    main()
