"""Small cross-process lock used around JSON read-modify-write operations."""

import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def interprocess_lock(path: Path, timeout: float = 15.0):
    """Serialize writers that may run in the desktop app and scheduler process."""
    resolved = str(Path(path).resolve()).lower()
    lock_id = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:24]

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateMutexW(None, False, f"Local\\CyberIntelJson_{lock_id}")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        result = kernel32.WaitForSingleObject(handle, max(0, int(timeout * 1000)))
        if result not in {0x00000000, 0x00000080}:  # acquired or abandoned
            kernel32.CloseHandle(handle)
            if result == 0x00000102:
                raise TimeoutError(f"等待数据文件锁超时：{Path(path).name}")
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        return

    import fcntl

    lock_path = Path(path).with_name(f".{Path(path).name}.{lock_id}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"等待数据文件锁超时：{Path(path).name}")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
