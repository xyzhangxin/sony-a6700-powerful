"""Built-in FL23 diagnostic export with optional fixed read-only camera probes.

Raw service preimages and replies stay in the local journal. The exported ZIP
contains only allowlisted records and filtered diagnostic lines. No automatic
reproduction, reset, reconnect, retry, or firmware/parameter/process writes.
"""
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from types import SimpleNamespace
import uuid
import zipfile

from . import __version__
from .diagnostic_records import collect, feature_rows


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_assets(folder):
    manifest = json.loads((folder / 'manifest.json').read_text())
    if manifest['schema'] != 1 or not manifest.get('expected_runtime'):
        raise ValueError('Unsupported diagnostic package')
    for name, digest in manifest['files'].items():
        p = Path(name)
        if p.is_absolute() or '..' in p.parts:
            raise ValueError('Invalid diagnostic asset path')
        path = folder / p
        if path.is_symlink() or sha(path.read_bytes()) != digest:
            raise ValueError('Diagnostic asset mismatch: ' + name)
    return manifest


def current_api():
    from .catalog import load_catalog
    from .device_profile import load_device_profile, inspect_sectors
    from .device_protocol import request, response
    from .fl23_package import Package, Session, inspect, service_files, save_bytes
    from .operations import device_lock, make_directory, save_json
    from .usb.backend import UsbBackend
    return SimpleNamespace(**locals())


