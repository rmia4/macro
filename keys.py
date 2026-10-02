"""키 이름 <-> 스캔코드(set 1) / Windows VK 매핑. 순수 데이터라 어느 OS에서든 import 가능."""
from __future__ import annotations

NAME_TO_SCAN: dict[str, tuple[int, bool]] = {}  # name -> (scancode, extended)


def _add(name: str, scan: int, ext: bool = False) -> None:
    NAME_TO_SCAN[name] = (scan, ext)


for _i, _c in enumerate("1234567890"):
    _add(_c, 0x02 + _i)
for _row, _start in (("qwertyuiop", 0x10), ("asdfghjkl", 0x1E), ("zxcvbnm", 0x2C)):
    for _i, _c in enumerate(_row):
        _add(_c, _start + _i)
for _i in range(10):
    _add(f"f{_i + 1}", 0x3B + _i)
_add("f11", 0x57)
_add("f12", 0x58)
for _i in range(11):
    _add(f"f{13 + _i}", 0x64 + _i)
_add("f24", 0x76)

for _n, _s in {
    "esc": 0x01, "-": 0x0C, "=": 0x0D, "backspace": 0x0E, "tab": 0x0F,
    "[": 0x1A, "]": 0x1B, "enter": 0x1C, "ctrl": 0x1D, ";": 0x27, "'": 0x28,
    "`": 0x29, "shift": 0x2A, "\\": 0x2B, ",": 0x33, ".": 0x34, "/": 0x35,
    "shift_r": 0x36, "alt": 0x38, "space": 0x39, "caps": 0x3A,
    "num_lock": 0x45, "scroll_lock": 0x46,
    "num7": 0x47, "num8": 0x48, "num9": 0x49, "num_sub": 0x4A,
    "num4": 0x4B, "num5": 0x4C, "num6": 0x4D, "num_add": 0x4E,
    "num1": 0x4F, "num2": 0x50, "num3": 0x51, "num0": 0x52, "num_dec": 0x53,
    "num_mul": 0x37,
}.items():
    _add(_n, _s)

for _n, _s in {
    "ctrl_r": 0x1D, "alt_r": 0x38, "num_div": 0x35, "print_screen": 0x37,
    "home": 0x47, "up": 0x48, "page_up": 0x49, "left": 0x4B, "right": 0x4D,
    "end": 0x4F, "down": 0x50, "insert": 0x52, "delete": 0x53,
    "win": 0x5B, "win_r": 0x5C, "menu": 0x5D,
}.items():
    _add(_n, _s, True)

VK_TO_NAME: dict[int, str] = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK_TO_NAME[ord(_c.upper())] = _c
for _c in "0123456789":
    VK_TO_NAME[ord(_c)] = _c
for _i in range(24):
    VK_TO_NAME[0x70 + _i] = f"f{_i + 1}"
for _i in range(10):
    VK_TO_NAME[0x60 + _i] = f"num{_i}"
VK_TO_NAME.update({
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x10: "shift", 0x11: "ctrl",
    0x12: "alt", 0x14: "caps", 0x1B: "esc", 0x20: "space", 0x21: "page_up",
    0x22: "page_down", 0x23: "end", 0x24: "home", 0x25: "left", 0x26: "up",
    0x27: "right", 0x28: "down", 0x2C: "print_screen", 0x2D: "insert",
    0x2E: "delete", 0x5B: "win", 0x5C: "win_r", 0x5D: "menu",
    0x6A: "num_mul", 0x6B: "num_add", 0x6D: "num_sub", 0x6E: "num_dec",
    0x6F: "num_div", 0x90: "num_lock", 0x91: "scroll_lock",
    0xA0: "shift", 0xA1: "shift_r", 0xA2: "ctrl", 0xA3: "ctrl_r",
    0xA4: "alt", 0xA5: "alt_r",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
})
_add("page_down", 0x51, True)


def is_known(name: str) -> bool:
    return name in NAME_TO_SCAN


