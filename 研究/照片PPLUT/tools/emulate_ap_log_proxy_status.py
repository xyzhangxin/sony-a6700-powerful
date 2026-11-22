"""Replay the A6700 AP log proxy with the recorded CP query and native callbacks."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import struct
from unittest.mock import patch

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from unicorn import UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0

import build_cp_selector_status_canary as candidate
import emulate_cp_native_log_protocol as stock
from cp_arm_thumb_fixture_stores import ArmThumbFixtureUc


MODULE_SHA = 'f1db304af8fd7c6a208880cc40edd11218a94f02d3b10ddd81aea9a8aa103852'
TEXT = 0x100000
RODATA = 0x120000
BSS = 0x130000
FIXTURES = 0x200000
AP_MESSAGE = 0x300000
CP_MESSAGE = 0x300100
STACK = 0x400000
STACK_TOP = STACK + 0x10000
RETURN = FIXTURES + 0x800
CALLBACK = 0x5570
CP_KEY = 0x908F084B


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_proxy(raw):
    if sha(raw) != MODULE_SHA:
        raise ValueError('Unexpected A6700 2.00 log module')
    elf = ELFFile(io.BytesIO(raw))
    text = bytearray(elf.get_section_by_name('.text').data())
    symbols = elf.get_section_by_name('.symtab')
    relocations = elf.get_section_by_name('.rela.text')
    fixture_calls = {}
    links = {}
    for row in relocations.iter_relocations():
        at = row['r_offset']
        if at not in (0x5A24, 0x5A58) and not CALLBACK <= at < 0x5890:
            continue
        symbol = symbols.get_symbol(row['r_info_sym'])
        index = symbol['st_shndx']
        section = elf.get_section(index).name if isinstance(index, int) else None
        value = symbol['st_value'] + row['r_addend']
        links[at] = (symbol.name or section, value)
        if not CALLBACK <= at < 0x5890:
            continue
        if row['r_info_type'] == 283:
            target = FIXTURES + 4 * len(fixture_calls)
            fixture_calls[target] = symbol.name
            distance = target - TEXT - at
            assert distance % 4 == 0 and -(1 << 27) <= distance < (1 << 27)
            struct.pack_into('<I', text, at, 0x94000000 | ((distance >> 2) & 0x3FFFFFF))
        elif row['r_info_type'] == 257:
            base = {'.text': TEXT, '.rodata.str1.8': RODATA, '.bss': BSS}[section]
            struct.pack_into('<Q', text, at, base + value)
        else:
            raise ValueError(f'Unexpected proxy relocation at {at:#x}')
    assert links[0x5A24] == ('osal_reg_sync_msg_queue_cb', 0)
    assert links[0x5A58] == ('.text', CALLBACK)
    assert links[0x560C] == ('osal_snd_sync_msg', 0)
    assert links[0x565C] == ('ulogio_read_backup', 0x1058)
    return bytes(text), elf.get_section_by_name('.rodata.str1.8').data(), fixture_calls


def replay(proxy, request, remote_reply=None, *, allocate=True, send=True):
    text, rodata, fixture_calls = proxy
    vm = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for address, data in ((TEXT, text), (RODATA, rodata)):
        vm.mem_map(address, (len(data) + 0xFFF) & ~0xFFF)
        vm.mem_write(address, data)
    for address, size in ((BSS, 0x2000), (FIXTURES, 0x1000), (AP_MESSAGE, 0x1000), (STACK, 0x10000)):
        vm.mem_map(address, size)
    vm.mem_write(AP_MESSAGE, request)
    vm.mem_write(CP_MESSAGE, bytes([0xA7]) * 24)
    calls = []
    forwarded = None

    def dispatch(machine, at, size, user):
        nonlocal forwarded
        name = fixture_calls.get(at)
        if name is None:
            if not TEXT + CALLBACK <= at < TEXT + 0x5838:
                raise AssertionError(f'Unexpected proxy instruction at {at:#x}')
            return
        args = [machine.reg_read(UC_ARM64_REG_X0 + index) for index in range(4)]
        calls.append(name)
        result = 0
        if name == 'osal_valloc_msg_wait':
            assert args[0] == CP_KEY and args[2:] == [24, 1]
            if allocate:
                machine.mem_write(args[1], struct.pack('<Q', CP_MESSAGE))
            else:
                result = -12
        elif name == 'osal_snd_sync_msg':
            assert args[:2] == [CP_KEY, CP_MESSAGE]
            forwarded = bytes(machine.mem_read(CP_MESSAGE, 24))
            if send:
                remote = remote_reply(forwarded)
                assert len(remote) == 24
                machine.mem_write(CP_MESSAGE, remote)
            else:
                result = -5
        elif name == 'osal_free_msg':
            assert args[:2] == [CP_KEY, CP_MESSAGE]
        elif name == 'ulogio_read_backup':
            assert args[0] == 0xFA006E and args[2] == 8
            result = -1
        elif name != 'printk':
            raise AssertionError(f'Unexpected proxy call: {name}')
        machine.reg_write(UC_ARM64_REG_X0, result & 0xFFFFFFFFFFFFFFFF)
        machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_LR))

    vm.hook_add(UC_HOOK_CODE, dispatch)
    vm.reg_write(UC_ARM64_REG_SP, STACK_TOP)
    vm.reg_write(UC_ARM64_REG_LR, RETURN)
    vm.reg_write(UC_ARM64_REG_X0, AP_MESSAGE)
    vm.emu_start(TEXT + CALLBACK, RETURN, timeout=500000, count=3000)
    assert vm.reg_read(UC_ARM64_REG_PC) == RETURN and vm.reg_read(UC_ARM64_REG_SP) == STACK_TOP
    return bytes(vm.mem_read(AP_MESSAGE, 24)), forwarded, calls


def cp_reply(image, request):
    with patch.object(stock, 'Uc', ArmThumbFixtureUc):
        machine = stock.CP(image)
    machine.u.mem_write(stock.MSG, request)
    machine.call(0x55A664, stock.MSG)
    assert not machine.events
    return bytes(machine.u.mem_read(stock.MSG, 24))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cp', type=Path, required=True)
    parser.add_argument('--ap-proxy', type=Path, required=True, help='Locally extracted osal_ulogio.ko')
    parser.add_argument('--canary', type=Path, required=True, help='Build directory from the selector/status canary')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Use a new output file')
    image = args.cp.read_bytes()
    assert sha(image) == stock.CP_SHA
    canary = candidate.load(args.canary)
    offset = 0x55A664 - stock.CP_BASE
    patched = image[:offset] + canary + image[offset + len(canary):]
    proxy = load_proxy(args.ap_proxy.read_bytes())
    packet = b'\x43' + bytes(23)
    expected_proxy_reply = b'\x43\x80' + bytes(22)
    cases = []

    for label, program, expected in (('original', image, 0x80), ('candidate', patched, 0x5A)):
        def remote(query):
            response = cp_reply(program, query)
            assert response[1] == expected and response[8:20] == bytes(12)
            return response

        out, wire, calls = replay(proxy, packet, remote)
        assert out == expected_proxy_reply
        assert wire == b'\x43\0' + bytes([0xA7]) * 6 + bytes(12) + bytes([0xA7]) * 4
        assert calls == ['osal_valloc_msg_wait', 'osal_snd_sync_msg', 'printk', 'osal_free_msg', 'printk']
        cases.append(dict(case=label, CP_status=f'0x{expected:02x}', AP_response=out.hex(),
                          forwarded_request=wire.hex(), emitted_calls=calls))

    noisy = packet[:2] + bytes(range(1, 7)) + packet[8:20] + b'echo'
    out, wire, _ = replay(proxy, noisy, lambda req: cp_reply(patched, req))
    assert wire[2:8] == bytes([0xA7]) * 6 and wire[20:] == bytes([0xA7]) * 4
    assert out[:2] == b'\x43\x80' and out[2:8] == noisy[2:8] and out[20:] == noisy[20:]
    cases.append(dict(case='caller_padding_stays_local', AP_response=out.hex(), forwarded_request=wire.hex()))

    for label, kwargs in (('allocation_failure', {'allocate': False}), ('send_failure', {'send': False})):
        out, _, calls = replay(proxy, packet, **kwargs)
        assert out == expected_proxy_reply
        assert ('osal_free_msg' in calls) == (label == 'send_failure')
        cases.append(dict(case=label, AP_response=out.hex(), emitted_calls=calls))

    out, _, calls = replay(proxy, packet, lambda req: req[:1] + b'\x01' + req[2:])
    assert out[:2] == b'\x43\x81' and 'ulogio_read_backup' in calls
    cases.append(dict(case='status_one_reaches_original_backup_branch', AP_response=out.hex(), emitted_calls=calls))

    report = dict(schema='d6700/AP-log-proxy-status-replay/1', result='passed',
                  recorded_utc=datetime.now(timezone.utc).isoformat(), camera_access=False,
                  source_sha256=sha(Path(__file__).read_bytes()), AP_module_sha256=MODULE_SHA,
                  original_CP_sha256=sha(image), CP_candidate_sha256=sha(canary),
                  selector_fixture=hex(packet[0]), AP_proxy_key='0x808f084a',
                  AP_callback_text_offset='0x5570', direct_CP_key='0x908f084b',
                  observed_reply_reproduced_by_both_original_and_candidate=True,
                  transport_and_external_services_are_fixtures=True,
                  live_candidate_execution_verified=False, case_count=len(cases), cases=cases)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(result='passed', case_count=len(cases), reproduced_AP_response=expected_proxy_reply.hex())))


if __name__ == '__main__':
    main()