def load_parser(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ValueError('Diagnostic parser unavailable: ' + path.name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def asset_root():
    return Path(__file__).resolve().parent / 'data' / 'diagnostics'


def save(path, data):
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def guard_history(state, own):
    """Never upload over an unresolved service-file transaction."""
    runs = state / 'runs'
    if not runs.exists():
        return
    for folder in runs.iterdir():
        if folder == own or folder.is_symlink() or not folder.is_dir():
            continue
        if not re.fullmatch(r'(?:looks|fl23|fl23-diagnostic)-[0-9a-f]{32}', folder.name):
            continue
        if not (folder / 'service-2.before').exists():
            continue
        try:
            path = folder / 'result.json'
            if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('Invalid result')
            result = json.loads(path.read_text())
        except (OSError, ValueError):
            raise ValueError('在先服务事务没有完整结果；请保留原操作记录，先完成收尾。')
        if result.get('service_restore_verified') is not True:
            raise ValueError('在先服务文件恢复未确认；未连接相机，请先完成收尾。')


def runtime_detail(raw, nonce, expected):
    text = raw.decode('ascii')
    if not text.startswith('A6700-DYNAMIC-RUNTIME-v1\n' + nonce + '\n'):
        raise ValueError('Stale runtime report')
    processes = re.findall(r'^PROCESS pid=(\d+) base=(0x[0-9a-f]+) mem_fd=(-?0x[0-9a-f]+)$', text, re.M)
    if len(processes) != 1:
        return dict(state='unavailable', reason='app-process-not-unique')
    pid, base, fd = processes[0]
    if fd.startswith('-'):
        return dict(state='unavailable', reason='process-memory-unreadable')
    reads = re.findall(r'^READ (fl23_[^ ]+) addr=(0x[0-9a-f]+) result=(-?0x[0-9a-f]+) data=([0-9a-f]*)$', text, re.M)
    tags = [row[0] for row in reads]
    if len(tags) != len(set(tags)) or set(tags) != {r['tag'] for r in expected}:
        raise ValueError('Incomplete or duplicate repair reads')
    observed = {tag: (int(at, 16), int(n, 16), bytes.fromhex(value)) for tag, at, n, value in reads}
    output = []
    for row in expected:
        at, n, value = observed[row['tag']]
        if at != int(base, 16) + row['offset']:
            raise ValueError('Runtime address binding differs')
        wanted = bytes.fromhex(row['expected_hex'])
        output.append(dict(tag=row['tag'], bytes=n, readable=n == len(wanted),
                           sha256=sha(value), matches_repair=n == len(wanted) and value == wanted))
    return dict(state='repair-loaded' if all(r['matches_repair'] for r in output) else 'repair-not-fully-loaded',
                process_id=int(pid), app_base=base, reads=output)


def relevant_log(raw):
    """Export diagnostic lines only. Full logs remain local for controlled review."""
    wanted = re.compile(r'assert|exception|panic|abort|segfault|watchdog|warmboot|reboot|BUG:|Call trace|backtrace|'
                        r'\bpc\s*[:=]|\blr\s*[:=]|\bsp\s*[:=]|\[<[0-9a-f]+>\]|REMOTE|chip crash|HANG', re.I)
    secret = re.compile(r'ssid|psk|password|passphrase|authorization|secret|token|ProductInfo|serial', re.I)
    output = []
    for line in raw.decode('utf-8', errors='replace').splitlines():
        if not wanted.search(line) or secret.search(line):
            continue
        line = ''.join(c for c in line if c.isprintable())[:1024]
        line = re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', '[MAC]', line)
        line = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP]', line)
        line = re.sub(r'(?:/[\w.+-]+){2,}', '[PATH]', line)
        output.append(line)
    return output[-160:]


def log_detail(raw, parsed):
    result = {k: v for k, v in parsed.items() if k != 'records'}
    result['records'] = parsed['records']
    result['relevant_lines'] = []
    for row in parsed['records']:
        if row['path'] not in ('/proc/klog', '/log/logdata.dat', '/log/logdata_cp.dat', '/log/logdata_core.dat'):
            continue
        # Validated record offsets are fixed in this exact current-layout parser.
        import struct
        offset = 44 + struct.unpack_from('<I', raw, 44 + 256 + row['index'] * 64 + 16)[0]
        result['relevant_lines'].extend(relevant_log(raw[offset:offset + row['bytes']]))
    result['relevant_lines'] = result['relevant_lines'][-160:]
    result['fault_attribution'] = 'unproven; logs may predate the reported event'
    return result


@contextlib.contextmanager
def service_scope(api, backend, run, report):
    manager = api.service_files(backend, run)
    invoke = manager.__enter__()
    try:
        yield invoke
    except BaseException:
        # A decoder failure still permits the existing context to restore its
        # files. Transport uncertainty deliberately prevents a blind restore.
        manager.__exit__(*sys.exc_info())
        report['service_restore_verified'] = not backend._fl23_transport_uncertain and not getattr(backend, 'cleanup_warning', None)
        raise
    else:
        manager.__exit__(None, None, None)
        report['service_restore_verified'] = not backend._fl23_transport_uncertain and not getattr(backend, 'cleanup_warning', None)


@contextlib.contextmanager
def authentication_deadline(seconds=120):
    """Bound only authentication, before any diagnostic payload is uploaded."""
    if threading.current_thread() is not threading.main_thread():
        # SIGALRM is main-thread-only. The guided watcher reconnects in a
        # worker thread; USB authentication already has its own protocol
        # timeout, so do not turn that path into a Python signal error.
        yield
        return
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError('Another authentication timer is already active')
    def expired(*unused):
        raise TimeoutError('USB接管认证等待超时，未上传采集载荷。')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextlib.contextmanager
def backend_scope(api, profile, progress):
    manager = api.UsbBackend(profile, progress)
    with authentication_deadline():
        backend = manager.__enter__()
    try:
        yield backend
    except BaseException:
        manager.__exit__(*sys.exc_info())
        raise
    else:
        manager.__exit__(None, None, None)


def read_compatible_base(api, profile, backend, exchange):
    nonce = uuid.uuid4().bytes
    config = api.request(profile, nonce)
    base = api.response(exchange('base', backend.assets['reader'], config), nonce, len(profile.entries))
    if base['status']:
        raise ValueError('Base profile read failed')
    detail, _ = api.inspect_sectors(profile, base['sectors'])
    if not detail['compatible']:
        raise ValueError('本机固件与2.00固定适配不符，未继续采集。')
    return feature_rows(detail['features'])


def start_operation_log(state_dir=None, operation_id=None, progress=print, backend=None):
    """Start the recorder using a new or caller-owned authenticated session."""
    state = Path(state_dir) if state_dir is not None else Path.home() / 'Library/Application Support/a6700-unlock'
    assets = asset_root()
    load_assets(assets)
    api = current_api()
    operation = operation_id or str(uuid.uuid4())
    run = state / 'runs' / ('fl23-diagnostic-' + uuid.uuid4().hex)
    api.make_directory(run)
    nonce = uuid.uuid4().bytes
    module = load_parser(assets / 'diagnostic_operation_control.py')
    report = dict(status='starting', operation_id=operation, device_access=False,
                  firmware_writes=False, parameter_writes=False, process_memory_writes=False,
                  temporary_service_files=True, service_restore_verified=False)
    device_context = None
    lock_held = False
    recorder = None
    try:
        if backend is None:
            device_context = api.device_lock(state)
            device_context.__enter__()
            lock_held = True
        guard_history(state, run)
        profile = api.load_device_profile(api.load_catalog())
        progress('正在启动机内连续记录；完成后相机恢复可操作页面…')
        manager = backend_scope(api, profile, progress) if backend is None else contextlib.nullcontext(backend)
        with manager as backend:
            report['device_access'] = True
            with service_scope(api, backend, run, report) as invoke:
                report['base_features'] = read_compatible_base(api, profile, backend,
                    lambda label, binary, config: invoke(binary, config))
                config = module.request(nonce, 0)
                save(run / 'operation-start.intent.json', dict(
                    payload_sha256=sha((assets / 'operation_control.elf').read_bytes()),
                    request_sha256=sha(config)))
                api.save_bytes(run / 'operation-start.request', config)
                raw = invoke((assets / 'operation_control.elf').read_bytes(), config)
                api.save_bytes(run / 'operation-start.reply', raw)
                detail = module.parse(raw, config)
                if detail['native_status'] or detail['status'] != 'recording':
                    if detail.get('history'):
                        history = module.session_history(detail['history'], nonce)
                        report['operation_log'] = dict(
                            source='/tmp/jiritsu/history.txt', lines=history['lines'],
                            ready_observed=history['ready_observed'],
                            ready_selection=history['ready_selection'],
                            sampling_errors=history['sampling_errors'])
                        if history['sampling_errors']:
                            raise ValueError('创意外观首次采样失败，返回码 %d；未开始复现。' %
                                             history['sampling_errors'][-1]['code'])
                    raise ValueError('机内连续记录未能启动。')
                recorder = dict(operation_id=operation, nonce=nonce.hex(),
                                mapping=detail['mapping'], mapping_bytes=detail['mapping_bytes'],
                                boot_id=detail['boot_id'], run=run.name,
                                sampling_ready=bool(detail.get('sampling_ready')),
                                sampling_degraded=bool(detail.get('sampling_degraded')),
                                sampling_pending=bool(detail.get('sampling_pending')),
                                sampling_error=detail.get('sampling_error'))
                report.update(status='recording', mapping=detail['mapping'],
                              mapping_bytes=detail['mapping_bytes'], boot_id=detail['boot_id'],
                              sampling_ready=bool(detail.get('sampling_ready')),
                              sampling_degraded=bool(detail.get('sampling_degraded')),
                              sampling_pending=bool(detail.get('sampling_pending')),
                              sampling_error=detail.get('sampling_error'))
        if not report['service_restore_verified']:
            raise ValueError('采集服务恢复未确认；请不要操作相机并保留本机记录。')
        if recorder.get('sampling_pending'):
            progress('日志服务已启动；创意外观探针正在初始化，可继续操作。')
        elif recorder.get('sampling_degraded'):
            progress('日志服务已启动；创意外观采样失败（ERR=%s），仍可继续操作，错误已保存。' %
                     recorder.get('sampling_error'))
        else:
            progress('首次创意外观采样已确认；请操作相机，后续选择变化会立即保存。')
        return recorder
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status='incomplete', error_type=type(exc).__name__)
        raise
    finally:
        report['cleanup_status'] = getattr(backend, 'cleanup_status', None)
        report['service_recovery_required'] = report['device_access'] and not report['service_restore_verified']
        try:
            save(run / 'result.json', report)
        finally:
            if lock_held:
                device_context.__exit__(None, None, None)


