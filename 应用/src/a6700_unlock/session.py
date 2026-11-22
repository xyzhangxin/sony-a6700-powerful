"""Bounded, bench-owned USB sessions over the existing fixed transaction core.

PersistentUsbSession is the worker's device owner. CameraMscSession is an IPC
client; it never acquires a second lock, authenticates, or retries a request.
No arbitrary payload, partition, profile, address or raw writer is accepted.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import secrets
import socket
import struct
import tempfile
import threading
import time
import uuid

from . import operations
from .catalog import load_catalog
from .device_access import UsbDeviceAccess
from .device_profile import load_device_profile
from .device_protocol import DeviceError

SCHEMA = "a6700.msc-session/v1"
MAX_FRAME = 1024 * 1024
TERMINAL = {"completed", "no-op", "settings-prepared", "rolled-back", "rejected", "blocked"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def sector_digest(sectors):
    return hashlib.sha256(b"".join(sectors)).hexdigest()


def pending_session_write(directory):
    """Return the audit blocker and whether native execution is unconfirmed.

    Used by both session startup and the power-cycle guard, under the shared
    device lock. No device access, record mutation or recovery is performed.
    """
    reason, unconfirmed = None, False
    for intent_path in (Path(directory) / "msc-sessions").glob("*/requests/*/intent.json"):
        intent = json.loads(intent_path.read_text(encoding='utf-8'))
        if intent["method"] != "apply":
            continue
        result_path = intent_path.with_name("result.json")
        if not result_path.exists():
            reason = reason or "存在未闭合写入：" + str(intent_path.parent)
            unconfirmed = True
            continue
        reply = json.loads(result_path.read_text(encoding='utf-8'))
        result = reply.get("result", {})
        safe = (not reply.get("execution_started", True) if not reply.get("ok") else
                result.get("write_attempted") is False and not result.get("settings_write_attempted"))
        if not safe and (not reply.get("ok") or result.get("status") not in TERMINAL or result.get("record_warning")):
            reason = reason or "前序写入结果需要核对：" + str(intent_path.parent)
            if not reply.get("ok") or (not result.get("native_result") and
                    result.get("status") in {"uncertain", "close-preparation-uncertain"}):
                unconfirmed = True
    return reason, unconfirmed


def execution_resources_digest():
    """Pin fixed executors and policies, including close-setting components."""
    root = files("a6700_unlock")
    resources = [item for item in root.joinpath("data/native").iterdir()
                 if item.is_file() and not item.name.startswith("._") and item.name != ".DS_Store"]
    resources += [root.joinpath("native", name) for name in ("transaction.c", "close_setting.c", "close_state.c")]
    resources += [root.joinpath("data", name) for name in ("guard-compatibility.json", "guard-volatile.json")]
    return digest({str(item): hashlib.sha256(item.read_bytes()).hexdigest() for item in resources})


def descriptor_path(state_dir=None):
    return Path(state_dir or operations.default_state_dir()) / "msc-session.json"


def _process_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    wait_single = kernel.WaitForSingleObject
    wait_single.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait_single.restype = wintypes.DWORD
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION; never use os.kill(pid, 0)
    # on Windows because signal 0 maps to CTRL_C_EVENT via GenerateConsoleCtrlEvent.
    handle = open_process(0x00101000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        return wait_single(handle, 0) == 258
    finally:
        close_handle(handle)


def recover_descriptor(state_dir=None):
    """Explicit bench job: retire a dead endpoint, never a lock or transaction.

    No authentication, STOP, USB reset or write is performed. Unresolved write
    journals stay intact and continue blocking writes in subsequent sessions.
    """
    if not os.environ.get("A6700_BENCH_JOB"):
        raise DeviceError("端点恢复必须通过共享调试台排队")
    path = descriptor_path(state_dir)
    lock_path = operations.host_lock_path()
    operations.make_directory(lock_path.parent)
    with lock_path.open("a+b") as handle, operations.host_io.exclusive_lock(handle):
        raw = path.read_bytes()
        try:
            info = json.loads(raw)
        except ValueError:
            info = {}
        pid = info.get("pid") if isinstance(info, dict) else None
        if type(pid) is int and pid > 0 and _process_alive(pid):
            raise DeviceError("端点所登记进程仍存在；不能接管或移除，请核对其状态")
        directory = path.parent / "msc-sessions" / ("endpoint-recovery-" + uuid.uuid4().hex)
        operations.make_directory(directory)
        result = dict(schema=SCHEMA, descriptor_sha256=hashlib.sha256(raw).hexdigest(),
                      previous_session_id=info.get("session_id") if isinstance(info, dict) else None,
                      wall_time=time.time(), camera_access=False, write_attempted=False,
                      transaction_recovery="not-performed")
        operations.save_json(directory / "recovery.json", result)
        path.unlink()
        operations.host_io.sync_directory(path.parent)
        return dict(**result, evidence_directory=str(directory))


def _seconds(value, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= upper:
        raise ValueError("会话期限必须为有限正数，且不超过 %s 秒" % upper)
    return float(value)


class PersistentUsbSession:
    """One lock and authentication; only the worker calls this device owner.

    Requests are serialized, leases expire only between requests, and no timer
    can interrupt a write/rollback. backend_factory/clock are test injections,
    never fields accepted by IPC. Real access requires a shared-bench job.
    """
    def __init__(self, *, timeout_seconds=300, max_duration_seconds=900,
                 state_dir=None, profile=None, backend_factory=None,
                 progress=lambda _: None, clock=time.monotonic):
        self.timeout = _seconds(timeout_seconds, 900)
        self.max_duration = _seconds(max_duration_seconds, 3600)
        self.directory = Path(state_dir or operations.default_state_dir())
        self.profile = profile or load_device_profile(load_catalog())
        self.factory, self.progress, self.clock = backend_factory, progress, clock
        self.id = uuid.uuid4().hex
        self.evidence = self.directory / "msc-sessions" / self.id
        self.mutex = threading.RLock()
        self.stack = None
        self.access = self.backend = None
        self.opened = self.closing = self.uncertain = self.execution_unconfirmed = False
        self.plans = {}
        self.write_block_reason = None
        self.cleanup = None
        self.adapter_set = None

    def __enter__(self):
        if self.stack is not None or self.opened or self.cleanup is not None:
            raise DeviceError("会话不能重复打开")
        if self.factory is None and not os.environ.get("A6700_BENCH_JOB"):
            raise DeviceError("相机会话必须通过共享调试台提交，请使用 scripts/msc-tool run 或 session start")
        self.stack = contextlib.ExitStack()
        try:
            self.stack.enter_context(operations.device_lock(self.directory, observe_returned_settings=True))
            operations.make_directory(self.evidence / "requests")
            operations.save_json(self.evidence / "session.json", dict(
                schema=SCHEMA, session_id=self.id, bench_job=os.environ.get("A6700_BENCH_JOB"),
                profile_sha256=self.profile.digest, catalog_sha256=self.profile.catalog.digest,
                timeout_seconds=self.timeout, max_duration_seconds=self.max_duration,
                opened_at=time.time(), pid=os.getpid()))
            self.write_block_reason = self._unresolved_write()
            if self.prior_execution_unconfirmed:
                raise DeviceError(self.write_block_reason + "；原生执行未确认结束，未认证相机，请先按原事务恢复入口核对")
            from .capability_packages import builtin_adapter_set
            self.adapter_set = builtin_adapter_set(self.profile, include_creative_looks=False,
                                                  include_custom_sound=False)
            self.resources_digest = execution_resources_digest()
            self.access = UsbDeviceAccess(self.profile, self.factory)
            authentication_started = self.clock()
            self.access.prepare_access(self.progress)
            self.backend = self.access.open_transport(self.progress)
            self.authentication_seconds = self.clock() - authentication_started
            self.opened = True
            self.started = self.last_activity = self.clock()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _unresolved_write(self):
        reason, self.prior_execution_unconfirmed = pending_session_write(self.directory)
        return reason

    def expired(self):
        return (self.opened and (self.clock() - self.last_activity >= self.timeout
                                or self.clock() - self.started >= self.max_duration))

    def status(self):
        return dict(schema=SCHEMA, session_id=self.id, bench_job=os.environ.get("A6700_BENCH_JOB"),
                    profile_sha256=self.profile.digest, evidence_directory=str(self.evidence),
                    authentication_seconds=self.authentication_seconds,
                    state="uncertain" if self.uncertain else "draining" if self.closing else "ready",
                    idle_remaining=max(0, self.timeout - (self.clock() - self.last_activity)),
                    duration_remaining=max(0, self.max_duration - (self.clock() - self.started)),
                    writes_blocked=self.write_block_reason)

    def _read(self, params):
        if params:
            if set(params) != {"device", "offset", "size"}:
                raise ValueError("read 仅接受 device/offset/size，或空参数读取固定 profile")
            dev, off, size = params["device"], params["offset"], params["size"]
            if (dev not in self.profile.data["devices"] or type(off) is not int or type(size) is not int
                    or off < 0 or off % 512 or size <= 0 or size % 512 or size > 512 * len(self.profile.entries)):
                raise ValueError("只允许固定 profile 内的完整、对齐扇区")
            device = self.profile.data["devices"].index(dev)
            mapping = {(e["device"], e["device_offset"]): i for i, e in enumerate(self.profile.entries)}
            try:
                indices = [mapping[(device, pos)] for pos in range(off, off + size, 512)]
            except KeyError as exc:
                raise ValueError("读取范围超出固定 profile；没有发送读取器") from exc
        else:
            indices = list(range(len(self.profile.entries)))
        nonce = uuid.uuid4().bytes
        started = self.clock()
        sectors = self.backend.read(nonce)
        elapsed = self.clock() - started
        if len(sectors) != len(self.profile.entries) or any(len(s) != 512 for s in sectors):
            raise DeviceError("读取结果不符合固定 profile")
        return dict(indices=indices, sectors=[sectors[i].hex() for i in indices],
                    profile_sha256=self.profile.digest, snapshot_sha256=sector_digest(sectors),
                    nonce=nonce.hex(), native_elapsed_seconds=elapsed,
                    native_scope="full-fixed-profile")

    def _plan(self, params):
        if set(params) != {"action", "features"} or params["action"] not in ("unlock", "close"):
            raise ValueError("plan 仅支持现有固定功能的 unlock/close")
        features = params["features"]
        if (not isinstance(features, list) or not features or any(not isinstance(f, str) for f in features)
                or len(features) != len(set(features)) or not set(features) <= set(self.profile.catalog.available_features)):
            raise ValueError("必须明确选择已开放的固定 profile 功能")
        from .capability_packages import resolve_capability_selection
        if execution_resources_digest() != self.resources_digest:
            raise DeviceError("执行资源在会话期间变化，请结束会话并重新准备")
        resolution = resolve_capability_selection(self.adapter_set, features, params["action"])
        snapshots = []
        result = operations._execute_session(
            self.profile, self.backend, params["action"], self.directory, self.progress,
            dry_run=True, features=features, manage_backend=False,
            preimage_guard=lambda sectors: snapshots.append(sector_digest(sectors)))
        if result.get("status") == "dry-run" and result["plan"]["compatible"]:
            definition = dict(session_id=self.id, plan_nonce=uuid.uuid4().hex,
                              profile_sha256=self.profile.digest, execution_resources_sha256=self.resources_digest,
                              action=params["action"], features=features,
                              before_sha256=snapshots[0], capability_resolution=resolution,
                              preparation_checks=result.get("close_preparation", {}).get("checks", []))
            plan_id = digest(definition)
            # Persist approval material before advertising an executable plan.
            path = self.evidence / ("plan-" + plan_id + ".json")
            if not path.exists():
                operations.save_json(path, definition)
            self.plans[plan_id] = definition
            result["plan_id"] = plan_id
        return result

    def _apply(self, params):
        if set(params) != {"plan_id", "confirmed"} or params["confirmed"] is not True:
            raise ValueError("apply 必须携带本会话计划摘要及明确 confirmed=true")
        if self.write_block_reason:
            raise DeviceError(self.write_block_reason)
        plan_id = params["plan_id"]
        if not isinstance(plan_id, str) or plan_id not in self.plans:
            raise ValueError("计划不存在、已使用或已失效；请重新生成计划")
        plan = self.plans[plan_id]
        # An execution consumes every outstanding approval, including failures.
        self.plans.clear()
        if execution_resources_digest() != plan["execution_resources_sha256"]:
            raise DeviceError("执行资源在计划后变化；未执行写入，请结束会话并重新准备")

        def guard(sectors):
            if sector_digest(sectors) != plan["before_sha256"]:
                raise DeviceError("计划前态已变化；未执行写入，请重新生成计划")

        def preparation_guard(rows):
            if digest(rows) != digest(plan["preparation_checks"]):
                raise DeviceError("计划中的关闭前设置已变化；未修改设置或固件，请重新生成计划")

        self.execution_started = True
        result = operations._execute_session(
            self.profile, self.backend, plan["action"], self.directory, self.progress,
            features=plan["features"], manage_backend=False, preimage_guard=guard,
            preparation_guard=preparation_guard)
        result["plan_id"] = plan_id
        attempted = result.get("write_attempted") or result.get("settings_write_attempted")
        if attempted and (result.get("status") not in TERMINAL or result.get("record_warning")):
            self.write_block_reason = "本次事务需要核对，禁止继续写入：" + result.get("run_directory", "")
            # No STOP/reconnect may interrupt an unconfirmed native execution.
            if attempted:
                self.uncertain = True
                self.execution_unconfirmed = (not result.get("native_result")
                                              and result.get("status") in {"uncertain", "close-preparation-uncertain"})
        return result

    def request(self, method, params=None, *, request_id=None):
        with self.mutex:
            if not self.opened:
                raise DeviceError("会话尚未打开或已经关闭")
            if self.closing or self.expired():
                self.closing = True
                raise DeviceError("会话正在退出或期限已到；不会自动重连")
            params = {} if params is None else params
            if not isinstance(params, dict) or not isinstance(method, str) or method not in {"status", "stop", "result", "read", "inspect", "plan", "apply"}:
                raise ValueError("未知方法或参数类型")
            if method in {"status", "stop", "inspect"} and params:
                raise ValueError("该方法不接受参数")
            ident = uuid.uuid4().hex if request_id is None else request_id
            if not isinstance(ident, str) or len(ident) != 32 or any(c not in "0123456789abcdef" for c in ident):
                raise ValueError("request_id 必须为32位小写十六进制")
            directory = self.evidence / "requests" / ident
            signature = dict(method=method, params=params)
            if directory.exists():
                previous = json.loads((directory / "intent.json").read_text(encoding='utf-8'))
                if previous["request_sha256"] != digest(signature):
                    raise DeviceError("request_id 已绑定其他请求")
                result = directory / "result.json"
                if not result.exists():
                    raise DeviceError("请求结果未知；只能核对记录，不会重发")
                return json.loads(result.read_text(encoding='utf-8'))
            operations.make_directory(directory)
            operations.save_json(directory / "intent.json", dict(
                method=method, params=params, request_id=ident, request_sha256=digest(signature),
                wall_time=time.time(), profile_sha256=self.profile.digest))
            started = self.clock()
            self.execution_started = False
            try:
                if method in {"read", "inspect", "plan", "apply"}:
                    if self.uncertain:
                        raise DeviceError("相机执行状态未知；本会话停止设备操作")
                    channel = getattr(self.backend, "channel", None)
                    if channel is not None and channel.sequence > 60000:
                        self.closing = True
                        raise DeviceError("接近 Senser 序号上限，请结束会话后重新排队")
                if method == "status":
                    result = self.status()
                elif method == "stop":
                    self.closing = True
                    result = self.status()
                elif method == "result":
                    key = params.get("request_id")
                    if (set(params) != {"request_id"} or not isinstance(key, str) or len(key) != 32
                            or any(c not in "0123456789abcdef" for c in key)):
                        raise ValueError("result 需要原请求的32位 request_id")
                    path = self.evidence / "requests" / key / "result.json"
                    result = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(status="unknown", request_id=key)
                elif method == "read":
                    result = self._read(params)
                elif method == "inspect":
                    result = operations._execute_session(self.profile, self.backend, "inspect", self.directory,
                                                         self.progress, manage_backend=False)
                elif method == "plan":
                    result = self._plan(params)
                else:
                    result = self._apply(params)
                reply = dict(ok=True, request_id=ident, result=result)
                if method not in {"status", "stop", "result"}:
                    self.last_activity = self.clock()
                if method in {"plan", "inspect"} and result.get("status") in {"error", "uncertain"}:
                    self.closing = True
            except Exception as exc:
                reply = dict(ok=False, request_id=ident, error=str(exc), execution_started=self.execution_started)
                if method == "apply" and self.execution_started:
                    self.uncertain = True
                    self.execution_unconfirmed = True
                    self.write_block_reason = "执行过程异常，需核对本次记录：" + str(directory)
                if isinstance(exc, DeviceError) and method == "read":
                    self.closing = True
            reply["elapsed_seconds"] = self.clock() - started
            try:
                operations.save_json(directory / "result.json", reply)
            except BaseException:
                self.closing = True
                if method == "apply":
                    self.uncertain = True
                    # A result persistence failure alone does not mean the
                    # native runner is still active; preserve its known state.
                raise
            return reply

    def __exit__(self, *unused):
        with self.mutex:
            if self.cleanup is not None:
                return
            self.closing = True
            cleanup = dict(stop_sent=False, msc_observed=False, transaction_uncertain=self.uncertain,
                           execution_unconfirmed=self.execution_unconfirmed,
                           stop_requested=self.opened and not self.execution_unconfirmed,
                           stop_acknowledged=False)
            try:
                if self.access is not None:
                    try:
                        cleanup.update(self.access.release_access(send_stop=not self.execution_unconfirmed))
                    except Exception as exc:
                        cleanup.update(cleanup_status="unconfirmed", cleanup_warning=str(exc))
                    cleanup["stop_sent"] = cleanup.get("cleanup_status") == "stop-sent"
                    cleanup["stop_acknowledged"] = cleanup["stop_sent"]
                    if self.opened and not self.execution_unconfirmed:
                        observe = getattr(self.backend, "observe_normal_mode", None)
                        try:
                            cleanup["msc_observed"] = bool(observe()) if observe else False
                        except Exception as exc:
                            cleanup["observation_error"] = str(exc)
                if self.evidence.exists():
                    cleanup["closed_at"] = time.time()
                    operations.save_json(self.evidence / "exit.json", cleanup)
            finally:
                self.cleanup = cleanup
                self.opened = False
                self.access = None
                if self.stack is not None:
                    self.stack.close()
                    self.stack = None


def _receive(sock):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            part = sock.recv(size - len(data))
            if not part:
                raise DeviceError("IPC 回复中断；请求不会自动重发")
            data.extend(part)
        return bytes(data)
    size = struct.unpack("!I", exact(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise DeviceError("IPC 帧大小越界")
    def fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("IPC JSON 字段重复")
            result[key] = value
        return result
    return json.loads(exact(size), object_pairs_hook=fields,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("IPC 数值无效")))


def _send(sock, data):
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > MAX_FRAME:
        raise DeviceError("IPC 帧大小越界")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


class SessionServer:
    """Paired local IPC; one accept/dispatch loop owns all camera traffic."""
    def __init__(self, owner):
        self.owner = owner
        self.path = descriptor_path(owner.directory)
        self.token = secrets.token_hex(32)
        self.socket = None
        self.temp = None
        self.closed = False

    def __enter__(self):
        # Reserve the descriptor before opening USB. Never erase a crashed
        # worker's descriptor or overwrite its recovery evidence automatically.
        if self.closed or self.socket is not None:
            raise DeviceError("worker 不能重复打开")
        operations.make_directory(self.path.parent)
        self.reservation = operations.host_io.open_exclusive(self.path)
        try:
            self.owner.__enter__()
            if os.name == "posix":
                self.temp = tempfile.TemporaryDirectory(prefix="a6700-msc-")
                address = str(Path(self.temp.name) / "ipc")
                self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.socket.bind(address)
                os.chmod(address, 0o600)
                endpoint = dict(kind="unix", address=address)
            else:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.bind(("127.0.0.1", 0))
                endpoint = dict(kind="loopback", port=self.socket.getsockname()[1])
            self.socket.listen(8)
            self.socket.settimeout(0.2)
            self.info = dict(schema=SCHEMA, token=self.token, endpoint=endpoint,
                             pid=os.getpid(), session_id=self.owner.id,
                             evidence_directory=str(self.owner.evidence),
                             bench_job=os.environ.get("A6700_BENCH_JOB"))
            with os.fdopen(self.reservation, "w") as output:
                self.reservation = None
                json.dump(self.info, output)
                output.flush()
                os.fsync(output.fileno())
            operations.host_io.sync_directory(self.path.parent)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def step(self):
        if self.owner.closing or self.owner.expired():
            return False
        try:
            connection, _ = self.socket.accept()
        except socket.timeout:
            return True
        with connection:
            connection.settimeout(2)
            try:
                request = _receive(connection)
                if (not isinstance(request, dict) or set(request) != {"schema", "token", "request_id", "method", "params"}
                        or request["schema"] != SCHEMA or not isinstance(request["token"], str)
                        or len(request["token"]) != 64
                        or any(c not in "0123456789abcdef" for c in request["token"])
                        or not hmac.compare_digest(request["token"], self.token)):
                    raise ValueError("IPC 身份或协议不匹配")
                reply = self.owner.request(request["method"], request["params"], request_id=request["request_id"])
            except (ValueError, DeviceError, OSError, RecursionError) as exc:
                reply = dict(ok=False, error=str(exc))
            try:
                _send(connection, reply)
            except OSError:
                # A lost client cannot cancel or replay an executed writer.
                pass
        return not self.owner.closing

    def __exit__(self, *args):
        if self.closed:
            return
        self.closed = True
        try:
            if self.socket is not None:
                self.socket.close()
            if self.reservation is not None:
                os.close(self.reservation)
                self.reservation = None
            if self.owner.stack is not None:
                self.owner.__exit__(*args)
        finally:
            if self.temp is not None:
                self.temp.cleanup()
            self.path.unlink(missing_ok=True)
            operations.host_io.sync_directory(self.path.parent)


class CameraMscSession:
    """Attach to a worker started by msc-tool run/session start.

    Closing this client closes no USB handle. The run wrapper owns worker
    lifetime; other clients may attach without stopping each other's session.
    """
    def __init__(self, *, state_dir=None, timeout_seconds=300, descriptor=None):
        self.path = Path(descriptor or os.environ.get("A6700_MSC_SESSION") or descriptor_path(state_dir))
        self.timeout = _seconds(timeout_seconds, 3600)
        self.info = None
        self.last_request_id = None

    def __enter__(self):
        self.info = json.loads(self.path.read_text(encoding='utf-8'))
        self.call("status")
        return self

    def __exit__(self, *unused):
        self.info = None

    def call(self, method, **params):
        if self.info is None:
            raise DeviceError("请通过 with CameraMscSession() 连接已启动的会话")
        endpoint = self.info["endpoint"]
        if endpoint["kind"] == "unix":
            family, address = socket.AF_UNIX, endpoint["address"]
        elif endpoint["kind"] == "loopback":
            family, address = socket.AF_INET, ("127.0.0.1", endpoint["port"])
        else:
            raise DeviceError("未知 IPC 传输")
        self.last_request_id = uuid.uuid4().hex
        try:
            with socket.socket(family, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(address)
                _send(connection, dict(schema=SCHEMA, token=self.info["token"],
                                       request_id=self.last_request_id, method=method, params=params))
                reply = _receive(connection)
        except (OSError, ValueError, DeviceError) as exc:
            raise DeviceError("IPC 结果未取回，不得重发；request_id=" + self.last_request_id
                              + "; evidence=" + self.info["evidence_directory"] + "; " + str(exc)) from exc
        if not reply.get("ok"):
            raise DeviceError(reply.get("error", "IPC 请求失败") + "; request_id=" + self.last_request_id)
        return reply["result"]

    def read(self, *, device, offset, size=512):
        result = self.call("read", device=device, offset=offset, size=size)
        return b"".join(bytes.fromhex(s) for s in result["sectors"])

    def read_profile(self):
        return [bytes.fromhex(s) for s in self.call("read")["sectors"]]

    def inspect(self):
        return self.call("inspect")

    def plan(self, action, features):
        return self.call("plan", action=action, features=features)

    def apply(self, plan_id, *, confirmed=False):
        return self.call("apply", plan_id=plan_id, confirmed=confirmed)

    def status(self):
        return self.call("status")

    def stop(self):
        return self.call("stop")

    def result(self, request_id):
        return self.call("result", request_id=request_id)
