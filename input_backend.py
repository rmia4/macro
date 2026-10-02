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


# ---- 오버레이 창: 클릭 통과 (Windows 전용, 그 외 OS 에선 no-op) ----

def make_click_through(hwnd: int) -> None:
    """창이 마우스 클릭을 받지 않고(아래 창으로 통과) 포커스도 가져가지 않게 한다."""
    if not IS_WINDOWS:
        return
    u = ctypes.windll.user32
    is64 = ctypes.sizeof(ctypes.c_void_p) == 8
    get = u.GetWindowLongPtrW if is64 else u.GetWindowLongW
    set_ = u.SetWindowLongPtrW if is64 else u.SetWindowLongW
    get.argtypes, get.restype = (wintypes.HWND, ctypes.c_int), ctypes.c_ssize_t
    set_.argtypes, set_.restype = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t), ctypes.c_ssize_t
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x20, 0x80, 0x80000, 0x08000000
    style = get(hwnd, GWL_EXSTYLE)
    set_(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE)


# ---- Raw Input 마우스 이동량 (상대 이동 녹화용, Windows 전용) ----

WM_INPUT, WM_CLOSE, WM_DESTROY = 0x00FF, 0x0010, 0x0002
RID_INPUT, RIM_TYPEMOUSE = 0x10000003, 0
RIDEV_INPUTSINK, RIDEV_REMOVE = 0x00000100, 0x00000001
MOUSE_MOVE_ABSOLUTE = 0x01
HWND_MESSAGE = -3


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class _RAWMOUSEBUTTONS(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _RAWMOUSEUNION(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("buttons", _RAWMOUSEBUTTONS)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("usFlags", wintypes.USHORT), ("u", _RAWMOUSEUNION), ("ulRawButtons", wintypes.ULONG),
                ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUT_MOUSE(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class RawMouseListener:
    """백그라운드 스레드에서 Raw Input 으로 마우스 이동량(dx, dy)을 받아 callback 에 넘긴다.

    게임이 커서를 화면 중앙에 고정해도 실제 마우스 이동량을 얻을 수 있다.
    포커스가 없어도 받도록 RIDEV_INPUTSINK + 메시지 전용 창을 쓴다.
    """

    def __init__(self, callback) -> None:
        if not IS_WINDOWS:
            raise OSError("Raw Input 녹화는 Windows 에서만 사용할 수 있습니다")
        import threading
        self.callback = callback
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self._hwnd = None
        self._error: Exception | None = None

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2)
        if self._error:
            raise self._error

    def stop(self) -> None:
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        self._thread.join(timeout=2)

    def _run(self) -> None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        u.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        u.DefWindowProcW.restype = LRESULT
        u.GetRawInputData.argtypes = (wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                      ctypes.POINTER(wintypes.UINT), wintypes.UINT)
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                      wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        buf = ctypes.create_string_buffer(ctypes.sizeof(RAWINPUT_MOUSE) + 64)

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT:
                size = wintypes.UINT(ctypes.sizeof(buf))
                if u.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), header_size) > 0:
                    raw = RAWINPUT_MOUSE.from_buffer_copy(buf)
                    m = raw.mouse
                    if raw.header.dwType == RIM_TYPEMOUSE and not (m.usFlags & MOUSE_MOVE_ABSOLUTE):
                        if m.lLastX or m.lLastY:
                            try:
                                self.callback(m.lLastX, m.lLastY)
                            except Exception:
                                pass
            elif msg == WM_CLOSE:
                u.DestroyWindow(hwnd)
                return 0
            elif msg == WM_DESTROY:
                u.PostQuitMessage(0)
                return 0
            return u.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(wndproc)  # 참조 유지 (GC 방지)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        try:
            hinst = k.GetModuleHandleW(None)
            cls_name = f"MacroToolRawInput{id(self)}"
            wc = WNDCLASSW(lpfnWndProc=proc, hInstance=hinst, lpszClassName=cls_name)
            if not u.RegisterClassW(ctypes.byref(wc)):
                raise OSError(ctypes.get_last_error(), "RegisterClassW 실패")
            hwnd = u.CreateWindowExW(0, cls_name, "", 0, 0, 0, 0, 0, HWND_MESSAGE, None, hinst, None)
            if not hwnd:
                raise OSError(ctypes.get_last_error(), "CreateWindowExW 실패")
            dev = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)  # Generic Desktop / Mouse
            if not u.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(dev)):
                u.DestroyWindow(hwnd)
                raise OSError(ctypes.get_last_error(), "RegisterRawInputDevices 실패")
            self._hwnd = hwnd
        except Exception as e:
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        dev = RAWINPUTDEVICE(0x01, 0x02, RIDEV_REMOVE, None)
        u.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(dev))
        u.UnregisterClassW(cls_name, hinst)
        self._hwnd = None