def load_unfinished_recorder(state_dir=None):
    """Recover the newest started-but-not-retrieved recorder after an app restart."""
    state = Path(state_dir) if state_dir is not None else Path.home() / 'Library/Application Support/a6700-unlock'
    runs = state / 'runs'
    if not runs.exists():
        return None
    candidates = []
    for run in runs.glob('fl23-diagnostic-*'):
        result_path = run / 'result.json'
        request_path = run / 'operation-start.request'
        if not result_path.is_file() or not request_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text())
            request = request_path.read_bytes()
        except (OSError, ValueError):
            continue
        if result.get('status') != 'recording' or len(request) != 40:
            continue
        candidates.append((result_path.stat().st_mtime, run, result, request[8:24]))
    if not candidates:
        return None
    # There is only one shared history file. Every newer start clears or
    # replaces older sessions, so falling back to an older unattempted START
    # after the latest session was already stopped is always stale metadata.
    _, run, result, nonce = max(candidates)
    attempted = False
    for request_path in runs.glob('fl23-diagnostic-*/operation-stop.request'):
        try:
            if request_path.read_bytes()[8:24] == nonce:
                attempted = True
                break
        except OSError:
            continue
    if attempted:
        return None
    return dict(operation_id=result['operation_id'], nonce=nonce.hex(),
                mapping=result['mapping'], mapping_bytes=result['mapping_bytes'],
                boot_id=result['boot_id'], run=run.name,
                events=[dict(step_id='recovery_detected', phase='connect', status='waiting',
                             source='core', elapsed_ms=0)])


