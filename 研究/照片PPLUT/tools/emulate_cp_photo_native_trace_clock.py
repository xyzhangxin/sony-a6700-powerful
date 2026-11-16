"""Execute stock trace-message construction and its clock veneer, locally.

Counter reads, allocation, send/fallback/disposal and memory APIs are named
fixtures. The hardware counter is NEVER read from the camera or host. This
does not enable native logging, introduce a hook, or decode real captured logs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_LITTLE_ENDIAN,
    UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC)
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
from emulate_lut_parameter_helpers import CP_BASE, CP_SHA256, check_hash

CLOCK, VENEER, COUNTER = 0xbd16e0, 0xc12c84, 0x5f976c
LOGGER, BUILD = 0xb6119a, 0xb61266
RETURN, STACK, TOP, DATA = 0xa000000, 0x8000000, 0x800f000, 0x9000000
BUFFERS = (DATA + 0x100, DATA + 0x200)
TRACE_KEY, FALLBACK_KEY = 0x8f013a, 0x8f0313
RANGES = ((LOGGER, 0xb61318), (0xbcfbbc, 0xbcfbca),
          (CLOCK, CLOCK + 4), (VENEER, VENEER + 12), (COUNTER, COUNTER + 20))


class NativeTraceClock:
    def __init__(self, raw, count=0x0123456789abcdef, allocation=(0, 0), send=0):
        self.count, self.allocation, self.send = count, allocation, send
        self.events, self.native_writes, self.allocations = [], [], 0
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_LITTLE_ENDIAN)
        size = (len(raw) + 4095) & ~4095
        self.uc.mem_map(CP_BASE, size)
        self.uc.mem_write(CP_BASE, raw)
        self.uc.mem_protect(CP_BASE, size, UC_PROT_READ | UC_PROT_EXEC)
        for at, size in ((STACK, 0x10000), (DATA, 4096), (RETURN, 4096)):
            self.uc.mem_map(at, size, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_write(DATA, bytes([0xa5]) * 4096)
        self.uc.hook_add(UC_HOOK_CODE, self.guard)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write)

    def r(self, n):
        return self.uc.reg_read(UC_ARM_REG_R0 + n)

    def put(self, at, value):
        self.uc.mem_write(at, struct.pack('<I', value))

    def ret(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def on_write(self, uc, access, at, size, value, user):
        assert (STACK <= at and at + size <= STACK + 0x10000 or
                DATA <= at and at + size <= DATA + 4096), hex(at)
        if DATA <= at:
            self.native_writes.append(dict(at=hex(at), bytes=size, value=hex(value),
                pc=hex(uc.reg_read(UC_ARM_REG_PC))))

    def guard(self, uc, at, size, user):
        if at == COUNTER + 4:
            # Model MRRC's returned register pair and execute original ISB,
            # 64-bit right shift and return. No real privileged instruction.
            self.uc.reg_write(UC_ARM_REG_R0 + 2, self.count & 0xffffffff)
            self.uc.reg_write(UC_ARM_REG_R0 + 3, self.count >> 32)
            self.events.append(dict(fixture='architected_counter_read', raw=hex(self.count),
                real_counter_read=False, frequency_verified=False))
            self.uc.reg_write(UC_ARM_REG_PC, at + 4)
            return
        if any(start <= at < end for start, end in RANGES):
            return
        if at == 0x612934:
            self.uc.mem_write(self.r(0), bytes(self.r(1)))
            self.events.append(dict(fixture='memory_zero', bytes=self.r(1)))
            self.ret()
        elif at == 0x612240:
            self.uc.mem_write(self.r(0), bytes(self.uc.mem_read(self.r(1), self.r(2))))
            self.events.append(dict(fixture='memory_copy', bytes=self.r(2)))
            self.ret()
        elif at == 0x559190:
            ordinal = self.allocations
            self.allocations += 1
            assert ordinal < 2
            key, length = (TRACE_KEY, 56) if ordinal == 0 else (FALLBACK_KEY, 32)
            assert (self.r(0), self.r(2), self.r(3)) == (key, length, 0)
            rc = self.allocation[ordinal]
            if rc == 0:
                self.put(self.r(1), BUFFERS[ordinal])
            self.events.append(dict(fixture='message_allocation', key=hex(key),
                bytes=length, return_r0=rc, buffer=hex(BUFFERS[ordinal]) if not rc else None))
            self.ret(rc)
        elif at == 0x558ac4:
            assert (self.r(0), self.r(1)) == (TRACE_KEY, BUFFERS[0])
            self.events.append(dict(fixture='message_send', key=hex(self.r(0)),
                return_r0=self.send, message=bytes(self.uc.mem_read(BUFFERS[0], 56)).hex()))
            self.ret(self.send)
        elif at == 0x558cec:
            assert (self.r(0), self.r(1), self.r(2), self.r(3)) == (FALLBACK_KEY, BUFFERS[1], 0, 0)
            self.events.append(dict(fixture='fallback_message_API', key=hex(self.r(0)),
                exact_ownership_and_delivery_unverified=True))
            self.ret()
        elif at == 0x55928c:
            assert (self.r(0), self.r(1)) == (FALLBACK_KEY, BUFFERS[1])
            self.events.append(dict(fixture='fallback_message_disposal', return_r0=0))
            self.ret()
        else:
            raise RuntimeError(f'Unexpected native execution {at:#x}')

    def call(self, at, args=(), stack=()):
        self.uc.reg_write(UC_ARM_REG_SP, TOP)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        for n, value in enumerate(args):
            self.uc.reg_write(UC_ARM_REG_R0 + n, value)
        for n, value in enumerate(stack):
            self.put(TOP + n * 4, value)
        self.uc.emu_start(at | 1, RETURN, timeout=300000, count=12000)
        assert self.uc.reg_read(UC_ARM_REG_PC) == RETURN
        return self.r(0) | self.r(1) << 32


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cp', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        p.error('Use a new report path')
    raw = a.cp.read_bytes()
    check_hash(raw, CP_SHA256)
    assert struct.unpack_from('<I', raw, VENEER + 8 - CP_BASE)[0] == COUNTER
    assert raw[COUNTER-CP_BASE:COUNTER-CP_BASE+20].hex() == '6ff07ff50e2f53eca310b0e16200a0e11eff2fe1'
    cases = []
    for count in (0, 1, 2, 3, 0xffffffff, 0x100000000, 0x8000000000000000, 0xffffffffffffffff):
        r = NativeTraceClock(raw, count=count)
        result = r.call(CLOCK)
        assert result == count >> 1 and len(r.events) == 1
        assert not r.native_writes
        cases.append(dict(name='original_Thumb_ARM_veneer_and_64bit_counter_shift',
            raw_counter=hex(count), native_return=hex(result), events=r.events))

    marker_inputs = (
        ('MLUT_begin', 0x10000, 0xffffffff, 0x4dca4d),
        ('MLUT_end_success', 0x20000, 0, 0xffffffff),
        ('MLUT_end_wait_failure', 0x20000, 0xfffffff9, 0xffffffff),
        ('MLUT_setup_error', 0xe0010000, 7, 0xffffffff),
    )
    for label, flags, rc, last in marker_inputs:
        r = NativeTraceClock(raw)
        r.call(LOGGER, (0x38, flags, rc, 0xffffffff), (0xffffffff, 0xffffffff, last))
        sent = [e for e in r.events if e.get('fixture') == 'message_send']
        assert len(sent) == 1 and r.allocations == 1
        msg = bytes.fromhex(sent[0]['message'])
        header = struct.unpack_from('<2H2I', msg)
        stamp = struct.unpack_from('<Q', msg, 16)[0]
        payload = list(struct.unpack_from('<6I', msg, 24))
        assert header == (2, 0x2ae, 0 if flags >= 0xe0010000 else 1, 24)
        assert stamp == r.count >> 1
        assert payload == [flags | 0x38, rc, 0xffffffff, 0xffffffff, 0xffffffff, last]
        assert struct.unpack('<I', r.uc.mem_read(BUFFERS[0] - 12, 4))[0] == TRACE_KEY
        cases.append(dict(name='native_trace_marker_message', marker=label,
            timestamp=hex(stamp), payload_words=[hex(w) for w in payload],
            observed_context_owner_handle_generation_in_this_marker=False,
            no_claim_that_all_firmware_diagnostics_lack_those_fields=True, events=r.events))

    for allocation, send in (((7, 7), 0), ((7, 0), 0), ((0, 0), 7)):
        r = NativeTraceClock(raw, allocation=allocation, send=send)
        r.call(LOGGER, (0x38, 0x10000, 0xffffffff, 0xffffffff),
            (0xffffffff, 0xffffffff, 0x4dca4d))
        assert r.allocations == 2
        fallback = [e for e in r.events if e.get('fixture') == 'fallback_message_API']
        disposed = [e for e in r.events if e.get('fixture') == 'fallback_message_disposal']
        assert len(fallback) == len(disposed) == int(allocation[1] == 0)
        cases.append(dict(name='diagnostic_allocation_or_send_failure_uses_loss_fallback',
            fixture_allocation_results=list(allocation), fixture_send_result=send,
            exact_queue_ownership_and_message_loss_semantics_unverified=True, events=r.events))

    report = dict(schema='d6700/native-photo-trace-clock-feasibility/1', result='passed',
        recorded_utc=datetime.now(timezone.utc).isoformat(), camera_access=False,
        cp_sha256=CP_SHA256, source_sha256={Path(__file__).name:hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        native_code_sha256={f'{s:#x}:{e-s:#x}':hashlib.sha256(raw[s-CP_BASE:e-CP_BASE]).hexdigest() for s,e in RANGES},
        case_count=len(cases), cases=cases,
        findings=[
            'bd16e0 tail-calls a Thumb-to-ARM veneer whose original literal targets5f976c. That original five-instruction clock function uses ISB, MRRC p15 c14 and a logical 64-bit shift right1 before return.',
            'The modeled counter read is the only clock fixture; original interworking and shift execute. This short function contains no allocation, queue call or wait loop.',
            'No counter frequency, hardware width, runtime availability in every intended context, elapsed-time scale or AP/CP clock bridge is verified.',
            'Original b6119a/b61266 builds a56-byte diagnostic message with a24-byte payload and the same raw timestamp. It calls allocation and message send APIs, with a second32-byte fallback path on failure.',
            'The bounded MLUT begin/end markers carry stage/status and sometimes callback pointer, without a per-photo task/owner/handle/generation field. They may supply timing anchors, not complete resource-event attribution.',
            'Do not add this diagnostic allocator/send path to new shutter/ISR hooks or call the existing markers a validated lossless nonblocking trace recorder.'],
        limitations=[
            'Counter values and OS allocation/send/fallback/disposal/memory operations are fixtures; no live CPU counter, diagnostic queue or captured native log is accessed.',
            'The caller marker arguments are taken from locally disassembled MLUT begin/end/error sites; this is not an exhaustive audit of all native logging.',
            'Diagnostic filter state, queue capacity, latency, ownership on failed send, retrieval API and timestamp conversion still require separate analysis.',
            'No usable trace/clock mechanism has been installed or approved, and no callback drain or image effect is proved.'],
        native_clock_short_path_identified=True, real_counter_frequency_verified=False,
        native_existing_log_retrieval_verified=False, new_event_observer_implemented=False,
        callback_drain_proved=False, photo_LUT_preview_verified=False,
        JPEG_pixels_verified=False, installable_candidate=False)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open('x',encoding='utf-8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
        f.write('\n')
    print(json.dumps(dict(result='passed',cases=len(cases),
        stock_clock_and_diagnostic_message_executed=True,camera_access=False)))


if __name__=='__main__':
    main()
