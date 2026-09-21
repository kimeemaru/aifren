"""Platform file primitives for existing registry and continuity ownership.

POSIX retains flock/no-follow. Windows uses handle-based, shared/exclusive byte
locks; reparse points are rejected rather than weakening the ownership fence.
No lock discovers or creates application data beyond its explicit lock file.
"""
import os
from pathlib import Path

LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8


def sync_directory(path):
    # Windows has no portable directory fsync (same contract as canonical JSON
    # persistence). Callers fsync file content before same-volume replacement.
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if os.name != 'nt':
    from fcntl import flock

    def open_lock_file(path):
        return os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
else:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class _Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                    ('hEvent', wintypes.HANDLE)]

    class _FileInfo(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
                    ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
                    ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                    ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                    ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]

    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    _kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.POINTER(_Overlapped)]

    def _long_path(path):
        text = str(path)
        if text.startswith('\\\\?\\'):
            return text
        return '\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text

    def _open_handle(path, *, directory):
        # OPEN_REPARSE_POINT prevents following the leaf. Pin each parent
        # without share-delete while opening its child, including junctions.
        handle = _kernel.CreateFileW(
            _long_path(path), 0x80 if directory else 0xC0000000,
            3 if directory else 7, None, 3 if directory else 4,
            0x00200000 | (0x02000000 if directory else 0), None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = _FileInfo()
            if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if (info.attributes & 0x400 or bool(info.attributes & 0x10) != directory
                    or (not directory and info.links != 1)):
                raise OSError('Lock ownership rejects reparse points, directories and hard links.')
            return handle
        except BaseException:
            _kernel.CloseHandle(handle)
            raise

    def open_lock_file(path):
        path = Path(os.path.abspath(path))
        parents, leaf = [], None
        try:
            for parent in reversed(path.parents):
                parents.append(_open_handle(parent, directory=True))
            leaf = _open_handle(path, directory=False)
            descriptor = msvcrt.open_osfhandle(leaf, os.O_RDWR | os.O_BINARY)
            leaf = None  # descriptor now owns the handle
            return descriptor
        finally:
            if leaf is not None:
                _kernel.CloseHandle(leaf)
            for handle in reversed(parents):
                _kernel.CloseHandle(handle)

    def flock(descriptor, operation):
        handle = msvcrt.get_osfhandle(descriptor)
        offset = _Overlapped()
        if operation == LOCK_UN:
            ok = _kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(offset))
        else:
            flags = (2 if operation & LOCK_EX else 0) | (1 if operation & LOCK_NB else 0)
            ok = _kernel.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(offset))
        if not ok:
            code = ctypes.get_last_error()
            if operation & LOCK_NB and code in (32, 33):
                raise BlockingIOError(code, 'Character storage lock is busy.')
            # Unlocking an unacquired failed lease is harmless, like flock.
            if operation == LOCK_UN and code == 158:
                return
            raise ctypes.WinError(code)