def capture_camera(api, assets, manifest, state, run, progress=print, recorder=None, backend=None):
    report = dict(status='starting', device_access=False, firmware_writes=False,
                  parameter_writes=False, process_memory_writes=False, temporary_service_files=True,
                  service_restore_verified=False, probes={})
    device_context = None
    lock_held = False
    try:
        if backend is None:
            device_context = api.device_lock(state)
            device_context.__enter__()
            lock_held = True
        guard_history(state, run)
        profile = api.load_device_profile(api.load_catalog())
        manager = backend_scope(api, profile, progress) if backend is None else contextlib.nullcontext(backend)
        with manager as backend:
            report['device_access'] = True
            with service_scope(api, backend, run, report) as invoke:
                def exchange(label, binary, config):
                    save(run / (label + '.intent.json'), dict(payload_sha256=sha(binary), request_sha256=sha(config)))
                    api.save_bytes(run / (label + '.request'), config)
                    if label == 'operation-stop' and isinstance(recorder, dict):
                        # The request is durable before USB invocation. The
                        # caller uses this local-only marker to distinguish a
                        # reconnect-safe pre-STOP failure from an uncertain
                        # STOP result that must never be replayed blindly.
                        recorder['stop_attempted'] = True
                    raw = invoke(binary, config)  # Never retried, including a lost GET.
                    api.save_bytes(run / (label + '.reply'), raw)
                    return raw
                if recorder:
                    module = load_parser(assets / 'diagnostic_operation_control.py')
                    nonce = bytes.fromhex(recorder['nonce'])
                    config = module.request(nonce, 1, int(recorder['mapping'], 16),
                                            recorder['mapping_bytes'], recorder['boot_id'].encode('ascii'))
                    raw = exchange('operation-stop', (assets / 'operation_control.elf').read_bytes(), config)
                    detail = module.parse(raw, config)
                    history = module.session_history(detail['history'], nonce)
                    if detail['status'] == 'stopped' and not history['stop_observed']:
                        raise ValueError('机内记录线程已停止，但缺少本次 STOP 记录。')
                    report['operation_log'] = dict(
                        source='/tmp/jiritsu/history.txt', write_mode='append-fsync-per-event',
                        session_found=history['session_found'], start_observed=history['start_observed'],
                        stop_observed=history['stop_observed'], records=history['records'],
                        ready_observed=history.get('ready_observed'), ready_selection=history.get('ready_selection'),
                        sampling_errors=history.get('sampling_errors', []),
                        ready_state=history.get('ready_state'),
                        lines=history['lines'], recovered_after_reboot=detail['recovered_after_reboot'],
                        recorder_stopped=detail['status'] == 'stopped', native_status=detail['native_status'],
                        history_bytes=detail['history_bytes'], history_sha256=detail['history_sha256'])
                    progress('已取回本次机内记录：%d 条选择变化%s。' % (
                        len(history['records']), '（含重启前未终止现场）' if not history['stop_observed'] else ''))
                    if detail['native_status']:
                        raise ValueError('机内记录停止或日志读取未完整完成。')
                    cleanup_config = module.request(nonce, 2, int(recorder['mapping'], 16),
                                                     recorder['mapping_bytes'], recorder['boot_id'].encode('ascii'))
                    raw = exchange('operation-cleanup', (assets / 'operation_control.elf').read_bytes(), cleanup_config)
                    cleanup = module.parse(raw, cleanup_config)
                    if cleanup['status'] != 'cleaned':
                        raise ValueError('相机内操作日志删除未完成。')
                    report['operation_log']['history_cleanup_verified'] = True
                report['base_features'] = read_compatible_base(api, profile, backend, exchange)
                # Logs first: preserve crash evidence before inspecting application state.
                for name in ('boot_log', 'cp_exception'):
                    module = load_parser(assets / ('diagnostic_' + name + '.py'))
                    config = module.request()
                    raw = exchange(name, (assets / (name + '.elf')).read_bytes(), config)
                    parsed = module.parse(raw, config)
                    report['probes'][name] = log_detail(raw, parsed) if name == 'boot_log' else parsed
                package = api.Package()
                session = api.Session(package, backend, invoke, run)
                row, details, _, _, file, selection = api.inspect(session)
                report['installed_features'] = feature_rows([row, dict(id='creative_look_names', state=details['names_state'])])
                report['installed_carrier'] = {k: file[k] for k in ('present', 'flags', 'revision') if k in file}
                report['main_selection'] = selection
                observed = []
                for number in (1, 2):
                    nonce = 'fl23-diagnostic-' + uuid.uuid4().hex
                    raw = exchange('runtime-' + str(number), (assets / 'runtime.elf').read_bytes(), nonce.encode())
                    observed.append(runtime_detail(raw, nonce, manifest['expected_runtime']))
                report['probes']['runtime'] = dict(reads=observed, stable=observed[0] == observed[1])
        report['status'] = 'collected'
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status='incomplete', error_type=type(exc).__name__)
        # Keep the actionable text for the local result/run only. It is removed
        # before report.json is written so arbitrary exception text is not exported.
        report['_error'] = (str(exc).strip() or type(exc).__name__)[:500]
        progress('采集未完成：' + str(exc))
    finally:
        report['cleanup_status'] = getattr(backend, 'cleanup_status', None)
        report['service_recovery_required'] = report['device_access'] and not report['service_restore_verified']
        try:
            save(run / 'result.json', report)
        finally:
            if lock_held:
                device_context.__exit__(None, None, None)
    return report


