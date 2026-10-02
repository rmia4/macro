"""Windows SendInput 래퍼. 로직 없이 OS 호출만 담당한다 (나머지는 이 인터페이스를 주입받음)."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol

import keys

IS_WINDOWS = sys.platform == "win32"

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x8
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_DOWN = {"left": 0x0002, "right": 0x0008, "middle": 0x0020, "x1": 0x0080, "x2": 0x0080}
MOUSEEVENTF_UP = {"left": 0x0004, "right": 0x0010, "middle": 0x0040, "x1": 0x0100, "x2": 0x0100}
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_ABSOLUTE = 0x4000, 0x8000
XBUTTON_DATA = {"x1": 1, "x2": 2}
WHEEL_DELTA = 120

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def key_input(name: str, up: bool) -> INPUT:
    scan, ext = keys.NAME_TO_SCAN[name]
    flags = KEYEVENTF_SCANCODE
    if ext:
        flags |= KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= KEYEVENTF_KEYUP
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    return inp


def mouse_input(dx: int, dy: int, data: int, flags: int) -> INPUT:
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return inp


def normalize_abs(x: int, y: int, vx: int, vy: int, vw: int, vh: int) -> tuple[int, int]:
    """가상 데스크톱 픽셀 좌표 -> 0..65535 정규화 좌표."""
    nx = round((x - vx) * 65535 / max(vw - 1, 1))
    ny = round((y - vy) * 65535 / max(vh - 1, 1))
    return min(max(nx, 0), 65535), min(max(ny, 0), 65535)


class InputBackend(Protocol):
    def key_down(self, name: str) -> None: ...
    def key_up(self, name: str) -> None: ...
    def mouse_move_abs(self, x: int, y: int) -> None: ...
    def mouse_move_rel(self, dx: int, dy: int) -> None: ...
    def mouse_down(self, button: str) -> None: ...
    def mouse_up(self, button: str) -> None: ...
    def scroll(self, dx: int, dy: int) -> None: ...
    def cursor_pos(self) -> tuple[int, int]: ...
    def screen_size(self) -> tuple[int, int]: ...
    def foreground_title(self) -> str: ...
    def find_window_rect(self, title: str) -> tuple[int, int, int, int] | None: ...


# ---- 프로세스 초기화 (Windows 전용, 그 외 OS에선 no-op) ----

def init_process() -> None:
    """DPI 인식과 타이머 해상도 1ms 설정. 다른 GUI/입력 라이브러리 import 전에 호출."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    ctypes.windll.winmm.timeBeginPeriod(1)


def shutdown_process() -> None:
    if IS_WINDOWS:
        ctypes.windll.winmm.timeEndPeriod(1)


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class WindowsBackend:
    def __init__(self) -> None:
        if not IS_WINDOWS:
            raise OSError("WindowsBackend 는 Windows 에서만 사용할 수 있습니다")
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._u.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._u.SendInput.restype = wintypes.UINT
        self._u.GetForegroundWindow.restype = wintypes.HWND
        self._u.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        self._u.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
        self._u.IsWindowVisible.argtypes = (wintypes.HWND,)
        self._u.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self._u.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        self._enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _send(self, inp: INPUT) -> None:
        if self._u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
            raise OSError(ctypes.get_last_error(), "SendInput 실패 (UIPI/권한 문제일 수 있음)")

    # keyboard
    def key_down(self, name: str) -> None:
        self._send(key_input(name, up=False))

    def key_up(self, name: str) -> None:
        self._send(key_input(name, up=True))

    # mouse
    def mouse_move_abs(self, x: int, y: int) -> None:
        g = self._u.GetSystemMetrics
        nx, ny = normalize_abs(x, y, g(76), g(77), g(78), g(79))
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        self._send(mouse_input(nx, ny, 0, flags))

    def mouse_move_rel(self, dx: int, dy: int) -> None:
        self._send(mouse_input(dx, dy, 0, MOUSEEVENTF_MOVE))

    def mouse_down(self, button: str) -> None:
        self._send(mouse_input(0, 0, XBUTTON_DATA.get(button, 0), MOUSEEVENTF_DOWN[button]))

    def mouse_up(self, button: str) -> None:
        self._send(mouse_input(0, 0, XBUTTON_DATA.get(button, 0), MOUSEEVENTF_UP[button]))

    def scroll(self, dx: int, dy: int) -> None:
        if dy:
            self._send(mouse_input(0, 0, dy * WHEEL_DELTA, MOUSEEVENTF_WHEEL))
        if dx:
            self._send(mouse_input(0, 0, dx * WHEEL_DELTA, MOUSEEVENTF_HWHEEL))

    # queries
    def cursor_pos(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        self._u.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def screen_size(self) -> tuple[int, int]:
        return self._u.GetSystemMetrics(0), self._u.GetSystemMetrics(1)

    def _title(self, hwnd) -> str:
        n = self._u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        self._u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def foreground_title(self) -> str:
        hwnd = self._u.GetForegroundWindow()
        return self._title(hwnd) if hwnd else ""

    def _client_rect(self, hwnd) -> tuple[int, int, int, int] | None:
        rect, pt = wintypes.RECT(), wintypes.POINT(0, 0)
        self._u.GetClientRect(hwnd, ctypes.byref(rect))
        self._u.ClientToScreen(hwnd, ctypes.byref(pt))
        if rect.right <= 0 or rect.bottom <= 0:
            return None
        return pt.x, pt.y, pt.x + rect.right, pt.y + rect.bottom

    def find_window_rect(self, title: str) -> tuple[int, int, int, int] | None:
        """제목에 title(대소문자 무시)이 포함된 창의 클라이언트 영역 (l, t, r, b)."""
        needle = title.lower()
        fg = self._u.GetForegroundWindow()
        if fg and needle in self._title(fg).lower():
            rect = self._client_rect(fg)
            if rect:
                return rect
        found: list[tuple[int, int, int, int]] = []

        def cb(hwnd, _):
            if self._u.IsWindowVisible(hwnd) and needle in self._title(hwnd).lower():
                rect = self._client_rect(hwnd)
                if rect:
                    found.append(rect)
                    return False
            return True

        self._u.EnumWindows(self._enum_proc(cb), 0)
        return found[0] if found else None
