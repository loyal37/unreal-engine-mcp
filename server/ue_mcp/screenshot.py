"""Captures editor windows from outside the editor (Win32 PrintWindow), as PNG.

Works while the editor is busy, and touches nothing in the editor. Standard library only.
"""
from __future__ import annotations

import struct
import sys
import zlib

PW_RENDERFULLCONTENT = 0x00000002
HALFTONE = 4
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
DWMWA_EXTENDED_FRAME_BOUNDS = 9

_api = None


def _win():
    global _api
    if _api is not None:
        return _api
    if sys.platform != "win32":
        raise OSError("screenshots need Windows")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:
        dwmapi = None
    try:  # physical pixels, so window rects match what PrintWindow renders
        user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        user32.SetProcessDpiAwarenessContext(wintypes.HANDLE(-4))
    except (AttributeError, OSError):
        pass

    HWND, HDC, HBITMAP, HGDIOBJ = wintypes.HWND, wintypes.HDC, wintypes.HBITMAP, wintypes.HGDIOBJ
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [HWND]
    user32.IsIconic.argtypes = [HWND]
    user32.GetWindowTextLengthW.argtypes = [HWND]
    user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetDC.argtypes = [HWND]
    user32.GetDC.restype = HDC
    user32.ReleaseDC.argtypes = [HWND, HDC]
    user32.PrintWindow.argtypes = [HWND, HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [HDC]
    gdi32.CreateCompatibleDC.restype = HDC
    gdi32.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = HBITMAP
    gdi32.SelectObject.argtypes = [HDC, HGDIOBJ]
    gdi32.SelectObject.restype = HGDIOBJ
    gdi32.DeleteObject.argtypes = [HGDIOBJ]
    gdi32.DeleteDC.argtypes = [HDC]
    gdi32.SetStretchBltMode.argtypes = [HDC, ctypes.c_int]
    gdi32.SetBrushOrgEx.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    gdi32.StretchBlt.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    gdi32.GetDIBits.argtypes = [HDC, HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT]
    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _api = dict(ctypes=ctypes, wintypes=wintypes, user32=user32, gdi32=gdi32, dwmapi=dwmapi,
                WNDENUMPROC=WNDENUMPROC, BITMAPINFOHEADER=BITMAPINFOHEADER)
    return _api


def list_windows(pid: int) -> list[dict]:
    """Visible top-level windows of a process, largest first."""
    a = _win()
    ctypes, wintypes, user32 = a["ctypes"], a["wintypes"], a["user32"]
    found = []

    def cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w > 1 and h > 1:
            found.append({"hwnd": int(hwnd or 0), "title": buf.value, "x": rect.left, "y": rect.top,
                          "width": w, "height": h, "minimized": bool(user32.IsIconic(hwnd))})
        return True

    user32.EnumWindows(a["WNDENUMPROC"](cb), 0)
    found.sort(key=lambda w: w["width"] * w["height"], reverse=True)
    return found


def capture(hwnd: int, max_width: int = 1600) -> tuple[bytes, int, int, bool]:
    """PNG bytes, width, height, and whether the image came back (almost) black."""
    a = _win()
    ctypes, wintypes, user32, gdi32 = a["ctypes"], a["wintypes"], a["user32"], a["gdi32"]
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise OSError("window has no size")
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    mem2 = bmp2 = old2 = None
    try:
        if not user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            raise OSError("PrintWindow failed")
        tw, th = w, h
        src_dc, src_bmp = mem, bmp
        if max_width and w > max_width:
            tw, th = max_width, max(1, round(h * max_width / w))
            mem2 = gdi32.CreateCompatibleDC(screen)
            bmp2 = gdi32.CreateCompatibleBitmap(screen, tw, th)
            old2 = gdi32.SelectObject(mem2, bmp2)
            gdi32.SetStretchBltMode(mem2, HALFTONE)
            gdi32.SetBrushOrgEx(mem2, 0, 0, None)
            gdi32.StretchBlt(mem2, 0, 0, tw, th, mem, 0, 0, w, h, SRCCOPY)
            src_dc, src_bmp = mem2, bmp2
        bih = a["BITMAPINFOHEADER"]()
        bih.biSize = ctypes.sizeof(bih)
        bih.biWidth, bih.biHeight = tw, -th  # top-down
        bih.biPlanes, bih.biBitCount, bih.biCompression = 1, 32, BI_RGB
        buf = ctypes.create_string_buffer(tw * th * 4)
        # The bitmap must not be selected into a DC for GetDIBits on some drivers: deselect first.
        gdi32.SelectObject(src_dc, old2 if src_dc is mem2 else old)
        got = gdi32.GetDIBits(src_dc, src_bmp, 0, th, buf, ctypes.byref(bih), DIB_RGB_COLORS)
        if got != th:
            raise OSError("GetDIBits failed")
        bgra = buf.raw
        black = _mostly_black(bgra)
        return encode_png(bgra, tw, th), tw, th, black
    finally:
        if mem2 is not None:
            gdi32.DeleteObject(bmp2)
            gdi32.DeleteDC(mem2)
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


def _mostly_black(bgra: bytes) -> bool:
    sample = bgra[::4 * 97]  # every 97th pixel's blue byte
    return max(sample, default=0) < 8 and max(bgra[1::4 * 97], default=0) < 8 and max(bgra[2::4 * 97], default=0) < 8


def encode_png(bgra: bytes, width: int, height: int) -> bytes:
    rgb = bytearray(width * height * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * stride:(y + 1) * stride]) for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