def export_report(path, report):
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED) as z:
        z.writestr('report.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        operation = report.get('camera', {}).get('operation_log')
        if operation:
            z.writestr('operation-history.txt', '\n'.join(operation['lines']) + '\n')
        z.writestr('说明.txt', '诊断采集结果；机内记录在每次变化时执行 fsync，重启后仅取回已保存现场。日志年代与故障归因仍需分析。原始服务文件、固件和完整系统日志没有打包。\n')


def export(camera=False, state_dir=None, output_dir=None, progress=print,
           operation_id=None, mode='direct', guided_events=None, recorder=None, backend=None):
    state = Path(state_dir) if state_dir is not None else Path.home() / 'Library/Application Support/a6700-unlock'
    output = Path(output_dir) if output_dir is not None else Path.home() / 'Downloads'
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    operation = operation_id or str(uuid.uuid4())
    events = list(guided_events or [])
    try:
        local_install = collect(state)
    except (OSError, ValueError) as exc:
        local_install = dict(records=[], status='unavailable', error_type=type(exc).__name__, camera_access=False)
    elapsed = lambda: int((time.monotonic() - started) * 1000)
    events.append(dict(step_id='local_records', phase='inspect', status='passed',
                       source='core', elapsed_ms=elapsed()))
    report = dict(schema=1, kind='fl23-error-report', collected_at=datetime.now(timezone.utc).isoformat(),
                  tool_version='built-in-1', application_version=__version__, operation_id=operation,
                  collection_mode=mode, local_install=local_install, automatic_reproduction=False)
    camera_error = None
    recorder_stop_attempted = False
    if camera:
        assets = asset_root()
        manifest = load_assets(assets)
        api = current_api()
        run = state / 'runs' / ('fl23-diagnostic-' + uuid.uuid4().hex)
        api.make_directory(run)
        camera_report = capture_camera(api, assets, manifest, state, run, progress,
                                       recorder=recorder, backend=backend)
        recorder_stop_attempted = (recorder is not None and
                                   (run / 'operation-stop.request').is_file())
        camera_error = camera_report.pop('_error', None)
        report['camera'] = camera_report
    suffix = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    path = output / ('FL23-故障报告-' + suffix + '.zip')
    export_report(path, report)
    camera_report = report.get('camera', {})
    status = camera_report.get('status', 'collected')
    if camera:
        events.append(dict(step_id='camera_collect', phase='inspect',
                           status='passed' if status == 'collected' else 'failed',
                           source='device', elapsed_ms=elapsed()))
        events.append(dict(step_id='service_restore', phase='exit',
                           status='passed' if camera_report.get('service_restore_verified') else 'unknown',
                           source='device', elapsed_ms=elapsed()))
    report['events'] = events
    return dict(status=status, path=str(path), camera=bool(camera),
                service_recovery_required=bool(camera_report.get('service_restore_verified') is False),
                error_type=camera_report.get('error_type'), operation_id=operation,
                error=camera_error,
                recorder_stop_attempted=recorder_stop_attempted,
                mode=mode, events=events, record_count=len(local_install.get('records', [])))

