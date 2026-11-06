"""A lock shared by every process on this machine (a named Win32 mutex).

The editor keeps a single remote-execution command connection; a new one replaces the
old. Every server process takes this lock around open -> command -> close so that
several Claude sessions queue instead of cutting each other off.
"""
from __future__ import annotations

import sys
import threading
import time

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102


class LockTimeout(Exception):
    pass


_kernel32 = None


def _k32():
    global _kernel32
    if _kernel32 is None:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.ReleaseMutex.restype = wintypes.BOOL
        k.ReleaseMutex.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32 = k
    return _kernel32


class MachineLock:
    def __init__(self, name: str):
        self.name = name
        self._handle = None
        self._fallback = threading.Lock() if sys.platform != "win32" else None

    def acquire(self, timeout: float) -> None:
        if self._fallback is not None:
            if not self._fallback.acquire(timeout=timeout):
                raise LockTimeout(self.name)
            return
        import ctypes

        k = _k32()
        handle = k.CreateMutexW(None, False, "Local\\" + self.name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        deadline = time.monotonic() + timeout
        while True:
            wait_ms = max(0, int(min(1.0, deadline - time.monotonic()) * 1000))
            res = k.WaitForSingleObject(handle, wait_ms)
            if res in (WAIT_OBJECT_0, WAIT_ABANDONED):
                self._handle = handle
                return
            if res != WAIT_TIMEOUT or time.monotonic() >= deadline:
                k.CloseHandle(handle)
                raise LockTimeout(self.name)

    def release(self) -> None:
        if self._fallback is not None:
            self._fallback.release()
            return
        if self._handle is None:
            return
        k = _k32()
        k.ReleaseMutex(self._handle)
        k.CloseHandle(self._handle)
        self._handle = None

    def hold(self, timeout: float):
        lock = self

        class _Ctx:
            def __enter__(self_inner):
                lock.acquire(timeout)
                return lock

            def __exit__(self_inner, *exc):
                lock.release()

        return _Ctx()