def name_from_pynput(key) -> str | None:
    """pynput Key/KeyCode -> 내부 키 이름. 알 수 없으면 None."""
    vk = getattr(key, "vk", None)
    if vk is None:
        vk = getattr(getattr(key, "value", None), "vk", None)
    if vk in VK_TO_NAME:
        return VK_TO_NAME[vk]
    name = getattr(key, "name", None)
    if name in NAME_TO_SCAN:
        return name
    char = getattr(key, "char", None)
    if char and char.lower() in NAME_TO_SCAN:
        return char.lower()
    return None


# ---- 핫키 (1~2키 조합) ----
HOTKEY_ALIASES = {"ctrl_r": "ctrl", "shift_r": "shift", "alt_r": "alt", "win_r": "win",
                  "control": "ctrl"}  # 핫키에서는 좌/우 수정키를 구분하지 않는다
MODIFIERS = ("ctrl", "shift", "alt", "win")
MAX_HOTKEY_KEYS = 2


def hotkey_key(name: str) -> str:
    return HOTKEY_ALIASES.get(name, name)


def format_hotkey(parts) -> str:
    """수정키(ctrl, shift, alt, win)를 앞에 두고 '+'로 잇는다."""
    parts = list(dict.fromkeys(hotkey_key(p) for p in parts))
    mods = [m for m in MODIFIERS if m in parts]
    return "+".join(mods + [p for p in parts if p not in MODIFIERS])


def parse_hotkey(text: str | None) -> str | None:
    """'Ctrl + F1' -> 'ctrl+f1'. 비어 있으면 None, 잘못되면 ValueError."""
    if text is None or not str(text).strip():
        return None
    parts = [p.strip().lower() for p in str(text).split("+")]
    if any(not p for p in parts):
        raise ValueError(f"잘못된 핫키 형식: {text!r}")
    for p in parts:
        if not is_known(hotkey_key(p)):
            raise ValueError(f"알 수 없는 키: {p!r}")
    result = format_hotkey(parts)
    if len(result.split("+")) > MAX_HOTKEY_KEYS:
        raise ValueError(f"핫키는 최대 {MAX_HOTKEY_KEYS}개 키 조합까지 가능합니다")
    return result


def hotkey_parts(hotkey: str) -> list[str]:
    return hotkey.split("+")


_TK_KEYSYMS = {
    "control_l": "ctrl", "control_r": "ctrl", "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt", "super_l": "win", "super_r": "win", "win_l": "win", "win_r": "win",
    "return": "enter", "kp_enter": "enter", "escape": "esc", "backspace": "backspace", "tab": "tab",
    "space": "space", "up": "up", "down": "down", "left": "left", "right": "right",
    "prior": "page_up", "next": "page_down", "home": "home", "end": "end", "insert": "insert",
    "delete": "delete", "caps_lock": "caps", "num_lock": "num_lock", "scroll_lock": "scroll_lock",
    "menu": "menu", "app": "menu", "print": "print_screen", "minus": "-", "equal": "=",
    "bracketleft": "[", "bracketright": "]", "backslash": "\\", "semicolon": ";", "apostrophe": "'",
    "grave": "`", "comma": ",", "period": ".", "slash": "/", "kp_add": "num_add",
    "kp_subtract": "num_sub", "kp_multiply": "num_mul", "kp_divide": "num_div", "kp_decimal": "num_dec",
}


def name_from_tk(keysym: str, keycode: int | None = None, windows: bool = False) -> str | None:
    """tkinter 키 이벤트 -> 내부 키 이름. Windows 에서는 keycode 가 VK 코드다."""
    if windows and keycode in VK_TO_NAME:
        return VK_TO_NAME[keycode]
    k = (keysym or "").lower()
    if k in _TK_KEYSYMS:
        return _TK_KEYSYMS[k]
    if k.startswith("kp_") and k[3:].isdigit():
        return "num" + k[3:]
    return k if k in NAME_TO_SCAN else None
