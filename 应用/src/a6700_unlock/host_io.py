"""Exclusive host records; Windows uses write-through plus the caller's fsync.

NTFS journaling alone is not a durability promise. A failed create/write/flush
must propagate before the camera writer is sent. POSIX retains parent fsync.
"""
import os


def open_exclusive(path):
    if os.name != 'nt':
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
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
