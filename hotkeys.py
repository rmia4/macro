"""전역 핫키. 상수를 바꾸면 키가 변경된다 (이름은 keys.py 기준)."""
from __future__ import annotations

from typing import Callable

import keys

HOTKEY_RECORD = "f8"
HOTKEY_PLAY = "f9"
HOTKEY_QUIT = "f10"
CONTROL_KEYS = (HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_QUIT)  # 녹화에서 제외되는 키


class HotkeyDispatcher:
    """눌림/뗌 이벤트 -> 콜백. auto-repeat 는 무시."""

    def __init__(self, bindings: dict[str, Callable[[], None]],
                 suppress: Callable[[str], bool] | None = None) -> None:
        self.bindings = bindings  # 통째로 교체해 갱신 (스레드 안전)
        self.suppress = suppress  # True 면 무시 (예: 재생 중 매크로가 보내는 키)
        self._down: set[str] = set()

    def press(self, name: str | None) -> None:
        if name is None or name in self._down:
            return
        self._down.add(name)
        if self.suppress and name not in CONTROL_KEYS and self.suppress(name):
            return
        cb = self.bindings.get(name)
        if cb:
            cb()

    def release(self, name: str | None) -> None:
        self._down.discard(name)


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
