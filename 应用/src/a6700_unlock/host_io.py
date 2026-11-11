"""Host platform adapters for paths, durable records and exclusive locks.

The camera workflow owns *when* these primitives are used. This module only
owns platform differences; it never opens or authenticates a camera.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import sys

from .device_protocol import DeviceBusyError


def default_state_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/a6700-unlock"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "a6700-unlock"
    return Path.home() / ".local/state/a6700-unlock"


def host_lock_path():
    # Shared with the research wizards and the desktop application.
    if sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path.home() / ".local/state"
    return base / "sony-a6700-ftp/.assistant.lock"


def sync_directory(path):
    """Sync a new directory entry on POSIX; Windows uses write-through files."""
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def open_exclusive(path):
    """Create a record without overwriting or sharing it with another writer."""
    if os.name != "nt":
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_WRITE, no sharing, CREATE_NEW, NORMAL | WRITE_THROUGH.
    handle = create(os.fspath(path), 0x40000000, 0, None, 1, 0x80000080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close(handle)
        raise


@contextlib.contextmanager
def exclusive_lock(handle):
    """Hold the same advisory lock semantics on POSIX and Windows."""
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise DeviceBusyError("本机已有相机工具运行，请等待其完成；更换记录目录不会绕过设备锁",
                                  Path(handle.name)) from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise DeviceBusyError("本机已有相机工具运行，请等待其完成；更换记录目录不会绕过设备锁",
                              Path(handle.name)) from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
