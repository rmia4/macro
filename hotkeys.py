"""전역 핫키 (이름은 keys.py 기준). 녹화·재생·종료·캡처 키는 설정에서 바꿀 수 있다 (set_control_keys).

바뀔 수 있으니 다른 모듈은 `from hotkeys import HOTKEY_PLAY` 처럼 값을 복사하지 말고
`hotkeys.HOTKEY_PLAY` 로 쓸 때마다 읽는다."""
from __future__ import annotations

from typing import Callable

import keys

HOTKEY_RECORD = "f8"
HOTKEY_PLAY = "f9"
HOTKEY_QUIT = "f10"
HOTKEY_CAPTURE = "f7"  # AI 로 만들기의 화면 캡처 모드에서만 (끝은 HOTKEY_RECORD)
CONTROL_KEYS = (HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_QUIT)  # 녹화에서 제외되는 키
DEFAULT_RECORD, DEFAULT_PLAY, DEFAULT_QUIT, DEFAULT_CAPTURE = HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_QUIT, HOTKEY_CAPTURE
CONTROL_LABELS = {"capture": "AI 화면 캡처", "record": "녹화 시작/종료", "play": "재생/중지", "quit": "프로그램 종료"}


def control_key_error(record: str, play: str, quit: str, capture: str) -> str | None:
    """녹화·재생·종료·캡처 키 검사 (각각 한 키, 수정키 아님, 서로 다름). 문제 없으면 None."""
    parsed = {}
    for which, key in (("capture", capture), ("record", record), ("play", play), ("quit", quit)):
        label = CONTROL_LABELS[which]
        try:
            value = keys.parse_hotkey(key)
        except ValueError as e:
            return f"{label} 키: {e}"
        if value is None:
            return f"{label} 키를 지정하세요"
        if "+" in value:
            return f"{label} 키는 한 키만 지정할 수 있습니다"
        if value in keys.MODIFIERS:
            return f"{label} 키로 {value.upper()} 같은 수정키는 쓸 수 없습니다"
        for other, used in parsed.items():
            if used == value:
                return f"{CONTROL_LABELS[other]} 키와 {label} 키가 같습니다 ({value.upper()})"
        parsed[which] = value
    return None


def set_control_keys(record: str, play: str, quit: str, capture: str) -> None:
    """녹화·재생·종료·캡처 키 변경 (control_key_error 로 검사한 값)."""
    global HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_QUIT, HOTKEY_CAPTURE, CONTROL_KEYS
    HOTKEY_RECORD, HOTKEY_PLAY = keys.parse_hotkey(record), keys.parse_hotkey(play)
    HOTKEY_QUIT, HOTKEY_CAPTURE = keys.parse_hotkey(quit), keys.parse_hotkey(capture)
    CONTROL_KEYS = (HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_QUIT)


class HotkeyDispatcher:
    """눌림/뗌 이벤트 -> 콜백. 바인딩 키는 'f6' 또는 'ctrl+f1' 같은 1~2키 조합.

    조합의 모든 키가 눌린 상태에서 그중 한 키가 새로 눌리면 실행된다 (수정키 조합은 수정키를
    누른 채 일반 키를 눌러야 한다: Ctrl 누른 채 F1). 여러 조합이 맞으면
    키가 더 많은 쪽만 실행한다 (Ctrl+F1 을 누르면 F1 단독 핫키는 실행되지 않음).
    다른 키(예: 이동 중인 W)를 누르고 있어도 단일 키 핫키는 동작한다. auto-repeat 는 무시.
    """

    def __init__(self, bindings: dict[str, Callable[[], None]],
                 suppress: Callable[[str], bool] | None = None) -> None:
        self.bindings = bindings  # 통째로 교체해 갱신 (스레드 안전)
        self.suppress = suppress  # True 면 무시 (예: 재생 중 매크로가 보내는 키)
        self._down: set[str] = set()

    def press(self, name: str | None) -> None:
        if name is None:
            return
        name = keys.hotkey_key(name)
        if name in self._down:
            return
        self._down.add(name)
        if self.suppress and name not in CONTROL_KEYS and self.suppress(name):
            return
        best = None
        for combo, cb in list(self.bindings.items()):
            parts = keys.hotkey_parts(combo)
            # 수정키 조합(ctrl+f1)은 일반 키를 누를 때만, 일반 키끼리(a+s)는 어느 쪽이 나중이든 실행
            triggers = [p for p in parts if p not in keys.MODIFIERS] or parts
            if name in triggers and all(p in self._down for p in parts):
                if best is None or len(parts) > best[0]:
                    best = (len(parts), cb)
        if best:
            best[1]()

    def release(self, name: str | None) -> None:
        if name is not None:
            self._down.discard(keys.hotkey_key(name))


class HotkeyListener:
    def __init__(self, bindings: dict[str, Callable[[], None]],
                 suppress: Callable[[str], bool] | None = None) -> None:
        self.dispatcher = HotkeyDispatcher(bindings, suppress)
        self._listener = None

    def start(self) -> None:
        from pynput import keyboard

        self._listener = keyboard.Listener(
            on_press=lambda k: self.dispatcher.press(keys.name_from_pynput(k)),
            on_release=lambda k: self.dispatcher.release(keys.name_from_pynput(k)))
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
