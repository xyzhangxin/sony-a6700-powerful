"""USB execution adapter over the historically verified Senser/Jiritsu path."""
from __future__ import annotations

import contextlib
import hashlib
from importlib.resources import files
import stat
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..device_protocol import DeviceError, request, response


def payloads(profile):
    root = files("a6700_unlock").joinpath("data/native")
    manifest = json.loads(root.joinpath("manifest.json").read_text())
    if manifest["profile_sha256"] != profile.digest:
        raise DeviceError("Native payload/profile versions do not match")
    source = files("a6700_unlock").joinpath("native/transaction.c").read_bytes()
    if hashlib.sha256(source).hexdigest() != manifest["source_sha256"]:
        raise DeviceError("Native source changed without rebuilding payloads and manifest")
    result = {}
    for name in ("reader", "writer"):
        data = root.joinpath(name + ".elf").read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest["payloads"][name]["sha256"]:
            raise DeviceError(f"Packaged {name} payload hash mismatch")
        result[name] = data
    return result


LOCAL_CAPTURE_HELPER = Path("/usr/local/sbin/a6700-capture")
INSTALLED_CAPTURE_SHA256 = "56c073e2116a9a30cb3f5297d8c1527bd77f9988cadc3f6d05952f1a48f00999"


def _local_capture_helper():
    """Return the one-time-installed privileged helper when it is safe to use."""
    path = LOCAL_CAPTURE_HELPER
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        return None
    from ..bundled import resource
    current = resource('capture')
    # An older installed helper would reintroduce its reset-before-capture
    # behavior. Use it only if it is the exact current bundled implementation.
    try:
        if current is None or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(current.read_bytes()).digest():
            return None
    except OSError:
        return None
    return path


def local_capture_available():
    """True only when the one-time helper is present and NOPASSWD works."""
    local = _local_capture_helper()
    if local is None:
        return False
    try:
        result = subprocess.run(['/usr/bin/sudo', '-n', '-l', str(local)],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def installed_capture_available_in_source_mode():
    """Accept the reviewed installed helper when the source tree has no bundle."""
    from ..bundled import resource
    if resource("capture") is not None:
        return False
    path = LOCAL_CAPTURE_HELPER
    try:
        info = path.stat()
    except OSError:
        return False
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & 0o022):
        return False
    try:
        current = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    return current == INSTALLED_CAPTURE_SHA256


class _CaptureTransportError(DeviceError):
    pass


