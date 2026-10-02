"""진입점: 기본은 GUI, `--cli` 로 REPL. 사용법은 README.md 참고."""
from __future__ import annotations

import dataclasses
import os
import shlex
import sys
import threading
from pathlib import Path

import input_backend
from hotkeys import CONTROL_KEYS, HOTKEY_PLAY, HOTKEY_QUIT, HOTKEY_RECORD, HotkeyListener
from player import PlayOptions, Player, options_from_dict, options_to_dict, set_option
from profiles import Macro, delete_macro, list_macros, load_macro, macro_path, save_macro
from recorder import Recorder

MACROS_DIR = Path(__file__).resolve().parent / "macros"

HELP = """명령: record | play | stop | save <이름> | load <이름> | delete <이름> | list | set <옵션> <값> | show | quit
핫키: {rec}=녹화 시작/종료, {play}=재생/중지, {quit}=종료""".format(
    rec=HOTKEY_RECORD.upper(), play=HOTKEY_PLAY.upper(), quit=HOTKEY_QUIT.upper())


class App:
    def __init__(self, backend, macros_dir: Path = MACROS_DIR, log=print,
                 recorder_factory=Recorder, player_factory=Player) -> None:
        self.backend = backend
        self.macros_dir = Path(macros_dir)
        self.log = log
        self.options = PlayOptions()        # REPL 용 현재 옵션
        self.macro: Macro | None = None     # REPL 용 현재 매크로
        self.macro_name: str | None = None  # 저장/불러온 이름 (새 녹화는 None)
        self.library: dict[str, Macro] = {}  # 저장된 매크로 (GUI 목록)
        self.playing_name: str | None = None
        self.playing_macro: Macro | None = None
        self.playing_keys: frozenset[str] = frozenset()
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

    @property
    def progress(self) -> tuple[int, int, int] | None:
        """재생 중이면 (루프 번호, 마지막 이벤트 인덱스, 반복 횟수 0=무한)."""
        p = self._player
        if p is None or not self.playing:
            return None
        return p.loop_index, p.event_index, getattr(p, "opt", p.options).repeat

    # ---- 녹화 ----
    def start_record(self, window_title: str = "", coord_space: str | None = None,
                     ignore_keys=CONTROL_KEYS) -> str:
        with self._lock:
            if self.playing:
                raise RuntimeError("재생 중에는 녹화할 수 없습니다")
            if self._recorder is not None:
                raise RuntimeError("이미 녹화 중입니다")
            rec = self._recorder_factory(self.backend, window_title, tuple(ignore_keys),
                                         coord_space=coord_space)
            space = rec.start()
            self._recorder = rec
        self.log(f"● 녹화 시작 (좌표계: {space})")
        return space

    def stop_record(self) -> Macro:
        with self._lock:
            rec, self._recorder = self._recorder, None
        if rec is None:
            raise RuntimeError("녹화 중이 아닙니다")
        macro = rec.stop()
        self.log(f"■ 녹화 종료: 이벤트 {len(macro.events)}개, {macro.duration:.2f}초")
        return macro

    def toggle_record(self) -> None:
        """REPL 용: 녹화 결과를 현재 매크로로 둔다."""
        if self.recording:
            self.macro = self.stop_record()
            self.macro_name = None
        else:
            self.start_record(self.options.window_title)

    # ---- 재생 ----
    def start_play(self, macro: Macro, options: PlayOptions, name: str | None = None) -> None:
        with self._lock:
            if self.playing:
                raise RuntimeError("이미 재생 중입니다")
            if self.recording:
                raise RuntimeError("녹화 중에는 재생할 수 없습니다")
            if not macro.events:
                raise ValueError("이벤트가 없습니다")
            player = self._player_factory(self.backend, options, log=self.log)
            self._player = player
            self.playing_macro, self.playing_name = macro, name
            self.playing_keys = frozenset(ev["key"] for ev in macro.events if "key" in ev)
            self._thread = threading.Thread(target=self._play, args=(player, macro), daemon=True)
            self._thread.start()
        self.log(f"▶ 재생 시작{f': {name}' if name else ''}")

    def stop_play(self) -> None:
        if self.playing:
            self._player.stop()
            self.log("재생 중지 요청")

    def toggle_play(self) -> None:
        """REPL 용: 현재 매크로를 현재 옵션으로 재생/중지."""
        if self.playing:
            self.stop_play()
        elif self.macro is None or not self.macro.events:
            raise ValueError("재생할 매크로가 없습니다 (record 또는 load)")
        else:
            self.start_play(self.macro, self.options, self.macro_name)

    def _play(self, player, macro: Macro) -> None:
        try:
            player.run(macro)
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

    # ---- 매크로 라이브러리 (GUI) ----
    def reload_library(self) -> None:
        lib = {}
        for name in list_macros(self.macros_dir):
            try:
                macro = load_macro(macro_path(self.macros_dir, name))
                options_from_dict(macro.options)  # 옵션 검증
                lib[name] = macro
            except (OSError, ValueError) as e:  # MacroFormatError 포함
                self.log(f"불러오기 실패 '{name}': {e}")
        self.library = lib

    def store(self, name: str, macro: Macro, old_name: str | None = None) -> str:
        """라이브러리에 저장. 이름이 바뀌면 이전 파일을 지운다. 저장된 이름을 반환."""
        path = macro_path(self.macros_dir, name)
        name = path.stem
        if name != old_name and path.exists():
            raise ValueError(f"같은 이름의 매크로가 이미 있습니다: {name}")
        save_macro(macro, path)
        self.library[name] = macro
        if old_name and old_name != name:
            self.library.pop(old_name, None)
            try:
                delete_macro(self.macros_dir, old_name)
            except (ValueError, OSError):
                pass
        self.log(f"저장: {name}")
        return name

    # ---- 저장 / 불러오기 / 삭제 (REPL, GUI 공용) ----
    def save(self, name: str) -> None:
        if self.macro is None:
            raise ValueError("저장할 매크로가 없습니다")
        self.macro.options = options_to_dict(self.options)
        path = save_macro(self.macro, macro_path(self.macros_dir, name))
        self.macro_name = path.stem
        self.library[path.stem] = self.macro
        self.log(f"저장: {path}")

    def load(self, name: str) -> None:
        macro = load_macro(macro_path(self.macros_dir, name))
        if macro.options:
            self.options = options_from_dict(macro.options)
        self.macro = macro
        self.macro_name = macro_path(self.macros_dir, name).stem
        self.log(f"불러옴: 이벤트 {len(macro.events)}개, {macro.duration:.2f}초")
        if macro.window and not self.options.window_title:
            self.log(f"힌트: 녹화 창 제목은 {macro.window.get('title')!r} "
                     "(window_title 로 포커스 제한 설정)")

    def delete(self, name: str) -> None:
        path = delete_macro(self.macros_dir, name)
        self.library.pop(path.stem, None)
        if self.macro_name == path.stem:
            self.macro_name = None  # 메모리의 매크로는 유지 (다시 저장 가능)
        self.log(f"삭제: {path}")

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
                self.save(args[0])
            elif cmd == "load":
                self._need(args, 1, "load <이름>")
                self.load(args[0])
            elif cmd == "delete":
                self._need(args, 1, "delete <이름>")
                self.delete(args[0])
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
        except (ValueError, OSError, RuntimeError) as e:
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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not input_backend.IS_WINDOWS:
        print("이 도구는 Windows 전용입니다.")
        return 1
    input_backend.init_process()  # pynput 보다 먼저 DPI 인식 설정
    if not input_backend.is_admin():
        print("[경고] 관리자 권한이 아닙니다. 게임이 관리자 권한으로 실행 중이면 "
              "입력이 무시되므로 이 도구도 관리자 권한으로 실행하세요.")
    if "--cli" not in argv:
        from gui import run_gui
        return run_gui(App(input_backend.WindowsBackend()))
    app = App(input_backend.WindowsBackend())

    def quit_now() -> None:
        app.shutdown()
        os._exit(0)  # input() 에서 대기 중인 REPL 을 즉시 종료

    def safe(fn):
        def run() -> None:
            try:
                fn()
            except Exception as e:  # 리스너 스레드가 죽지 않도록
                print(f"오류: {e}")
        return run

    hotkeys = HotkeyListener({HOTKEY_RECORD: safe(app.toggle_record),
                              HOTKEY_PLAY: safe(app.toggle_play),
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
