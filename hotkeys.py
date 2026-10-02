"""전역 핫키. 상수를 바꾸면 키가 변경된다 (이름은 keys.py 기준)."""
from __future__ import annotations

from typing import Callable

import keys

HOTKEY_RECORD = "f8"
HOTKEY_PLAY = "f9"
HOTKEY_QUIT = "f10"
CONTROL_KEYS = (HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_QUIT)  # 녹화에서 제외되는 키


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
