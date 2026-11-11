"""One-elevated-worker WinUSB preparation for the fixed A6700 USB identities.

The main application and firmware transactions stay unprivileged. SetupAPI is
used only for device/driver inspection; libwdi is invoked only with fixed Sony
VID and reviewed MSC/service PIDs. No USB authentication happens in this module.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .device_protocol import DeviceError

SONY_VENDOR = 0x054C
MSC_PIDS = (0x047D, 0x0E76)
SERVICE_PID = 0x0336
SUPPORTED_PIDS = frozenset(MSC_PIDS + (SERVICE_PID,))
WINUSB_SERVICES = frozenset({'winusb', 'libusb0', 'libusbk'})
NAMES = {0x047D: 'Sony A6700 - Storage', 0x0E76: 'Sony A6700 - Storage',
         SERVICE_PID: 'Sony A6700 - Service'}
DIGCF_PRESENT = 0x02
DIGCF_ALLCLASSES = 0x04
SPDRP_SERVICE = 0x04
SEE_MASK_NOCLOSEPROCESS = 0x40
CREATE_NO_WINDOW = 0x08000000


class GUID(ctypes.Structure):
    _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('ClassGuid', GUID),
                ('DevInst', wintypes.DWORD), ('Reserved', ctypes.c_void_p)]


def is_admin():
    if sys.platform != 'win32':
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _setupapi():
    api = ctypes.WinDLL('setupapi', use_last_error=True)
    api.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    api.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                         wintypes.HWND, wintypes.DWORD]
    api.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    api.SetupDiEnumDeviceInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(SP_DEVINFO_DATA)]
    api.SetupDiGetDeviceInstanceIdW.argtypes = [wintypes.HANDLE,
        ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    api.SetupDiGetDeviceRegistryPropertyW.argtypes = [wintypes.HANDLE,
        ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    return api


def _instance_id(api, handle, info):
    value = ctypes.create_unicode_buffer(512)
    needed = wintypes.DWORD()
    if not api.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(info), value,
                                           512, ctypes.byref(needed)):
        return ''
    return value.value


def _service(api, handle, info):
    value = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if not api.SetupDiGetDeviceRegistryPropertyW(handle, ctypes.byref(info),
            SPDRP_SERVICE, None, ctypes.cast(value, ctypes.c_void_p),
            ctypes.sizeof(value), ctypes.byref(needed)):
        return ''
    return value.value.strip().lower()


def devices(*, include_nonpresent=False):
    """Return only Sony parent USB devices; never open a libusb session."""
    if sys.platform != 'win32':
        return []
    api = _setupapi()
    flags = DIGCF_ALLCLASSES | (0 if include_nonpresent else DIGCF_PRESENT)
    handle = api.SetupDiGetClassDevsW(None, 'USB', None, flags)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise DeviceError('无法读取 Windows USB 驱动状态。')
    rows = []
    try:
        index = 0
        while True:
            info = SP_DEVINFO_DATA(); info.cbSize = ctypes.sizeof(info)
            if not api.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
                break
            index += 1
            identifier = _instance_id(api, handle, info)
            upper = identifier.upper()
            if 'VID_054C&PID_' not in upper or '&MI_' in upper:
                continue
            try:
                pid = int(upper.split('PID_', 1)[1][:4], 16)
            except (ValueError, IndexError):
                continue
            rows.append(dict(pid=pid, service=_service(api, handle, info)))
    finally:
        api.SetupDiDestroyDeviceInfoList(handle)
    return rows


def driver_state(pid):
    registered = [r for r in devices(include_nonpresent=True) if r['pid'] == pid]
    present = [r for r in devices() if r['pid'] == pid]
    services = sorted({r['service'] for r in registered if r['service']})
    return dict(pid=pid, present=bool(present), registered=bool(registered),
                services=services, winusb=any(s in WINUSB_SERVICES for s in services))


def _pin():
    root = Path(__file__).resolve().parents[2]
    return json.loads((root/'desktop/runtime-dependencies.json').read_text(encoding='utf-8'))['libwdi']


def wdi_path():
    from .bundled import resource
    path = resource('wdi')
    if path is None:
        path = Path(__file__).resolve().parents[2]/'desktop/windows/driver/wdi-simple.exe'
        pin = _pin()
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != pin['binary_sha256']:
                raise ValueError('digest')
        except (OSError, ValueError) as exc:
            raise DeviceError('驱动安装组件缺失或摘要变化，请重新获取完整应用。') from exc
    return path


def driver_arguments(pid, destination):
    if pid not in SUPPORTED_PIDS:
        raise ValueError('仅允许为已验证的 A6700 USB 身份准备驱动')
    return [str(wdi_path()), '--vid', '0x054C', '--pid', f'0x{pid:04X}',
            '--type', '0', '--name', NAMES[pid], '--dest', str(destination), '--log', '0']


def bind(pid, destination, progress=lambda _: None):
    """Run the verified installer once, then independently inspect the binding."""
    if pid not in SUPPORTED_PIDS:
        raise DeviceError('未允许的 USB 身份，已拒绝驱动操作。')
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    progress(f'正在为 0x{pid:04X} 准备 WinUSB…')
    try:
        result = subprocess.run(driver_arguments(pid, destination), capture_output=True,
                                text=True, timeout=300, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        if driver_state(pid)['winusb']:
            progress(f'0x{pid:04X} 的安装超过预期，但已确认 WinUSB 就绪。')
            return
        raise DeviceError(f'0x{pid:04X} 的驱动准备超过 300 秒；请检查安装进程和 Windows 驱动状态，不要连续重启。')
    if result.returncode:
        raise DeviceError(f'WinUSB 准备程序返回 {result.returncode}；请确认只连接目标相机后重试。')
    for _ in range(40):
        if driver_state(pid)['winusb']:
            progress(f'0x{pid:04X} 的 WinUSB 已就绪。')
            return
        time.sleep(.25)
    raise DeviceError(f'0x{pid:04X} 的 WinUSB 绑定未获确认，请拔插相机数据线后重试。')



def _driver_store():
    return Path(os.environ.get('SystemRoot', 'C:/Windows'))/'INF'


def _read_inf(path):
    raw = Path(path).read_bytes()
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8'
    return raw.decode(encoding, errors='ignore')


def service_staged():
    """The exact 0336 WinUSB package is already in the driver store."""
    if sys.platform != 'win32':
        return False
    try:
        for path in _driver_store().glob('oem*.inf'):
            text = _read_inf(path)
            if 'VID_054C&PID_0336' in text and 'AddService = WinUSB' in text:
                return True
    except OSError:
        pass
    return False


def stage_service(destination):
    """Register only the libwdi-generated, exact 0336 WinUSB package."""
    inf = Path(destination)/'usb_device.inf'
    text = _read_inf(inf)
    if 'DeviceID   = "VID_054C&PID_0336"' not in text or 'AddService = WinUSB' not in text:
        raise DeviceError('0336 驱动清单身份不匹配，已停止。')
    result = subprocess.run(['pnputil.exe', '/add-driver', str(inf), '/install'],
                            capture_output=True, text=True, timeout=60,
                            creationflags=CREATE_NO_WINDOW)
    if result.returncode or not service_staged():
        raise DeviceError('0336 WinUSB 预注册未获确认。')
    return True

def _write_status(path, **values):
    path = Path(path)
    # Small status frames are allowed to be observed partially; readers retry.
    # Direct writing avoids Windows replace/sharing races with the observer.
    path.write_text(json.dumps(values, ensure_ascii=False), encoding='utf-8')


def run_worker(request_path):
    """Elevated, driver-only worker. Request paths cannot select code or PIDs."""
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    if request.get('schema') != 1:
        raise DeviceError('驱动助手请求格式不受支持。')
    status = Path(request['status_path'])
    stop = Path(request['stop_path'])
    destination = Path(request['destination'])
    try:
        if not is_admin():
            raise DeviceError('驱动助手需要管理员授权；主应用仍保持普通权限。')
        initial = next((pid for pid in MSC_PIDS if driver_state(pid)['present']), None)
        if initial is not None and not driver_state(initial)['winusb']:
            bind(initial, destination)
        # Pre-stage the service binding while MSC is still present. Otherwise a
        # short-lived 0336 enumeration can return to MSC before WDI finishes.
        service = driver_state(SERVICE_PID)
        if not service['winusb']:
            try:
                bind(SERVICE_PID, destination)
            except DeviceError:
                # WDI creates a signed INF before requiring device presence.
                # Pre-register that exact package so the short 0336 window does
                # not race an interactive install. Keep on-demand as fallback.
                try:
                    stage_service(destination)
                except DeviceError:
                    pass
        _write_status(status, stage='initial_ready', pid=initial)
        if driver_state(SERVICE_PID)['winusb'] or service_staged():
            _write_status(status, stage='service_ready', pid=SERVICE_PID)
            return 0
        deadline = time.monotonic() + float(request.get('wait_seconds', 90))
        while time.monotonic() < deadline:
            if stop.exists():
                _write_status(status, stage='stopped')
                return 0
            state = driver_state(SERVICE_PID)
            if state['winusb']:
                _write_status(status, stage='service_ready', pid=SERVICE_PID)
                return 0
            if state['present']:
                bind(SERVICE_PID, destination)
                _write_status(status, stage='service_ready', pid=SERVICE_PID)
                return 0
            time.sleep(.25)
        _write_status(status, stage='waiting_service')
        return 0
    except Exception as exc:
        _write_status(status, stage='error', error=str(exc))
        return 1


def _self_command(request_path):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--driver-worker', str(request_path)]
    script = Path(__file__).resolve().parents[2]/'desktop/windows/service.py'
    return [sys.executable, '-B', str(script), '--driver-worker', str(request_path)]


def _runas(command, working_directory):
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', ctypes.c_ulong),
            ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
            ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
            ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
            ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
            ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.LPCWSTR),
            ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE),
            ('hProcess', wintypes.HANDLE)]
    info = SHELLEXECUTEINFOW(); info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS; info.lpVerb = 'runas'
    info.lpFile = command[0]
    info.lpParameters = subprocess.list2cmdline(command[1:])
    info.lpDirectory = str(working_directory); info.nShow = 0
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        raise DeviceError('管理员授权已取消，尚未更改驱动。')
    return int(info.hProcess or 0)


class DriverSession:
    def __init__(self, state_dir, progress=lambda _: None):
        self.progress = progress
        self.directory = Path(state_dir)/'driver-sessions'/uuid.uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=True)
        self.status = self.directory/'status.json'
        self.stop = self.directory/'stop.request'
        self.handle = None

    def _status(self):
        try:
            return json.loads(self.status.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def start(self):
        self.progress('首次连接需要准备 WinUSB；请在系统窗口授权专用驱动助手。')
        request = self.directory/'request.json'
        request.write_text(json.dumps(dict(schema=1, status_path=str(self.status),
            stop_path=str(self.stop), destination=str(self.directory/'wdi'),
            wait_seconds=90)), encoding='utf-8')
        self.handle = _runas(_self_command(request), self.directory)
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            status = self._status()
            if status.get('stage') in ('initial_ready', 'service_ready'):
                return self
            # The helper status file is only one signal. Independently verify
            # the actual Windows driver state so a stale/unreadable status
            # cannot leave the UI waiting after the driver is already ready.
            if driver_state(SERVICE_PID)['winusb'] or service_staged():
                return self
            if status.get('stage') == 'error':
                raise DeviceError(status.get('error', '驱动准备失败。'))
            time.sleep(.25)
        raise DeviceError('驱动准备超时；没有自动重试或继续认证。')

    def close(self):
        try:
            self.stop.touch()
        except OSError:
            pass
        if self.handle:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.WaitForSingleObject(wintypes.HANDLE(self.handle), 3000)
            kernel.CloseHandle(wintypes.HANDLE(self.handle))
            self.handle = None


def prepare(state_dir, progress=lambda _: None):
    """Return a one-UAC session when needed; otherwise no elevated process."""
    if sys.platform != 'win32':
        return None
    initial = [pid for pid in MSC_PIDS if driver_state(pid)['present']]
    if not initial:
        return None
    if len(initial) != 1:
        raise DeviceError('检测到多台 Sony MSC 设备；驱动准备要求只连接一台目标相机。')
    pid = initial[0]
    if driver_state(pid)['winusb'] and (driver_state(SERVICE_PID)['winusb'] or service_staged()):
        return None
    return DriverSession(state_dir, progress).start()
