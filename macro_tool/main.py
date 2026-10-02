"""진입점: 핫키 + REPL. 사용법은 README.md 참고."""
from __future__ import annotations

import dataclasses
import os
import shlex
import sys
import threading
from pathlib import Path

import input_backend
from hotkeys import CONTROL_KEYS, HOTKEY_PLAY, HOTKEY_QUIT, HOTKEY_RECORD, HotkeyListener
from player import PlayOptions, Player, set_option
from profiles import Macro, MacroFormatError, list_macros, load_macro, macro_path, save_macro
from recorder import Recorder

MACROS_DIR = Path(__file__).resolve().parent / "macros"

HELP = """명령: record | play | stop | save <이름> | load <이름> | list | set <옵션> <값> | show | quit
핫키: {rec}=녹화 시작/종료, {play}=재생/중지, {quit}=종료""".format(
    rec=HOTKEY_RECORD.upper(), play=HOTKEY_PLAY.upper(), quit=HOTKEY_QUIT.upper())


class App:
    def __init__(self, backend, macros_dir: Path = MACROS_DIR, log=print,
                 recorder_factory=Recorder, player_factory=Player) -> None:
        self.backend = backend
        self.macros_dir = Path(macros_dir)
        self.log = log
        self.options = PlayOptions()
        self.macro: Macro | None = None
        self._recorder_factory = recorder_factory
        self._player_factory = player_factory
        self._recorder = None
        self._player = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---- 상태 ----
    @property
    def recording(self) -> bool:
        return self._recorder is not None

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- 녹화 / 재생 ----
    def toggle_record(self) -> None:
        with self._lock:
            if self.playing:
                self.log("재생 중에는 녹화할 수 없습니다")
                return
            if self._recorder is None:
                rec = self._recorder_factory(self.backend, self.options.window_title, CONTROL_KEYS)
                space = rec.start()
                self._recorder = rec
                self.log(f"● 녹화 시작 (좌표계: {space})")
            else:
                rec, self._recorder = self._recorder, None
                self.macro = rec.stop()
                self.log(f"■ 녹화 종료: 이벤트 {len(self.macro.events)}개, {self.macro.duration:.2f}초")

    def toggle_play(self) -> None:
        with self._lock:
            if self.playing:
                self._player.stop()
                self.log("재생 중지 요청")
                return
            if self.recording:
                self.log("녹화 중에는 재생할 수 없습니다")
                return
            if self.macro is None or not self.macro.events:
                self.log("재생할 매크로가 없습니다 (record 또는 load)")
                return
            self._player = self._player_factory(self.backend, self.options, log=self.log)
            macro = self.macro
            self._thread = threading.Thread(target=self._play, args=(macro,), daemon=True)
            self._thread.start()
            self.log("▶ 재생 시작")

    def _play(self, macro: Macro) -> None:
        try:
            self._player.run(macro)
        except Exception as e:  # 입력은 Player 의 finally 에서 이미 해제됨
            self.log(f"재생 오류: {e}")
        self.log("■ 재생 종료")

    def stop_all(self) -> None:
        if self.playing:
            self._player.stop()
            self._thread.join(timeout=2)
        if self.recording:
            self.toggle_record()

    def shutdown(self) -> None:
        self.stop_all()
        input_backend.shutdown_process()

    # ---- REPL ----
    def handle(self, line: str) -> bool:
        """명령 한 줄 처리. 종료해야 하면 False."""
        try:
            parts = shlex.split(line, posix=False)
        except ValueError as e:
            self.log(f"입력 오류: {e}")
            return True
        if not parts:
            return True
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("quit", "exit"):
                return False
            elif cmd == "record":
                self.toggle_record()
            elif cmd in ("play", "stop"):
                if (cmd == "play") != self.playing:
                    self.toggle_play()
            elif cmd == "save":
                self._need(args, 1, "save <이름>")
                if self.macro is None:
                    raise ValueError("저장할 매크로가 없습니다")
                self.log(f"저장: {save_macro(self.macro, macro_path(self.macros_dir, args[0]))}")
            elif cmd == "load":
                self._need(args, 1, "load <이름>")
                self.macro = load_macro(macro_path(self.macros_dir, args[0]))
                self.log(f"불러옴: 이벤트 {len(self.macro.events)}개, {self.macro.duration:.2f}초")
                if self.macro.window and not self.options.window_title:
                    self.log(f"힌트: 녹화 창 제목은 {self.macro.window.get('title')!r} "
                             "(set window_title 로 포커스 제한 설정)")
            elif cmd == "list":
                names = list_macros(self.macros_dir)
                self.log("\n".join(names) if names else "(저장된 매크로 없음)")
            elif cmd == "set":
                self._need(args, 2, "set <옵션> <값>", exact=False)
                set_option(self.options, args[0], " ".join(args[1:]).strip('"'))
                self.log(f"{args[0]} = {getattr(self.options, args[0])!r}")
            elif cmd == "show":
                self._show()
            elif cmd == "help":
                self.log(HELP)
            else:
                self.log(f"알 수 없는 명령: {cmd} (help)")
        except (ValueError, OSError, MacroFormatError) as e:
            self.log(f"오류: {e}")
        return True

    @staticmethod
    def _need(args, n, usage, exact=True) -> None:
        if len(args) < n or (exact and len(args) > n):
            raise ValueError(f"사용법: {usage}")

    def _show(self) -> None:
        for k, v in dataclasses.asdict(self.options).items():
            self.log(f"  {k} = {v!r}")
        if self.macro:
            m = self.macro
            self.log(f"매크로: 이벤트 {len(m.events)}개, {m.duration:.2f}초, 좌표계 {m.coord_space}")
        else:
            self.log("매크로: (없음)")


def main() -> int:
    if not input_backend.IS_WINDOWS:
        print("이 도구는 Windows 전용입니다.")
        return 1
    input_backend.init_process()  # pynput 보다 먼저 DPI 인식 설정
    if not input_backend.is_admin():
        print("[경고] 관리자 권한이 아닙니다. 게임이 관리자 권한으로 실행 중이면 "
              "입력이 무시되므로 이 도구도 관리자 권한으로 실행하세요.")
    app = App(input_backend.WindowsBackend())

    def quit_now() -> None:
        app.shutdown()
        os._exit(0)  # input() 에서 대기 중인 REPL 을 즉시 종료

    hotkeys = HotkeyListener({HOTKEY_RECORD: app.toggle_record,
                              HOTKEY_PLAY: app.toggle_play,
                              HOTKEY_QUIT: quit_now})
    hotkeys.start()
    print(HELP)
    try:
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not app.handle(line):
                break
    finally:
        hotkeys.stop()
        app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
