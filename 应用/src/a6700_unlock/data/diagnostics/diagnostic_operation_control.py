# Fixed parser frozen from fl23_operation_control_protocol.py
import hashlib
import secrets
import struct

REQUEST = struct.Struct('<4sI16sIQI')
RESPONSE = struct.Struct('<4sI16sIIQI')
SIZE = 131072
HISTORY_AT = 1024
HISTORY_CAPACITY = SIZE - HISTORY_AT
BOOT_ID_AT = 512
MAGIC = 0x3145424f52503141
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def request(nonce=None, action=0, mapping=0, mapping_bytes=0, boot_id=None):
    if action not in (0, 1, 2):
        raise ValueError('Operation recorder action differs')
    nonce = secrets.token_bytes(16) if nonce is None else nonce
    base = REQUEST.pack(b'A67P', 1, nonce, 46, action, SIZE)
    if not action:
        return base
    if (not isinstance(boot_id, bytes) or len(boot_id) != 36 or not mapping
            or mapping & 4095 or not mapping_bytes or mapping_bytes & 4095):
        raise ValueError('Operation recorder stop identity differs')
    return base + struct.pack('<QQ36sI', mapping, mapping_bytes, boot_id, 0)


def _signed(value):
    return value if value < 1 << 63 else value - (1 << 64)