class _CaptureSession:
    """Per-worker privileged capture broker; no persistent sudoers or daemon."""

    def __init__(self, helper, progress):
        self.directory = Path(tempfile.mkdtemp(prefix='a6700-capture-session-'))
        self.socket_path = self.directory / 'capture.sock'
        self.socket = None
        self.stream = None
        try:
            progress('首次接管需要 macOS 授权；本次应用运行期间将复用该授权…')
            script = ('on run argv\n'
                      'tell application "System Events" to activate\n'
                      'set command to quoted form of item 1 of argv & " --session " & '
                      'quoted form of item 2 of argv & " --uid " & quoted form of item 3 of argv & '
                      '""\n'
                      'do shell script command with administrator privileges\n'
                      'end run')
            result = subprocess.run(['/usr/bin/osascript', '-e', script, str(helper),
                                     str(self.socket_path),
                                     str(os.getuid() if hasattr(os, 'getuid') else 0)],
                                    capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise DeviceError('系统授权或相机接口接管未完成，请确认连接与授权后再试。')
            self._connect()
        except BaseException:
            self.close()
            raise

    def _connect(self):
        deadline = time.monotonic() + 30
        while True:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(30)
            try:
                client.connect(str(self.socket_path))
                self.socket = client
                self.stream = client.makefile('rwb')
                if self.stream.readline(64) != b'READY\n':
                    raise DeviceError('本机授权接管组件握手失败。')
                return
            except (FileNotFoundError, ConnectionRefusedError):
                client.close()
                if time.monotonic() >= deadline:
                    raise DeviceError('本机授权接管组件未能启动。')
                time.sleep(0.05)
            except PermissionError as exc:
                client.close()
                raise DeviceError('本机授权接管组件 socket 权限不正确；请更新应用后重试。') from exc
            except BaseException:
                client.close()
                raise

    def capture(self):
        if self.stream is None:
            raise DeviceError('本机授权接管组件未连接。')
        try:
            self.stream.write(b'capture\n')
            self.stream.flush()
            reply = self.stream.readline(4096)
        except OSError as exc:
            raise _CaptureTransportError('本机授权接管组件连接中断；下次连接会重新授权。') from exc
        if reply != b'OK\n':
            detail = reply.decode('utf-8', 'replace').strip() or '未找到唯一 Sony MSC 相机'
            raise DeviceError('相机接口接管未完成：' + detail)

    def close(self):
        stream, self.stream = self.stream, None
        client, self.socket = self.socket, None
        try:
            if stream is not None:
                try:
                    stream.write(b'stop\n'); stream.flush(); stream.readline(64)
                except OSError:
                    pass
                stream.close()
            if client is not None:
                client.close()
        finally:
            try:
                self.socket_path.unlink()
            except OSError:
                pass
            try:
                self.directory.rmdir()
            except OSError:
                pass


_capture_session = None
_capture_session_guard = threading.Lock()


def close_capture_session():
    global _capture_session
    with _capture_session_guard:
        session, _capture_session = _capture_session, None
        if session is not None:
            session.close()


def _bundled_capture(helper, progress):
    global _capture_session
    with _capture_session_guard:
        try:
            if _capture_session is None:
                _capture_session = _CaptureSession(helper, progress)
        except BaseException:
            if _capture_session is not None:
                _capture_session.close()
                _capture_session = None
            raise
        try:
            progress('正在接管相机 MSC 接口…')
            _capture_session.capture()
        except _CaptureTransportError:
            if _capture_session is not None:
                _capture_session.close()
                _capture_session = None
            raise


def capture_macos(progress):
    local = _local_capture_helper()
    if local is not None:
        progress('正在使用本机已授权的接管组件…')
        result = subprocess.run(['/usr/bin/sudo', '-n', str(local)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and 'capture: OK' in getattr(result, 'stdout', ''):
            return
        if result.returncode == 1 and 'password is required' in (getattr(result, 'stderr', '') or '').lower():
            # No NOPASSWD rule on this machine: use the normal one-prompt path.
            pass
        else:
            detail = (getattr(result, 'stderr', '') or getattr(result, 'stdout', '') or '').strip().splitlines()
            raise DeviceError('本机已授权接管组件未能接管相机：' + (detail[-1] if detail else '未知错误'))
    if installed_capture_available_in_source_mode():
        progress('正在使用本机已安装的固定接管组件…')
        result = subprocess.run(['/usr/bin/sudo', '-n', str(LOCAL_CAPTURE_HELPER)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and 'capture: OK' in result.stdout:
            return
        detail = (result.stderr or result.stdout or '').strip().splitlines()
        raise DeviceError('本机固定接管组件未能接管相机：'
                          + (detail[-1] if detail else '未知错误'))
    from ..bundled import resource
    helper = resource('capture')
    if helper is not None:
        _bundled_capture(helper, progress)
        return
    _capture_macos_source(progress)


def _capture_macos_source(progress):
    clang = shutil.which("clang")
    if not clang:
        raise DeviceError("macOS 接口接管需要 Command Line Tools 中的 clang")
    progress("正在接管相机 MSC 接口，系统可能请求管理员授权…")
    source = files("a6700_unlock").joinpath("native/mac_capture.m").read_bytes()
    sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    clang = subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="a6700-capture-") as folder:
        folder = Path(folder)
        (folder / "capture.m").write_bytes(source)
        build = subprocess.run([clang, '-isysroot', sdk, "-fobjc-arc", "-framework", "Foundation", "-framework", "IOUSBHost",
                                "-framework", "IOKit", "-o", str(folder / "capture"), str(folder / "capture.m")],
                               capture_output=True, text=True)
        if build.returncode:
            raise DeviceError("macOS helper 编译失败：" + build.stderr[-1500:])
        # With a terminal sudo can prompt on /dev/tty. In a batch process fail
        # promptly instead of hiding a password prompt or waiting indefinitely.
        sudo = ["sudo"]
        try:
            with open("/dev/tty", "rb"):
                pass
        except OSError:
            sudo.append("-n")
        result = subprocess.run(sudo + [str(folder / "capture")], capture_output=True, text=True)
        if result.returncode:
            detail = (result.stdout + "\n" + result.stderr).strip()[-1500:]
            if 'need exactly one Sony MSC camera' in detail:
                raise DeviceError('未找到唯一 Sony MSC 相机，请确认相机处于 MSC 模式。')
            raise DeviceError("macOS 接口接管失败；需要授权时请在终端运行启动工具：" + detail)


class UsbBackend:
    def __init__(self, profile, progress=lambda message: None, capture=True):
        self.profile, self.progress, self.capture_mac = profile, progress, capture
        self.captured = not capture
        self.target = None
        self.cleanup_warning = None
        self.cleanup_status = None
        self.access_prepared = False
        self.assets = payloads(profile)

    def prepare_access(self):
        """Load verified components and capture macOS MSC, without authentication."""
        if self.access_prepared:
            raise DeviceError("USB 访问已经准备，不能重复接管")
        try:
            from . import legacy
        except ImportError as exc:
            raise DeviceError("缺少 USB 依赖，请安装 a6700-unlock[usb] 和系统 libusb") from exc
        self.legacy = legacy
        if sys.platform == "darwin" and self.capture_mac:
            import gc
            reset_backend = getattr(self.legacy, "reset_libusb_backend", None)
            if callable(reset_backend):
                reset_backend()
            gc.collect()
            capture_macos(self.progress)
            self.captured = True
        self.access_prepared = True
        return dict(stage="access-prepared", platform=sys.platform,
                    capture=self.captured, authenticated=False)

    def open_transport(self):
        """Load libusb and authenticate after prepare_access, exactly once."""
        if not self.access_prepared:
            self.prepare_access()
        if self.target is not None:
            raise DeviceError("USB 服务会话已经打开，不能重复认证")
        legacy = self.legacy
        try:
            legacy.get_libusb_backend()
        except Exception as exc:
            raise DeviceError("无法加载系统 libusb：" + str(exc)) from exc
        driver_session = None
        if sys.platform == 'win32':
            from ..windows_driver import prepare
            from ..operations import default_state_dir
            driver_session = prepare(default_state_dir(), self.progress)
        self.progress("正在认证相机 USB 服务模式…")
        # Old diagnostics include raw ProductInfo. Do not put device identifiers
        # in user reports or ordinary stdout; the sparse layout guards establish
        # supported firmware compatibility below.
        stage = "USB 认证"
        def authentication_progress(message):
            nonlocal stage
            stage = message
            self.progress(message)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.target, self.channel = legacy.run_authentication(
                    None, 0x0336, 45.0, True,
                    progress=authentication_progress)
        except Exception as exc:
            notes = getattr(exc, "__notes__", [])
            self.cleanup_warning = getattr(exc, "cleanup_warning", None) or ("；".join(notes) if notes else None)
            raise DeviceError(f"USB 认证失败（{stage}）；确认 MSC、关闭 FTP、相机唤醒且仅连接一台目标相机：" + str(exc)) from exc
        finally:
            if driver_session is not None:
                driver_session.close()
        return self

    def read_custom_sound_identity(self):
        """Fresh MSC identity before authentication, under the caller's lock.

        Audio alone requires this stronger binding; ordinary feature access
        keeps its existing compatibility guards. Persist only the digest.
        """
        import hashlib
        if not self.access_prepared or self.target is not None:
            raise DeviceError('声音机身身份必须在准备完成后、USB 认证前读取')
        devices = []
        for attempt in range(15):
            devices = [device for device in self.legacy.iter_usb_devices(fresh=True)
                       if device.idVendor == 0x054c]
            if len(devices) == 1 and devices[0].idProduct == 0x0e76:
                break
            if attempt < 14:
                time.sleep(0.5)
        if len(devices) != 1 or devices[0].idProduct != 0x0e76:
            raise DeviceError('声音导入需要唯一一台处于 MSC 的 A6700')
        identity = self.legacy.device_identity(devices[0])
        serial = identity.get('serial_number')
        if (identity.get('product') != 'ILCE-6700'
                or not isinstance(serial, str) or not serial.strip()):
            raise DeviceError('无法取得声音导入所需的机身身份')
        self._custom_sound_identity = hashlib.sha256(serial.encode('utf-8')).hexdigest()
        return self._custom_sound_identity

    def read_custom_sound_binding(self):
        """Observe the fixed installed loader in this authenticated session."""
        import uuid
        from ..custom_sound_binding import assets, request, parse
        from ..fl23_package import service_files
        from ..operations import default_state_dir, make_directory
        identity = getattr(self, '_custom_sound_identity', None)
        if self.target is None or not identity:
            raise DeviceError('加载器读取缺少认证前机身身份')
        manifest, payload = assets()
        for attempt in range(3):
            directory = default_state_dir() / 'runs' / ('audio-binding-' + uuid.uuid4().hex)
            make_directory(directory)
            nonce = uuid.uuid4().bytes
            try:
                with service_files(self, directory / 'service') as invoke:
                    try:
                        raw = invoke(payload, request(nonce))
                    except BaseException:
                        self._fl23_transport_uncertain = True
                        raise
                    self._custom_sound_service_restore_unconfirmed = True
                self._custom_sound_service_restore_unconfirmed = False
                try:
                    return parse(raw, nonce, manifest, identity, self.profile.digest)
                except DeviceError as exc:
                    # status 5 in the read-only probe means /proc/self/maps shifted
                    # concurrently during the host process scan; wait briefly and retry.
                    if '（原生状态 5）' in str(exc) and attempt < 2:
                        time.sleep(0.15)
                        continue
                    raise
            except DeviceError:
                raise
            except Exception as exc:
                raise DeviceError('声音加载器绑定读取失败：' + str(exc)) from exc

    def __enter__(self):
        # CLI compatibility keeps the established prepare -> open sequence.
        self._jiritsu_mounted = False
        self.prepare_access()
        return self.open_transport()

    def _command(self, command, payload):
        code, body = self.channel.send(0x40, struct.pack("<HH", 0x1001, command) + payload)
        if code != 0:
            raise DeviceError(f"USB 服务命令失败：0x{command:04x}, code={code}")
        return body

    def _file(self, command, ident, data):
        body = self._command(command, bytes([0, ident, 0]) + data)
        if len(body) != 4 or body != b"\0" * 4:
            raise DeviceError("文件写入或载荷执行失败，状态：" + body[:16].hex())

    def ensure_jiritsu_mount(self):
        if getattr(self, "_jiritsu_mounted", False):
            return
        self._file(0x4002, 0, b"")
        self._jiritsu_mounted = True

    def _run(self, name, config, nonce, count):
        try:
            self.ensure_jiritsu_mount()
            self._file(0x4010, 0, config)
            self._file(0x4007, 2, self.assets[name])
            body = self._command(0x4008, bytes([0, 0, 0]))
            if len(body) < 4 or body[:4] != b"\0" * 4:
                raise DeviceError("无法读取载荷结果")
            return response(body[4:], nonce, count)
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError("USB 会话中断；写入器不会自动重发：" + str(exc)) from exc

    def read(self, nonce):
        result = self._run("reader", request(self.profile, nonce), nonce, len(self.profile.entries))
        if result["status"]:
            raise DeviceError(f"目标读取失败：{result['error']}, index={result['failed_index']}")
        return result["sectors"]

    def write(self, before, after, nonce):
        return self._run("writer", request(self.profile, nonce, before, after), nonce, 0)

    def run_custom_sound_import(self, plan, record, nonce):
        """Run the fixed custom-sound slot payload once; never retry it."""
        if self.target is None or getattr(self, "channel", None) is None:
            raise DeviceError("请先建立并验证当前相机连接")
        from ..custom_sound_native import (
            custom_sound_request, custom_sound_response, load_custom_sound_payload,
        )
        config = custom_sound_request(plan, record, nonce)
        payload = load_custom_sound_payload()
        try:
            self.ensure_jiritsu_mount()
            self._file(0x4010, 0, config)
            self._file(0x4007, 2, payload)
            body = self._command(0x4008, bytes([0, 0, 0]))
            if len(body) < 4 or body[:4] != b"\0" * 4:
                raise DeviceError("无法读取自定义快门声载荷结果")
            return custom_sound_response(body[4:], nonce)
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError("USB 会话中断；自定义快门声写入器不会自动重发：" + str(exc)) from exc

    def read_custom_sound_slot(self, slot, nonce):
        """Observe one fixed slot through the caller's locked service session."""
        if self.target is None or getattr(self, 'channel', None) is None:
            raise DeviceError('请先建立并验证当前相机连接')
        from ..custom_sound_native import (
            custom_sound_read_request, custom_sound_read_response, load_custom_sound_reader)
        config = custom_sound_read_request(slot, nonce)
        payload = load_custom_sound_reader()
        try:
            self.ensure_jiritsu_mount()
            self._file(0x4010, 0, config)
            self._file(0x4007, 2, payload)
            body = self._command(0x4008, bytes(3))
            if len(body) < 4 or body[:4] != bytes(4):
                raise DeviceError('无法读取音频槽观察结果')
            return custom_sound_read_response(body[4:], nonce, slot)
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError('音频槽读取中断；未自动重发：' + str(exc)) from exc

    def read_close_state(self, features, nonce):
        if self.target is None or getattr(self,'channel',None) is None:
            raise DeviceError('请先建立并验证当前相机连接')
        from ..close_state import read_close_state
        return read_close_state(self,features,nonce)

    @property
    def automatic_close_settings(self):
        from ..close_setting import setting_assets,ORDER
        setting_assets()
        return ORDER

    native_settings_adapter = True

    def reset_close_setting(self, feature, target):
        from ..close_setting import reset_close_setting
        return reset_close_setting(self,feature,target)

    def __exit__(self, *unused):
        if self.target is not None:
            try:
                self.legacy.vendor_request_out(self.target.device, self.legacy.STOP_SENSER, strict=True)
                self.cleanup_status = "stop-sent"
            except Exception as exc:
                self.cleanup_status = "unconfirmed"
                self.cleanup_warning = "无法确认退出服务模式，请拔插 USB：" + str(exc)
            finally:
                try:
                    self.legacy.close_target(self.target)
                except Exception as exc:
                    warning = "释放 USB 接口失败，请拔插 USB：" + str(exc)
                    self.cleanup_warning = ((self.cleanup_warning + "；") if self.cleanup_warning else "") + warning
                finally:
                    self.target = None
                    self.channel = None
                    self._jiritsu_mounted = False
            if self.cleanup_status == "unconfirmed":
                try:
                    observed = self.legacy.normal_mode_observed()
                except Exception:
                    observed = False
                if observed:
                    self.cleanup_status = "msc-observed"
                    # Preserve a separate interface-release error if any.
                    if "释放 USB 接口失败" not in (self.cleanup_warning or ""):
                        self.cleanup_warning = None

    def abandon_transport(self):
        """Release host handles without interrupting a possibly active writer."""
        self.cleanup_status = "unconfirmed"
        self.cleanup_warning = "事务结果未知；未发送 STOP_SENSER，不得自动重试写入。"
        if self.target is not None:
            try:
                self.legacy.close_target(self.target)
            finally:
                self.target = None
                self.channel = None
                self._jiritsu_mounted = False

    def observe_normal_mode(self):
        """Observe real descriptors after closing; does not authenticate/reset."""
        return self.legacy.normal_mode_observed()
