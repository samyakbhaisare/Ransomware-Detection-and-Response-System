"""Shared file-access helpers.

Kept deliberately dependency-free (standard library only, no other ``rdrs``
imports) so that low-level modules such as :mod:`rdrs.entropy` can use it
without creating an import cycle through :mod:`rdrs.monitoring`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO


def open_read_shared(path: str | Path) -> IO[bytes]:
    """Open a file for reading WITHOUT blocking renames or deletes.

    On Windows, ``Path.open("rb")`` requests no ``FILE_SHARE_DELETE``, so while
    RDRS holds the handle to inspect a file, **any** other process is refused
    permission to rename or delete it (``WinError 32``, a sharing violation).

    That is unacceptable for a detection system: it would obstruct the very
    activity it exists to observe -- a ransomware burst renames files while the
    detector reads them, and the detector must never make those renames fail.

    On Windows this opens the file through ``CreateFileW`` with
    ``FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE``. On other
    platforms ``open()`` already permits rename and delete, so it is used
    directly.
    """
    if os.name != "nt":
        return open(path, "rb")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]

    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )

    if handle == INVALID_HANDLE_VALUE or handle is None:
        raise OSError(
            ctypes.get_last_error() or 0,
            f"CreateFileW failed for {path}",
            str(path),
        )

    # open_osfhandle takes ownership of the handle, so closing the returned
    # file object closes the Win32 handle too.
    descriptor = msvcrt.open_osfhandle(
        handle,
        os.O_RDONLY | os.O_BINARY,
    )

    return os.fdopen(descriptor, "rb")