def parse(raw, config):
    if len(config) not in (40, 96) or len(raw) != 44 + SIZE:
        raise ValueError('Operation recorder frame extent differs')
    action = REQUEST.unpack(config[:40])[4]
    if len(config) != (96 if action else 40):
        raise ValueError('Operation recorder action framing differs')
    mapping, mapping_bytes, boot_id = (struct.unpack_from('<QQ36s', config, 40)
                                       if action else (0, 0, None))
    if config != request(config[8:24], action, mapping, mapping_bytes, boot_id):
        raise ValueError('Operation recorder request differs')
    header = RESPONSE.unpack(raw[:44])
    native_status = header[3]
    if header != (b'A67B', 1, config[8:24], native_status, 46, action, SIZE):
        raise ValueError('Operation recorder response stale or foreign')
    data = raw[44:]
    w = struct.unpack_from('<32Q', data)
    if (w[0] != 1 or not 0 < w[1] < 1 << 31 or not 0 < w[2] < 1 << 31
            or w[9] != action or w[13] != MAGIC or any(w[16:])):
        raise ValueError('Operation recorder wrapper identity differs')
    if any(value not in (0, 1) for value in (w[10], w[12], w[15])):
        raise ValueError('Operation recorder lifecycle flag differs')
    if w[11] > HISTORY_CAPACITY or w[14] > 3:
        raise ValueError('Operation history extent differs')
    history = data[HISTORY_AT:HISTORY_AT + w[11]]
    if any(data[HISTORY_AT + w[11]:]):
        raise ValueError('Operation history padding differs')
    result = dict(native_status=native_status, action={0:'start',1:'stop',2:'cleanup'}[action],
                  process_id=w[1], service_tid=w[2], load_rc=_signed(w[3]),
                  start_rc=_signed(w[4]), stop_rc=_signed(w[5]), unmap_rc=_signed(w[6]),
                  mapping=hex(w[7]), mapping_bytes=w[8], report_present=bool(w[10]),
                  history_bytes=w[11], history_sha256=sha(history), history=history,
                  history_status=w[14], safe_to_unload=bool(w[12]),
                  mapping_released=bool(w[12] and not w[6] and w[7]),
                  recovered_after_reboot=bool(w[15]), application_writes=False,
                  parameter_writes=False, firmware_writes=False)
    if not action:
        boot_id = data[BOOT_ID_AT:BOOT_ID_AT + 36]
        if native_status:
            if w[11] and w[14]:
                raise ValueError('Failed recorder start returned unreadable sampling history')
            result['status'] = 'error'
            result['sampling_ready'] = False
            if (boot_id[8:9] == b'-' and boot_id[13:14] == b'-'
                    and boot_id[18:19] == b'-' and boot_id[23:24] == b'-'):
                result['boot_id'] = boot_id.decode('ascii')
            return result
        if (native_status or w[3] or w[4] or w[5] or w[6] or w[11]
                or w[12] or w[14] or w[15] or (not w[10] and any(data[256:BOOT_ID_AT]))
                or any(data[BOOT_ID_AT + 36:])):
            raise ValueError('Operation recorder start did not retain a clean mapping')
        if (boot_id[8:9] != b'-' or boot_id[13:14] != b'-'
                or boot_id[18:19] != b'-' or boot_id[23:24] != b'-'):
            raise ValueError('Operation recorder boot identity differs')
        result['boot_id'] = boot_id.decode('ascii')
        result['status'] = 'recording'
        result['sampling_ready'] = True
        result['sampling_degraded'] = False
        if w[10]:
            report = struct.unpack_from('<21Q', data, 256)
            if (report[0] != 1 or report[2] or report[3] != w[1] or report[4] != w[2]
                    or data[256 + 168:256 + 184] != config[8:24]):
                raise ValueError('Started recorder report identity differs')
            reserved = struct.unpack_from('<9Q', data, 256 + 184)
            if reserved[1] not in (0, 1, 2):
                raise ValueError('Started recorder sampling state differs')
            result['sampling_ready'] = reserved[1] == 1
            result['sampling_degraded'] = reserved[1] == 2
            result['sampling_pending'] = reserved[1] == 0
            if reserved[1] == 2:
                result['sampling_error'] = _signed(reserved[2])
        return result
    if action == 2:
        if (native_status or w[3] or w[4] or w[5] or w[6] or w[7] or w[8]
                or w[10] or w[11] or w[12] or w[15] or w[14] > 3
                or any(data[256:HISTORY_AT])):
            raise ValueError('Operation history cleanup did not complete')
        result['cleanup_status'] = w[14]
        result['status'] = 'cleaned'
        return result
    if any(data[BOOT_ID_AT:HISTORY_AT]):
        raise ValueError('Operation recorder stop returned a stale boot identity')
    if w[15] and (w[10] or w[5] != (-22 & ((1 << 64) - 1)) or w[7] or w[8]):
        raise ValueError('Reboot recovery identity differs')
    if not w[15] and not w[10]:
        raise ValueError('Stopped recorder has neither report nor recovery marker')
    if w[10]:
        report = struct.unpack_from('<21Q', data, 256)
        if (report[0] != 1 or report[3] != w[1] or report[4] != w[2]
                or data[256 + 168:256 + 184] != config[8:24]):
            raise ValueError('Operation recorder report identity differs')
        result.update(thread_state=report[1], thread_error=report[2],
                      joined=bool(report[16]), worker_done=bool(report[18]))
        if w[12] != report[16]:
            raise ValueError('Recorder mapping lifetime differs from join state')
    elif any(data[256:BOOT_ID_AT]):
        raise ValueError('Reboot recovery contains a stale thread report')
    if w[11] and w[14]:
        raise ValueError('Failed history read returned bytes')
    if not native_status:
        if w[3] or w[6] or w[14] or not w[12] or (not w[15] and not w[7]):
            raise ValueError('Successful recorder stop has incomplete cleanup')
        if not w[15] and (w[5] or not result.get('joined')):
            raise ValueError('Successful recorder stop did not join')
    result['status'] = 'recovered' if w[15] else 'stopped'
    return result


def session_history(raw, nonce):
    """Return only this recording session, including an unterminated crash tail."""
    if not isinstance(nonce, bytes) or len(nonce) != 16:
        raise ValueError('Operation recording identity differs')
    marker = b'START ' + nonce.hex().encode() + b'\n'
    stop = b'STOP ' + nonce.hex().encode() + b'\n'
    try:
        text = raw.decode('ascii')
    except UnicodeDecodeError as exc:
        raise ValueError('Operation history is not ASCII') from exc
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.encode() == marker]
    if len(starts) != 1 or not text.endswith('\n'):
        raise ValueError('Current operation history start is missing or ambiguous')
    selected = lines[starts[0]:]
    records = []
    errors = []
    ready_selection = None
    ready_state = None
    stopped = False
    def parse_state(parts):
        if parts[1] not in ('READY', 'STATE') or len(parts) != 6:
            raise ValueError('Operation history state framing differs')
        fields = {}
        for token in parts[2:]:
            key, separator, value = token.partition('=')
            if not separator or key in fields:
                raise ValueError('Operation history state field differs')
            fields[key] = value
        if set(fields) != {'REC', 'LIVE', 'SEL', 'MR'}:
            raise ValueError('Operation history state fields differ')
        try:
            record = int(fields['REC'])
            live = int(fields['LIVE'])
            saved = int(fields['SEL'])
        except ValueError as exc:
            raise ValueError('Operation history state number differs') from exc
        if any(not 0 <= value <= 0xffffffff for value in (record, live, saved)):
            raise ValueError('Operation history state value out of range')
        mr = fields['MR']
        if len(mr) != 26 or any(character not in '0123456789abcdef' for character in mr):
            raise ValueError('Operation history MR value differs')
        return dict(elapsed_seconds=None, record=record, live=live, saved=saved, mr=mr)
    for i, line in enumerate(selected):
        if line.encode() == stop:
            if i != len(selected) - 1:
                raise ValueError('Operation history continues after STOP')
            stopped = True
            continue
        if i == 0:
            continue
        parts = line.rstrip('\n').split(' ')
        if not parts or not parts[0].startswith('T'):
            raise ValueError('Operation history contains an unknown line')
        try:
            elapsed = int(parts[0][1:])
        except ValueError as exc:
            raise ValueError('Operation history number differs') from exc
        if elapsed < 0 or elapsed >= 1 << 31:
            raise ValueError('Operation history value out of range')
        if parts[1] in ('READY', 'STATE') and len(parts) == 6:
            state = parse_state(parts)
            state['elapsed_seconds'] = elapsed
            if parts[1] == 'READY':
                if ready_state is not None or records or errors:
                    raise ValueError('Operation history READY is duplicated or out of order')
                ready_state = state
                ready_selection = state['record']
            elif ready_state is None:
                raise ValueError('Operation history STATE appears before READY')
            records.append(dict(state, selection=state['record']))
        elif len(parts) == 3 and parts[1] == 'READY' and parts[2].startswith('SEL='):
            if ready_selection is not None or records or errors:
                raise ValueError('Operation history READY is duplicated or out of order')
            try:
                selection = int(parts[2][4:])
            except ValueError as exc:
                raise ValueError('Operation history READY value differs') from exc
            if not 0 <= selection <= 0xffffffff:
                raise ValueError('Operation history value out of range')
            ready_selection = selection
            records.append(dict(elapsed_seconds=elapsed, selection=selection, record=selection,
                                live=selection, saved=selection, mr=None))
        elif len(parts) == 2 and parts[1].startswith('SEL='):
            try:
                selection = int(parts[1][4:])
            except ValueError as exc:
                raise ValueError('Operation history number differs') from exc
            if not 0 <= selection <= 0xffffffff:
                raise ValueError('Operation history value out of range')
            records.append(dict(elapsed_seconds=elapsed, selection=selection, record=None,
                                live=None, saved=selection, mr=None))
        elif len(parts) == 2 and parts[1].startswith('ERR='):
            try:
                code = int(parts[1][4:])
            except ValueError as exc:
                raise ValueError('Operation history error code differs') from exc
            if not -(1 << 31) <= code < 1 << 31:
                raise ValueError('Operation history error code out of range')
            errors.append(dict(elapsed_seconds=elapsed, code=code))
        else:
            raise ValueError('Operation history contains an unknown line')
    return dict(session_found=True, start_observed=True, stop_observed=stopped,
                ready_observed=ready_selection is not None, ready_selection=ready_selection,
                ready_state=ready_state, sampling_errors=errors, records=records,
                lines=[line.rstrip('\n') for line in selected])
