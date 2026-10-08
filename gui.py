"""tkinter GUI. 로직은 main.App / editor_model 에 위임하고 화면 표시/입력만 담당한다.

- 메인 화면(Gui): 매크로 목록, 선택 재생/중지, 진행 상황, 추가/편집/복제/삭제
- 기록 화면(EditorWindow): 녹화, 이벤트 목록 개별 수정, 이벤트 추가, 핫키·반복 등 설정
- 이벤트 대화상자(EventDialog): 이벤트 하나(또는 클릭/키 입력 쌍) 추가·수정
"""
from __future__ import annotations

import copy
import queue
import shutil
import sys
import tempfile
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import editor_model as em
import input_backend
import keys
from hotkeys import CONTROL_KEYS, HOTKEY_PLAY, HOTKEY_QUIT, HOTKEY_RECORD, HotkeyListener
from main import App
from paths import resource_path
from player import PlayOptions, options_from_dict, options_to_dict, set_option
from profiles import BUTTONS, Macro
from settings import OVERLAY_POSITIONS, SETTINGS_PATH, Settings
import vision
from pathlib import Path

# (옵션 이름, 라벨, 종류)  종류: entry | check
PLAY_FIELDS = [
    ("repeat", "반복 횟수 (0=무한)", "entry"),
    ("speed", "속도 배율", "entry"),
    ("loop_delay", "반복 간 대기(초)", "entry"),
    ("time_jitter", "대기 시간 편차 ±%", "entry"),
    ("pos_jitter", "클릭 좌표 편차 ±px", "entry"),
    ("min_key_hold", "키 최소 유지(초)", "entry"),
    ("approach", "루프 시작 시 부드럽게 이동", "check"),
    ("approach_duration", "이동 최대 시간(초)", "entry"),
    ("scale_coords", "창 크기에 맞춰 좌표 조정", "check"),
    ("max_minutes", "최대 실행 시간(분, 0=무제한)", "entry"),
]
UNDO_LIMIT = 100
OVERLAY_LABELS = {"off": "끄기", "nw": "↖ 왼쪽 위", "n": "↑ 위 가운데", "ne": "↗ 오른쪽 위",
                  "w": "← 왼쪽 가운데", "e": "→ 오른쪽 가운데", "sw": "↙ 왼쪽 아래",
                  "s": "↓ 아래 가운데", "se": "↘ 오른쪽 아래"}
FLASH_SECONDS = 2.0
COLORS = {"idle": "#555555", "rec": "#c62828", "play": "#2e7d32", "wait": "#ef6c00"}
NO_HOTKEY = "(없음)"
COORD_LABELS = {"screen": "화면 기준", "window": "창 기준"}


class Gui:
    """메인 화면: 매크로 목록과 재생."""

    def __init__(self, root: tk.Tk, app: App, settings: Settings | None = None) -> None:
        self.root, self.app = root, app
        root.iconbitmap(default=str(resource_path("icon.ico")))
        self.settings = settings or Settings(None)
        self._logs: queue.Queue[str] = queue.Queue()
        self._calls: queue.Queue = queue.Queue()  # 다른 스레드(핫키) -> Tk 스레드
        self._countdown_id = None
        self._countdown_owner: str | None = None  # "main" | "editor"
        self._countdown_left = 0
        self._countdown_what = ""
        self.macros_enabled = bool(self.settings["macros_enabled"])  # 전체 매크로 실행 가능 여부
        self._flash_until = 0.0  # 전체 실행 전환 직후 오버레이에 잠깐 상태 표시
        self._notice: tuple[str, float] | None = None  # 오버레이 안내 (문구, 만료 시각)
        self.grabber = None  # 화면 캡처. None 이면 처음 쓸 때 MssGrabber 생성 (테스트에서 주입)
        self._after_id = None
        self._closed = False
        self.play_started: float | None = None
        self.editor: EditorWindow | None = None
        self.hotkey_listener: HotkeyListener | None = None
        app.log = self._logs.put
        self.delay = tk.StringVar(root, value=str(self.settings["start_delay"]))
        root.title("매크로 도구")
        root.minsize(760, 520)
        restore_geometry(root, self.settings["main_geometry"])
        self.overlay = Overlay(root, self.settings["overlay_position"])
        self._build()
        self.reload()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll()

    # ---- 화면 구성 ----
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.status = tk.Label(top, text="대기 중", fg="white", bg=COLORS["idle"],
                               font=("", 13, "bold"), width=14, pady=6)
        self.status.pack(side="left")
        self.btn_play = ttk.Button(top, text=f"▶ 재생 ({HOTKEY_PLAY.upper()})", command=self.on_play)
        self.btn_stop = ttk.Button(top, text="■ 중지", command=self.on_stop)
        self.btn_play.pack(side="left", padx=(8, 4))
        self.btn_stop.pack(side="left")
        ttk.Label(top, text="버튼 시작 지연(초)").pack(side="left", padx=(16, 2))
        ttk.Spinbox(top, from_=0, to=30, width=4, textvariable=self.delay).pack(side="left")

        opts = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        opts.pack(fill="x")
        self.btn_power = tk.Button(opts, command=self.toggle_macros_enabled, width=28, pady=3,
                                   fg="white", relief="raised", font=("", 10, "bold"))
        self.btn_power.pack(side="left")
        self._update_power_button()
        ttk.Button(opts, text="단축키 변경", command=self.change_toggle_hotkey).pack(side="left", padx=4)
        self.v_overlay = tk.StringVar(self.root, value=OVERLAY_LABELS[self.settings["overlay_position"]])
        overlay_box = ttk.Combobox(opts, textvariable=self.v_overlay, state="readonly", width=14,
                                   values=[OVERLAY_LABELS[p] for p in OVERLAY_POSITIONS])
        overlay_box.pack(side="right")
        overlay_box.bind("<<ComboboxSelected>>", lambda e: self.set_overlay_position(self._overlay_key()))
        ttk.Label(opts, text="상태 오버레이 위치").pack(side="right", padx=4)

        prog = ttk.Frame(self.root, padding=(8, 0))
        prog.pack(fill="x")
        self.progress = ttk.Progressbar(prog, maximum=100, length=240)
        self.progress.pack(side="left")
        self.progress_label = ttk.Label(prog, text="재생 대기")
        self.progress_label.pack(side="left", padx=8)

        box = ttk.LabelFrame(self.root, text="매크로 목록", padding=6)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        frame = ttk.Frame(box)
        frame.pack(fill="both", expand=True)
        cols = ("name", "hotkey", "repeat", "events", "duration", "window")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=10, selectmode="browse")
        for col, text, width in zip(cols, ("이름", "시작 핫키", "반복", "이벤트", "길이(초)", "대상 창"),
                                    (180, 100, 60, 70, 80, 170)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w", stretch=col in ("name", "window"))
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<Double-Button-1>", lambda e: self.on_edit())
        self.tree.bind("<Return>", lambda e: self.on_play())
        self.tree.bind("<Delete>", lambda e: self.on_delete())

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        self.macro_settings_buttons = []
        for text, cmd in (("+ 추가", self.on_add), ("편집", self.on_edit), ("복제", self.on_duplicate),
                          ("삭제", self.on_delete), ("새로고침", self.reload)):
            button = ttk.Button(row, text=text, command=cmd)
            button.pack(side="left", padx=(0, 4))
            self.macro_settings_buttons.append(button)
        self._update_macro_settings_buttons()

        ttk.Label(self.root, foreground="#666", padding=(8, 0), wraplength=740,
                  text=f"{HOTKEY_PLAY.upper()}: 선택한 매크로 재생/중지 · 매크로별 시작 핫키: 그 매크로 재생/중지 · "
                       f"{HOTKEY_RECORD.upper()}: 기록 화면에서 녹화 시작/종료 · {HOTKEY_QUIT.upper()}: 종료").pack(fill="x")
        self.log_box = scrolledtext.ScrolledText(self.root, height=7, state="disabled")
        self.log_box.pack(fill="both", padx=8, pady=8)

    # ---- 목록 ----
    def reload(self) -> None:
        self.app.reload_library()
        self.refresh_list()

    def refresh_list(self, select: str | None = None) -> None:
        keep = select or self.selected()
        self.tree.delete(*self.tree.get_children())
        for name in sorted(self.app.library, key=str.lower):
            m = self.app.library[name]
            opts = m.options or {}
            repeat = opts.get("repeat", 1)
            self.tree.insert("", "end", iid=name, values=(
                name, (m.hotkey or "-").upper(), "∞" if repeat == 0 else repeat, len(m.events),
                f"{m.duration:.2f}", opts.get("window_title") or "-"))
        if keep in self.app.library:
            self.tree.selection_set(keep)
            self.tree.see(keep)
        self.sync_hotkeys()

    def selected(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    # ---- 공통 ----
    def log(self, msg: str) -> None:
        self._logs.put(msg)

    @property
    def counting_down(self) -> bool:
        return self._countdown_id is not None

    def countdown_for(self, owner: str) -> tuple[int, str] | None:
        """owner 가 시작한 카운트다운이면 (남은 초, 무엇)."""
        if self._countdown_id is None or self._countdown_owner != owner:
            return None
        return self._countdown_left, self._countdown_what

    def cancel_countdown(self) -> bool:
        if self._countdown_id is None:
            return False
        self.root.after_cancel(self._countdown_id)
        owner, self._countdown_id, self._countdown_owner = self._countdown_owner, None, None
        if owner == "main":
            self.log("시작 취소")
        return True

    def start_after_delay(self, action, owner: str = "main", what: str = "재생") -> None:
        """버튼으로 시작할 때 지연 후 실행. 상태는 owner 화면(main/editor)에 표시된다."""
        try:
            delay = max(0, int(float(self.delay.get())))
        except ValueError:
            delay = 3
        self._countdown_owner, self._countdown_what = owner, what

        def tick(n: int) -> None:
            if n == 0:
                self._countdown_id = self._countdown_owner = None
                action()
                return
            self._countdown_left = n
            self._countdown_id = self.root.after(1000, tick, n - 1)

        tick(delay)

    def guard(self, fn) -> bool:
        try:
            fn()
            return True
        except Exception as e:
            self.log(f"오류: {e}")
            return False

    # ---- 재생 ----
    def play_macro(self, name: str, immediate: bool = False) -> None:
        macro = self.app.library.get(name)
        if macro is None:
            self.log(f"매크로가 없습니다: {name}")
            return
        if not self.macros_enabled:
            self.log("매크로 실행이 꺼져 있습니다 (오른쪽 위 버튼으로 켜기)")
            return
        try:
            opts = options_from_dict(macro.options)
        except ValueError as e:
            self.log(f"'{name}' 옵션 오류: {e}")
            return
        start = lambda: self.guard(lambda: self.app.start_play(macro, opts, name))
        start() if immediate else self.start_after_delay(start)

    def on_play(self, immediate: bool = False) -> None:
        if self.cancel_countdown():
            return
        if self.app.playing:
            self.app.stop_play()
            return
        if not self.macros_enabled:
            self.log("매크로 실행이 꺼져 있습니다 (오른쪽 위 버튼으로 켜기)")
            return
        name = self.selected()
        if name is None:
            if immediate:
                self.log("재생할 매크로를 목록에서 선택하세요")
            else:
                messagebox.showinfo("재생", "재생할 매크로를 목록에서 선택하세요", parent=self.root)
            return
        self.play_macro(name, immediate)

    def toggle_macro(self, name: str) -> None:
        """매크로별 핫키: 재생 중이면 중지, 아니면 그 매크로 즉시 재생."""
        if self.cancel_countdown():
            return
        if self.app.playing:
            self.app.stop_play()
        else:
            self.play_macro(name, immediate=True)

    def on_stop(self) -> None:
        if self.cancel_countdown():
            return
        if self.app.playing:
            self.app.stop_play()

    # ---- 추가 / 편집 / 복제 / 삭제 ----
    def open_editor(self, name: str | None) -> None:
        if self.editor is not None:
            self.editor.show()
            self.log("기록 화면이 이미 열려 있습니다. 먼저 닫아 주세요.")
            return
        self.editor = EditorWindow(self, name)

    def on_add(self) -> None:
        if self.macros_enabled:
            return
        self.open_editor(None)

    def on_edit(self) -> None:
        if self.macros_enabled:
            return
        name = self.selected()
        if name is not None:
            self.open_editor(name)

    def on_duplicate(self) -> None:
        if self.macros_enabled:
            return
        name = self.selected()
        if name is None:
            return
        dup = Macro.from_dict(copy.deepcopy(self.app.library[name].to_dict()))
        dup.hotkey = None  # 핫키는 중복될 수 없다
        new = em.unique_name(f"{name} 복사", self.app.library)
        if self.guard(lambda: self.app.store(new, dup, assets=self.app.asset_files(name))):
            self.refresh_list(select=new)

    def on_delete(self) -> None:
        if self.macros_enabled:
            return
        name = self.selected()
        if name is None:
            return
        if self.app.playing and self.app.playing_name == name:
            messagebox.showinfo("삭제", "재생 중인 매크로는 삭제할 수 없습니다", parent=self.root)
            return
        if not messagebox.askyesno("삭제 확인", f"'{name}' 매크로를 삭제할까요?\n되돌릴 수 없습니다.",
                                   parent=self.root):
            return
        self.guard(lambda: self.app.delete(name))
        if self.editor is not None and self.editor.old_name == name:
            self.editor.old_name = None  # 편집 중인 내용은 새 매크로로 저장된다
        self.refresh_list()

    def toggle_macros_enabled(self) -> None:
        """전체 매크로 실행 가능/불가 전환. 끄면 재생 중인 매크로도 멈춘다."""
        self.macros_enabled = not self.macros_enabled
        if not self.macros_enabled:
            if self.countdown_for("main"):
                self.cancel_countdown()
            self.app.stop_play()
        self._update_power_button()
        self._update_macro_settings_buttons()
        self._flash_until = time.monotonic() + FLASH_SECONDS
        self.settings["macros_enabled"] = self.macros_enabled
        self.settings.save()
        self.log(f"매크로 실행 {'가능' if self.macros_enabled else '불가'} 상태")

    def _overlay_key(self) -> str:
        label = self.v_overlay.get()
        return next(k for k, v in OVERLAY_LABELS.items() if v == label)

    def set_overlay_position(self, position: str) -> None:
        self.overlay.set_position(position)
        self.v_overlay.set(OVERLAY_LABELS[position])
        self.settings["overlay_position"] = position
        self.settings.save()
        if position != "off":
            self._flash_until = time.monotonic() + FLASH_SECONDS  # 위치 확인용으로 잠깐 표시

    def overlay_state(self) -> tuple[str, str] | None:
        """오버레이에 보여줄 (문구, 색상). 보여줄 게 없으면 None."""
        if self._notice and time.monotonic() < self._notice[1]:
            return self._notice[0], COLORS["wait"]
        cd = self.countdown_for("main") or self.countdown_for("editor")
        if cd:
            return f"{cd[0]}초 후 {cd[1]}", COLORS["wait"]
        if self.app.recording:
            return f"● 녹화 중 ({HOTKEY_RECORD.upper()} 종료)", COLORS["rec"]
        if self.app.playing:
            return self.overlay_play_text(), COLORS["play"]
        if time.monotonic() < self._flash_until:
            if self.macros_enabled:
                return "● 매크로 실행 가능", COLORS["play"]
            return "○ 매크로 실행 불가", COLORS["idle"]
        return None

    def overlay_play_text(self) -> str:
        """오버레이용 재생 표시: 재생 아이콘, 매크로 이름, 반복 횟수(현재/전체, 무한은 ∞)만."""
        name = self.app.playing_name or "재생 중"
        prog = self.app.progress
        if prog is None:
            return f"▶ {name}"
        loop, _, repeat = prog
        return f"▶ {name} · {max(loop, 1)}/{repeat or '∞'}"

    def progress_text(self) -> str:
        prog, macro = self.app.progress, self.app.playing_macro
        if prog is None or macro is None or not macro.events or self.play_started is None:
            return ""
        loop, idx, repeat = prog
        return (f"{self.app.playing_name or ''} · 루프 {loop}/{repeat or '∞'} · "
                f"이벤트 {idx + 1}/{len(macro.events)} · {time.monotonic() - self.play_started:.1f}초")

    # ---- 화면 작업: 창을 숨기고 카운트다운 후 실행 ----
    def get_grabber(self):
        if self.grabber is None:
            self.grabber = vision.default_grabber()
        return self.grabber

    def notice(self, text: str, seconds: float = 1.2) -> None:
        self._notice = (text, time.monotonic() + seconds)
        self.log(text)

    def run_screen_action(self, windows, label: str, action) -> None:
        """windows 를 숨기고 '버튼 시작 지연' 동안 카운트다운한 뒤 action(restore) 실행.
        action 은 작업이 끝나면 restore() 를 호출해 창을 되돌려야 한다."""
        try:
            delay = max(0, int(float(self.delay.get())))
        except ValueError:
            delay = 3
        hidden = []
        for w in windows:
            if w is not None and w.winfo_exists() and w.winfo_viewable():
                if w.grab_current() is w:
                    w.grab_release()
                close_popdowns(w)
                w.withdraw()
                hidden.append(w)

        def restore() -> None:
            for w in reversed(hidden):
                if w.winfo_exists():
                    w.deiconify()
                    w.lift()

        def tick(n: int) -> None:
            if n > 0:
                self.notice(f"{n}초 후 {label}", 1.2)
                self.root.after(1000, tick, n - 1)
                return
            self._notice = None
            self.root.update()  # 숨긴 창이 실제로 사라진 뒤 화면을 읽는다
            try:
                action(restore)
            except Exception as e:
                restore()
                self.log(f"오류: {e}")

        tick(delay)

    @property
    def toggle_hotkey(self) -> str:
        return self.settings["toggle_hotkey"]

    def _update_power_button(self) -> None:
        key = self.toggle_hotkey.upper().replace("+", " + ")
        if self.macros_enabled:
            self.btn_power.configure(text=f"● 매크로 실행 가능 ({key})", bg="#2e7d32", activebackground="#388e3c")
        else:
            self.btn_power.configure(text=f"○ 매크로 실행 불가 ({key})", bg="#757575", activebackground="#8a8a8a")

    def _update_macro_settings_buttons(self) -> None:
        state = ["disabled"] if self.macros_enabled else ["!disabled"]
        for button in self.macro_settings_buttons:
            button.state(state)

    def set_toggle_hotkey(self, text: str) -> str | None:
        """전체 실행 전환 키 변경. 문제가 있으면 오류 메시지를 반환."""
        try:
            hotkey = keys.parse_hotkey(text)
        except ValueError as e:
            return str(e)
        if hotkey is None:
            return "키를 지정하세요"
        if any(p in CONTROL_KEYS for p in keys.hotkey_parts(hotkey)):
            return f"{', '.join(k.upper() for k in CONTROL_KEYS)} 는 제어 키라 쓸 수 없습니다"
        for name, m in self.app.library.items():
            if m.hotkey == hotkey:
                return f"{hotkey.upper()} 는 '{name}' 매크로의 시작 핫키입니다"
        self.settings["toggle_hotkey"] = hotkey
        self.settings.save()
        self._update_power_button()
        self.sync_hotkeys()
        self.log(f"전체 실행 전환 키: {hotkey.upper()}")
        return None

    def change_toggle_hotkey(self) -> None:
        result = HotkeyCaptureDialog.ask(self.root)
        if result:
            error = self.set_toggle_hotkey(result)
            if error:
                messagebox.showerror("단축키 변경", error, parent=self.root)

    # ---- 핫키 (리스너 스레드에서 호출됨 -> 큐로 Tk 스레드에 전달) ----
    def hotkey_bindings(self) -> dict:
        def post(fn):
            return lambda: self._calls.put(fn)
        bindings = {}
        for name, m in self.app.library.items():
            if m.hotkey and not any(p in CONTROL_KEYS for p in keys.hotkey_parts(m.hotkey)):
                bindings[m.hotkey] = post(lambda n=name: self.toggle_macro(n))
        bindings[self.toggle_hotkey] = post(self.toggle_macros_enabled)
        bindings[HOTKEY_RECORD] = post(self._hotkey_record)
        bindings[HOTKEY_PLAY] = post(lambda: self.on_play(True))
        bindings[HOTKEY_QUIT] = post(self.close)
        return bindings

    def hotkey_suppressed(self, name: str) -> bool:
        """재생 중인 매크로가 보내는 키가 다른 매크로 핫키를 건드리지 않게 한다."""
        if name in keys.hotkey_parts(self.toggle_hotkey):
            return False  # 전체 실행 전환 키는 항상 동작
        return self.app.playing and name in self.app.playing_keys

    def sync_hotkeys(self) -> None:
        if self.hotkey_listener is not None:
            self.hotkey_listener.dispatcher.bindings = self.hotkey_bindings()

    def recording_ignore_keys(self, own_hotkey: str | None = None) -> set[str]:
        """녹화에서 제외할 키: 제어 키, 단일 키 핫키, 전체 전환 키의 일반 키.
        (조합 매크로 핫키의 키는 게임 입력일 수 있어 제외하지 않는다)"""
        hotkeys = {m.hotkey for m in self.app.library.values() if m.hotkey} | {own_hotkey}
        ignore = set(CONTROL_KEYS) | {h for h in hotkeys if h and "+" not in h}
        ignore |= {p for p in keys.hotkey_parts(self.toggle_hotkey) if p not in keys.MODIFIERS}
        return ignore

    def _hotkey_record(self) -> None:
        if self.editor is not None:
            self.editor.toggle_record(immediate=True)
        else:
            self.log("녹화는 기록 화면(+ 추가 / 편집)에서 할 수 있습니다")

    # ---- 주기 갱신 ----
    def _poll(self) -> None:
        while True:
            try:
                self._calls.get_nowait()()
            except queue.Empty:
                break
            except Exception as e:
                self.log(f"오류: {e}")
        if self._closed:
            return
        lines = []
        while not self._logs.empty():
            lines.append(self._logs.get_nowait())
        if lines:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", "\n".join(lines) + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self._refresh_status()
        if self.editor is not None:
            self.editor.refresh()
        self._after_id = self.root.after(50, self._poll)

    def _refresh_status(self) -> None:
        playing = self.app.playing
        if playing:
            self.play_started = self.play_started or time.monotonic()
        else:
            self.play_started = None
        cd = self.countdown_for("main")
        if cd:
            text, color = f"{cd[0]}초 후 재생", "wait"
        elif playing:
            text, color = "▶ 재생 중", "play"
        else:
            text, color = "대기 중", "idle"  # 녹화 상태는 기록 화면에 표시한다
        self.status.configure(text=text, bg=COLORS[color])
        can_play = self.macros_enabled and not self.app.recording
        self.btn_play.state(["!disabled"] if can_play or playing else ["disabled"])
        self.btn_stop.state(["!disabled"] if playing or cd else ["disabled"])

        state = self.overlay_state()
        if state:
            self.overlay.show(*state)
        else:
            self.overlay.hide()
        text = self.progress_text()
        if not text:
            self.progress["value"] = 0
            self.progress_label.configure(text="재생 대기")
            return
        _, idx, _ = self.app.progress
        self.progress["value"] = (idx + 1) / len(self.app.playing_macro.events) * 100
        self.progress_label.configure(text=text)

    def close(self) -> None:
        if self._closed:
            return
        if self.editor is not None and not self.editor.close():
            return  # 저장 여부에서 취소
        self._closed = True
        try:
            self.settings["start_delay"] = max(0, min(30, int(float(self.delay.get()))))
        except ValueError:
            pass
        self.settings["main_geometry"] = self.root.geometry()
        self.settings.save()
        self.overlay.destroy()
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.app.shutdown()
        self.root.destroy()


class EditorWindow:
    """기록 화면: 녹화, 이벤트 목록 편집, 이벤트 추가, 매크로 설정."""

    def __init__(self, gui: Gui, name: str | None = None) -> None:
        self.gui, self.app = gui, gui.app
        macro = self.app.library.get(name) if name else None
        self.old_name = name if macro else None
        self.items: list[dict] = em.to_items(macro.events) if macro else []
        self.screen = dict(macro.screen) if macro else None
        self.window = dict(macro.window) if macro and macro.window else None
        opts = options_from_dict(macro.options) if macro else PlayOptions()
        # 조건 이미지 작업 폴더: 저장할 때 macros/<이름>/ 에 반영, 닫으면 삭제
        self.assets_dir = Path(tempfile.mkdtemp(prefix="macro_assets_"))
        if self.old_name:
            for fname, src in self.app.asset_files(self.old_name).items():
                shutil.copyfile(src, self.assets_dir / fname)

        self.top = tk.Toplevel(gui.root)
        self.top.minsize(900, 560)
        saved_geometry = gui.settings["editor_geometry"]
        if saved_geometry:
            restore_geometry(self.top, saved_geometry)
        else:
            self.top.withdraw()  # 처음 열 때는 메인 창 가운데에 놓은 뒤 보인다
        self._undo: list[list[dict]] = []
        self._moves_only = False  # 이동 녹화 중 (마우스 이동만 선택 위치 뒤에 추가)
        self._redo: list[list[dict]] = []
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.v_name = tk.StringVar(self.top, value=name or em.unique_name("새 매크로", self.app.library))
        self.v_hotkey = tk.StringVar(self.top, value=macro.hotkey if macro and macro.hotkey else NO_HOTKEY)
        self.v_title = tk.StringVar(self.top, value=opts.window_title)
        coord = macro.coord_space if macro else ("window" if opts.window_title else "screen")
        self.v_coord = tk.StringVar(self.top, value=COORD_LABELS[coord])
        self.v_opts: dict[str, tk.Variable] = {}
        for fname, _, kind in PLAY_FIELDS:
            value = getattr(opts, fname)
            self.v_opts[fname] = (tk.BooleanVar(self.top, value=value) if kind == "check"
                                  else tk.StringVar(self.top, value=str(value)))
        self.dirty = False
        self.v_rec_relative = tk.BooleanVar(  # 상대 이동이 있는 매크로는 상대 이동 녹화로 시작
            self.top, value=any(it["type"] in ("rmove", "relpath") for it in self.items))
        self._build()
        self.refresh_tree()
        for var in [self.v_name, self.v_hotkey, self.v_title, self.v_coord, *self.v_opts.values()]:
            var.trace_add("write", lambda *a: self._mark_dirty())
        self.v_title.trace_add("write", lambda *a: self._auto_coord())
        self._update_title()
        self.refresh()
        if not saved_geometry:
            center_on_parent(self.top, gui.root)

    # ---- 화면 구성 ----
    def _build(self) -> None:
        head = ttk.Frame(self.top, padding=8)
        head.pack(fill="x")
        ttk.Label(head, text="이름").pack(side="left")
        ttk.Entry(head, textvariable=self.v_name, width=24).pack(side="left", padx=(4, 16))
        ttk.Label(head, text="시작 핫키").pack(side="left")
        ttk.Combobox(head, textvariable=self.v_hotkey, width=14,
                     values=[NO_HOTKEY] + em.HOTKEY_CHOICES).pack(side="left", padx=4)
        ttk.Button(head, text="키 입력으로 지정", command=self.capture_hotkey).pack(side="left")
        ttk.Button(head, text="닫기", command=self.close).pack(side="right")
        ttk.Button(head, text="저장", command=self.on_save).pack(side="right", padx=4)

        body = ttk.Frame(self.top, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(body, text="이벤트", padding=6)  # 설정 패널을 먼저 배치한 뒤 남은 공간을 채운다
        self.banner = tk.Label(left, text="대기", fg="white", bg=COLORS["idle"],
                               font=("", 12, "bold"), pady=5)
        self.banner.pack(fill="x", pady=(0, 6))
        bar = ttk.Frame(left)
        bar.pack(fill="x")
        self.btn_rec = ttk.Button(bar, text=f"● 녹화 ({HOTKEY_RECORD.upper()})", command=self.toggle_record)
        self.btn_rec.pack(side="left")
        self.btn_test = ttk.Button(bar, text="▶ 테스트 재생", command=self.test_play)
        self.btn_test.pack(side="left", padx=4)
        # 녹화 방식일 뿐 매크로 설정이 아니다: 재생은 이벤트 종류(이동/상대 이동)를 따른다
        ttk.Checkbutton(bar, text="상대 이동으로 녹화 (3D 시점, Raw Input)",
                        variable=self.v_rec_relative).pack(side="left", padx=4)

        frame = ttk.Frame(left)
        frame.pack(fill="both", expand=True, pady=4)
        cols = ("no", "delay", "type", "detail")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=10, selectmode="extended")
        for col, text, width in zip(cols, ("#", "앞 지연(ms)", "종류", "내용"), (45, 85, 160, 200)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w", stretch=col == "detail")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<Double-Button-1>", lambda e: self.on_edit())
        self.tree.bind("<Delete>", lambda e: self.on_delete())

        add = ttk.Frame(left)
        add.pack(fill="x")
        ttk.Label(add, text="추가:").pack(side="left")
        for text, group in (("키 입력", "key"), ("마우스", "mouse"), ("지연", "wait")):
            ttk.Button(add, text=text, width=9, command=lambda g=group: self.on_add(g)).pack(side="left", padx=2)
        flow = ttk.Frame(left)
        flow.pack(fill="x", pady=(4, 0))
        ttk.Label(flow, text="흐름:").pack(side="left")
        for row, label, buttons in (
                (flow, None, (("🔁 while", self.on_add_loop), ("❓ if", self.on_add_branch),
                              ("⏹ break", self.on_add_break))),
                (ttk.Frame(left), "조건:", (("🔍 await", self.on_add_condition), ("🖱 이미지 클릭", self.on_add_click))),
                (ttk.Frame(left), "변수:", (("📌 const", self.on_add_set_var),
                                           ("🖼 이미지 변수", self.on_add_set_image)))):
            if label:
                row.pack(fill="x", pady=(4, 0))
                ttk.Label(row, text=label).pack(side="left")
            for text, cmd in buttons:
                ttk.Button(row, text=text, command=cmd).pack(side="left", padx=2)
        edit = ttk.Frame(left)
        edit.pack(fill="x", pady=(4, 0))
        for text, cmd in (("수정", self.on_edit), ("삭제", self.on_delete),
                          ("▲ 위로", lambda: self.on_move(-1)), ("▼ 아래로", lambda: self.on_move(1))):
            ttk.Button(edit, text=text, width=8, command=cmd).pack(side="left", padx=(0, 4))
        self.btn_undo = ttk.Button(edit, text="↶ 되돌리기", width=10, command=self.undo)
        self.btn_redo = ttk.Button(edit, text="↷ 다시 실행", width=10, command=self.redo)
        self.btn_undo.pack(side="left", padx=(8, 4))
        self.btn_redo.pack(side="left")
        for seq, fn in (("<Control-z>", self.undo), ("<Control-Z>", self.redo), ("<Control-y>", self.redo)):
            self.top.bind(seq, lambda e, f=fn: self._shortcut(f))
        self.summary = ttk.Label(left, foreground="#444")
        self.summary.pack(anchor="e", pady=(4, 0))

        right = ttk.LabelFrame(body, text="매크로 설정", padding=6)
        right.pack(side="right", fill="y")
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ttk.Label(right, text="대상 창 제목").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(right, textvariable=self.v_title, width=18).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(right, text="좌표 기준").grid(row=1, column=0, sticky="w", pady=2)
        self.coord_box = ttk.Combobox(right, textvariable=self.v_coord, state="readonly", width=10,
                                      values=list(COORD_LABELS.values()))
        self.coord_box.grid(row=1, column=1, sticky="w", padx=6)
        for i, (fname, label, kind) in enumerate(PLAY_FIELDS, start=2):
            ttk.Label(right, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = self.v_opts[fname]
            if kind == "check":
                w = ttk.Checkbutton(right, variable=var)
            else:
                w = ttk.Entry(right, textvariable=var, width=10)
            w.grid(row=i, column=1, sticky="w", padx=6)
        ttk.Label(right, foreground="#666", wraplength=260, justify="left",
                  text="대상 창 제목을 넣으면 그 창이 앞에 있을 때만 입력을 보내고, 좌표를 창 기준으로 저장합니다. "
                       "마우스 이동은 이벤트 종류(마우스 이동=절대 좌표, 마우스 상대 이동=이동량)대로 재생됩니다. "
                       f"녹화 종료는 {HOTKEY_RECORD.upper()} 키를 권장합니다 (버튼 클릭이 기록됨).").grid(
            row=len(PLAY_FIELDS) + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    # ---- 상태 ----
    def show(self) -> None:
        self.top.deiconify()
        self.top.lift()

    def _mark_dirty(self) -> None:
        self.dirty = True
        self._update_title()

    def _update_title(self) -> None:
        self.top.title(f"기록 - {self.v_name.get() or '이름 없음'}{' *' if self.dirty else ''}")

    def _auto_coord(self) -> None:
        if not em.has_positional(self.items):
            self.v_coord.set(COORD_LABELS["window" if self.v_title.get().strip() else "screen"])

    @property
    def coord_space(self) -> str:
        return "window" if self.v_coord.get() == COORD_LABELS["window"] else "screen"

    # ---- 이벤트 목록 ----
    def refresh_tree(self, select: list[int] | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, (it, depth) in enumerate(zip(self.items, em.depths(self.items))):
            detail = em.describe(it)
            if it["type"] == "set_var":
                detail += " · 쓰일 때 판정"
            self.tree.insert("", "end", iid=str(i), values=(
                i + 1, round(it.get("dt", 0) * 1000), "│ " * depth + em.EVENT_LABELS[it["type"]], detail))
        shown = [str(i) for i in (select or []) if self.tree.exists(str(i))]
        if shown:
            self.tree.selection_set(shown)
            self.tree.see(shown[-1])
        total, expanded = em.total_duration(self.items), em.expanded_duration(self.items)
        if expanded == float("inf"):
            extra = " (무한 반복 포함)"
        else:
            extra = f" (반복 포함 {expanded:.2f}초)" if abs(expanded - total) > 0.0001 else ""
        self.summary.configure(text=f"항목 {len(self.items)}개 (이벤트 {em.event_count(self.items)}개) · "
                                    f"{total:.2f}초{extra}")
        self.coord_box.configure(state="disabled" if em.has_positional(self.items) else "readonly")

    # ---- 되돌리기 (이벤트 목록 변경만 대상) ----
    def _snapshot(self) -> None:
        self._undo.append(copy.deepcopy(self.items))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def undo(self) -> None:
        if self._undo:
            self._redo.append(copy.deepcopy(self.items))
            self.items = self._undo.pop()
            self._changed([])

    def redo(self) -> None:
        if self._redo:
            self._undo.append(copy.deepcopy(self.items))
            self.items = self._redo.pop()
            self._changed([])

    def _shortcut(self, fn) -> str | None:
        if isinstance(self.top.focus_get(), (tk.Entry, ttk.Entry)):
            return None  # 입력란에서의 Ctrl+Z 는 건드리지 않는다
        fn()
        return "break"

    def selected_indices(self) -> list[int]:
        return sorted(int(i) for i in self.tree.selection())

    def _changed(self, select: list[int]) -> None:
        self.dirty = True
        self._update_title()
        self.refresh_tree(select)

    def on_add(self, group: str = "key") -> None:
        """group: 'key'(키 입력·누름·뗌) / 'mouse'(클릭·누름·뗌·이동·상대 이동·스크롤) / 'wait'."""
        kinds = em.ADD_GROUPS[group]
        result = EventDialog.ask(self.top, kinds, kind=kinds[0][0], pick=self.pick_position,
                                 record=self.start_move_record)
        if result:
            self.insert_items(result)

    def insert_items(self, new: list[dict]) -> None:
        """선택한 이벤트 뒤(선택이 없으면 끝)에 삽입."""
        sel = self.selected_indices()
        at = sel[-1] + 1 if sel else len(self.items)
        self._snapshot()
        self.items[at:at] = new
        self._changed(list(range(at, at + len(new))))

    def on_edit(self) -> None:
        sel = self.selected_indices()
        if len(sel) != 1:
            return
        i = sel[0]
        mode = {"wait_until": "wait", "if_start": "if", "break_if": "break", "click_image": "click",
                "set_var": "set_var", "repeat_start": "loop", "while_start": "loop"}.get(self.items[i]["type"])
        if self.items[i]["type"] == "set_image":
            result = ImageVarDialog.ask(self, self.items[i])
            if result:
                self._snapshot()
                self.items[i] = result
                self._changed([i])
            return
        if mode:
            result = ConditionDialog.ask(self, self.items[i], mode=mode)
            if result:
                self._snapshot()
                if mode == "loop":  # 횟수 <-> 동안 으로 바뀌면 짝 끝 표시도 바뀐다
                    self.items = em.replace_loop_start(self.items, i, result)
                else:
                    self.items[i] = result
                self._changed([i])
            return
        if self.items[i]["type"] not in em.KIND_FIELDS:
            return
        kinds = {"path": em.PATH_KINDS, "relpath": em.RELPATH_KINDS,
                 "repeat_end": em.REPEAT_END_KINDS, "else": em.ELSE_KINDS,
                 "if_end": em.IF_END_KINDS, "while_end": em.WHILE_END_KINDS}.get(self.items[i]["type"], em.EDIT_KINDS)
        result = EventDialog.ask(self.top, kinds, item=self.items[i], pick=self.pick_position)
        if result:
            self._snapshot()
            self.items[i:i + 1] = result
            self._changed([i])

    def on_delete(self) -> None:
        sel = self.selected_indices()
        if not sel:
            return
        # 구간 시작/끝 중 하나를 지우면 짝(분기는 '아니면'까지)도 지운다 (안의 이벤트는 남는다)
        members = set(sel)
        for i in sel:
            if self.items[i]["type"] in em.BLOCK_MARKERS:
                members |= em.block_members(self.items, i)
        sel = sorted(members)
        self._snapshot()
        for i in reversed(sel):
            del self.items[i]
        self._changed([min(sel[0], len(self.items) - 1)] if self.items else [])

    def on_move(self, step: int) -> None:
        sel = self.selected_indices()
        if len(sel) != 1:
            return
        i, j = sel[0], sel[0] + step
        if not 0 <= j < len(self.items):
            return
        moved = list(self.items)
        moved.insert(j, moved.pop(i))
        if em.block_error(moved) and not em.block_error(self.items):
            return  # 반복 시작/끝의 순서가 뒤바뀌는 이동은 하지 않는다
        self._snapshot()
        self.items = moved
        self._changed([j])

    def on_add_loop(self) -> None:
        """반복문 추가: 횟수 반복(반복 구간) 또는 조건 반복(동안 반복)으로 선택 범위를 감싼다."""
        result = ConditionDialog.ask(self, mode="loop")
        if not result:
            return
        if result["type"] == "repeat_start":
            self.wrap_repeat(result["count"])
        else:
            self.wrap_while(result["cond"])

    def wrap_repeat(self, count: int) -> None:
        try:
            items, select = em.wrap_repeat(self.items, self.selected_indices(), count)
        except ValueError as e:
            messagebox.showerror("반복 구간", str(e), parent=self.top)
            return
        self._snapshot()
        self.items = items
        self._changed(select)

    def coord_origin(self) -> tuple[int, int]:
        """이 매크로 좌표의 원점(화면 좌표). 창 기준이면 대상 창의 좌상단."""
        if self.coord_space != "window":
            return 0, 0
        title = self.v_title.get().strip()
        rect = self.app.backend.find_window_rect(title) if title else None
        if rect is None:
            raise ValueError("창 기준 좌표: 대상 창을 찾을 수 없습니다")
        return rect[0], rect[1]

    def pick_position(self) -> tuple[int, int]:
        """현재 커서 위치를 이 매크로의 좌표 기준으로 반환."""
        x, y = self.app.backend.cursor_pos()
        ox, oy = self.coord_origin()
        return x - ox, y - oy

    # ---- 조건 대기 ----
    def new_template_name(self) -> str:
        n = 1
        while (self.assets_dir / f"이미지{n}.png").exists():
            n += 1
        return f"이미지{n}.png"

    def available_templates(self) -> set[str]:
        return {p.name for p in self.assets_dir.glob("*.png")}

    def on_add_condition(self) -> None:
        result = ConditionDialog.ask(self)
        if result:
            self.insert_items([result])

    def on_add_click(self) -> None:
        result = ConditionDialog.ask(self, mode="click")
        if result:
            self.insert_items([result])

    def on_add_break(self) -> None:
        result = ConditionDialog.ask(self, mode="break")
        if result:
            self.insert_items([result])

    def on_add_branch(self) -> None:
        result = ConditionDialog.ask(self, mode="if")
        if result:
            self.wrap_if(result["cond"], result.get("with_else", False))

    def on_add_set_var(self) -> None:
        result = ConditionDialog.ask(self, mode="set_var")
        if result:
            self.insert_items([result])

    def on_add_set_image(self) -> None:
        result = ImageVarDialog.ask(self)
        if result:
            self.insert_items([result])

    def wrap_while(self, cond: dict) -> None:
        try:
            items, select = em.wrap_while(self.items, self.selected_indices(), cond)
        except ValueError as e:
            messagebox.showerror("while", str(e), parent=self.top)
            return
        self._snapshot()
        self.items = items
        self._changed(select)

    def wrap_if(self, cond: dict, with_else: bool = False) -> None:
        try:
            items, select = em.wrap_if(self.items, self.selected_indices(), cond, with_else)
        except ValueError as e:
            messagebox.showerror("if", str(e), parent=self.top)
            return
        self._snapshot()
        self.items = items
        self._changed(select)

    # ---- 녹화 / 테스트 재생 ----
    def start_move_record(self, relative: bool = False) -> None:
        """마우스 이동만 기록하는 녹화. 끝나면 선택한 이벤트 뒤(없으면 끝)에 추가된다.
        relative: 마우스 상대 이동 추가에서 시작하면 Raw Input 이동량(rmove)으로 녹화."""
        if self.app.recording or self.gui.countdown_for("editor"):
            return
        self.toggle_record(moves_only=True, relative=relative)

    def toggle_record(self, immediate: bool = False, moves_only: bool = False,
                      relative: bool | None = None) -> None:
        """relative: None 이면 '상대 이동으로 녹화' 체크를 따른다."""
        if self.gui.cancel_countdown():
            self._moves_only = False
            return
        if self.app.recording:
            try:
                rec = self.app.stop_record()
            except RuntimeError as e:
                self.gui.log(f"오류: {e}")
                return
            if rec.window:
                self.window = rec.window
            self.screen = rec.screen
            if self._moves_only:
                self._moves_only = False
                moves = em.to_items(em.moves_only(rec.events))
                if moves:
                    self.insert_items(moves)
                else:
                    self.gui.log("기록된 마우스 이동이 없습니다")
                return
            start = len(self.items)
            self._snapshot()
            self.items.extend(em.to_items(rec.events))
            self._changed(list(range(start, len(self.items)))[-1:])
            return
        if self.app.playing:
            self.gui.log("재생 중에는 녹화할 수 없습니다")
            return
        ignore = self.gui.recording_ignore_keys(self._hotkey_value(strict=False))
        title, coord = self.v_title.get().strip(), self.coord_space
        if relative is None:
            relative = self.v_rec_relative.get()
        self._moves_only = moves_only

        def start():
            if not self.gui.guard(lambda: self.app.start_record(title, coord, ignore, relative)):
                self._moves_only = False
        start() if immediate else self.gui.start_after_delay(start, owner="editor",
                                                             what="이동 녹화" if moves_only else "녹화")

    def collect_options(self) -> PlayOptions:
        opts = PlayOptions()
        errors = []
        for fname, var in self.v_opts.items():
            raw = ("on" if var.get() else "off") if isinstance(var, tk.BooleanVar) else str(var.get()).strip()
            try:
                set_option(opts, fname, raw)
            except ValueError as e:
                errors.append(f"{fname}: {e}")
        opts.window_title = self.v_title.get().strip()
        if errors:
            raise ValueError("\n".join(errors))
        return opts

    def _hotkey_value(self, strict: bool = True) -> str | None:
        """입력된 핫키를 정규화 ('Ctrl + F1' -> 'ctrl+f1'). strict 가 아니면 잘못된 값은 None."""
        text = self.v_hotkey.get().strip()
        if text in ("", NO_HOTKEY):
            return None
        try:
            return keys.parse_hotkey(text)
        except ValueError:
            if strict:
                return text  # validate_for_save 가 오류로 알려준다
            return None

    def capture_hotkey(self) -> None:
        result = HotkeyCaptureDialog.ask(self.top)
        if result:
            self.v_hotkey.set(result)

    def build_macro(self) -> tuple[Macro, PlayOptions]:
        opts = self.collect_options()
        window = dict(self.window) if self.window else None
        if self.coord_space == "window":
            window = window or {}
            window["title"] = opts.window_title or window.get("title", "")
        data = {"version": 1, "screen": self.screen or {"width": 0, "height": 0},
                "coord_space": self.coord_space, "window": window, "hotkey": self._hotkey_value(),
                "options": options_to_dict(opts), "events": em.to_events(self.items)}
        return Macro.from_dict(data), opts

    def test_play(self) -> None:
        if self.gui.cancel_countdown():
            return
        if self.app.playing:
            self.app.stop_play()
            return
        try:
            macro, opts = self.build_macro()
        except ValueError as e:  # MacroFormatError 포함
            messagebox.showerror("테스트 재생", str(e), parent=self.top)
            return
        if not macro.events:
            messagebox.showinfo("테스트 재생", "이벤트가 없습니다", parent=self.top)
            return
        label = f"(편집 중) {self.v_name.get().strip()}"
        assets = self.assets_dir
        self.gui.start_after_delay(lambda: self.gui.guard(lambda: self.app.start_play(macro, opts, label, assets)),
                                   owner="editor", what="테스트 재생")

    # ---- 저장 / 닫기 ----
    def on_save(self) -> bool:
        """저장 후 창을 닫는다."""
        if self.app.recording:
            self.toggle_record()
        if not self._save():
            return False
        self._destroy()
        return True

    def _save(self) -> bool:
        name = self.v_name.get().strip()
        errors = em.validate_for_save(name, self._hotkey_value(), self.items, self.app.library, self.old_name,
                                      reserved=(self.gui.toggle_hotkey,),
                                      available_templates=self.available_templates())
        macro = None
        if not errors:
            try:
                macro, _ = self.build_macro()
            except ValueError as e:
                errors.append(str(e))
        if errors:
            messagebox.showerror("저장할 수 없습니다", "\n".join(errors), parent=self.top)
            return False
        try:
            assets = {t: self.assets_dir / t for t in vision.templates_in(self.items)}
            saved = self.app.store(name, macro, self.old_name, assets=assets)
        except (ValueError, OSError) as e:
            messagebox.showerror("저장 실패", str(e), parent=self.top)
            return False
        self.old_name = saved
        self.dirty = False
        self.gui.refresh_list(select=saved)
        return True

    def close(self) -> bool:
        """창을 닫는다. 사용자가 취소하면 False."""
        if self.app.recording:
            self.toggle_record()
        if self.dirty:
            answer = messagebox.askyesnocancel("기록 화면 닫기", "변경 내용을 저장할까요?", parent=self.top)
            if answer is None or (answer and not self._save()):
                return False
        self._destroy()
        return True

    def _destroy(self) -> None:
        if self.gui.countdown_for("editor"):
            self.gui.cancel_countdown()
        self.gui.settings["editor_geometry"] = self.top.geometry()
        self.gui.settings.save()
        shutil.rmtree(self.assets_dir, ignore_errors=True)
        self.top.destroy()
        self.gui.editor = None

    # ---- 주기 갱신 (Gui._poll 에서 호출) ----
    def refresh(self) -> None:
        recording, playing = self.app.recording, self.app.playing
        self.btn_rec.configure(text=f"{'■ 녹화 종료' if recording else '● 녹화'} ({HOTKEY_RECORD.upper()})")
        self.btn_rec.state(["disabled"] if playing else ["!disabled"])
        self.btn_test.configure(text="■ 테스트 중지" if playing else "▶ 테스트 재생")
        self.btn_test.state(["disabled"] if recording else ["!disabled"])
        self.btn_undo.state(["!disabled"] if self._undo else ["disabled"])
        self.btn_redo.state(["!disabled"] if self._redo else ["disabled"])
        cd = self.gui.countdown_for("editor")
        if cd:
            text, color = f"{cd[0]}초 후 {cd[1]} 시작 — 게임 창으로 전환하세요", "wait"
        elif recording:
            mode = " · 상대 이동(Raw Input)" if self.app.recording_relative else ""
            if self._moves_only:
                text = f"● 이동 녹화 중{mode} — {HOTKEY_RECORD.upper()}로 종료 (마우스 이동만, 선택 위치 뒤에 추가됨)"
            else:
                text = f"● 녹화 중{mode} — {HOTKEY_RECORD.upper()}로 종료 (목록 끝에 추가됨)"
            color = "rec"
        elif playing:
            text, color = f"▶ 재생 중 — {self.app.playing_name or ''}", "play"
        else:
            text, color = "대기", "idle"
        self.banner.configure(text=text, bg=COLORS[color])


class EventDialog:
    """이벤트 추가/수정 대화상자. result: 편집 항목 리스트 (취소 시 None)."""

    def __init__(self, parent, kinds: list[tuple[str, str]], item: dict | None = None,
                 kind: str = "tap", pick=None, record=None) -> None:
        self.kinds = kinds
        self.pick = pick
        self.record = None if item else record  # 마우스 이동 추가 시 '이동 녹화' (마우스 이동만 기록)
        self._capturing = False
        self.item = item
        self.result: list[dict] | None = None
        init = em.item_fields(item) if item else {}
        kind = init.get("kind", kind)
        labels = dict(kinds)
        self.top = tk.Toplevel(parent)
        self.top.withdraw()  # 위치를 잡은 뒤 보인다
        self.top.title("이벤트 수정" if item else "이벤트 추가")
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.v_kind = tk.StringVar(self.top, value=labels.get(kind, kinds[0][1]))
        self.v = {k: tk.StringVar(self.top, value=str(init.get(k, default))) for k, default in (
            ("delay_ms", 100), ("key", ""), ("button", "left"), ("x", 0), ("y", 0),
            ("dx", 0), ("dy", -1), ("hold_ms", ""), ("duration_ms", 0), ("scale_pct", 100), ("count", 2))}
        self.v_cursor = tk.BooleanVar(self.top, value=init.get("at_cursor", False))
        if not item and pick is not None:
            try:  # 새 마우스 이벤트는 현재 커서 위치로 시작
                x, y = pick()
                self.v["x"].set(str(x))
                self.v["y"].set(str(y))
            except Exception:
                pass
        self._build()
        self._on_kind()
        center_on_parent(self.top, parent)

    def _build(self) -> None:
        f = ttk.Frame(self.top, padding=10)
        f.pack(fill="both")
        self.rows: dict[str, list] = {}

        def row(r: int, field: str | None, label: str, widget) -> None:
            lab = ttk.Label(f, text=label)
            lab.grid(row=r, column=0, sticky="w", pady=3)
            widget.grid(row=r, column=1, sticky="w", padx=6)
            if field:
                self.rows.setdefault(field, []).extend([lab, widget])

        row(0, None, "종류", ttk.Combobox(f, textvariable=self.v_kind, state="readonly", width=22,
                                          values=[label for _, label in self.kinds]))
        self.v_kind.trace_add("write", lambda *a: self._on_kind())
        self.delay_label = ttk.Label(f, text="앞 지연(ms)")
        self.delay_label.grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.v["delay_ms"], width=10).grid(row=1, column=1, sticky="w", padx=6)
        key = ttk.Frame(f)
        ttk.Combobox(key, textvariable=self.v["key"], values=em.KEY_CHOICES, width=14).pack(side="left")
        self.capture_btn = ttk.Button(key, text="키 입력으로 지정", command=self._capture_key)
        self.capture_btn.pack(side="left", padx=(4, 0))
        row(2, "key", "키", key)
        row(3, "button", "버튼", ttk.Combobox(f, textvariable=self.v["button"], state="readonly",
                                              values=sorted(BUTTONS), width=10))
        pos = ttk.Frame(f)
        ttk.Entry(pos, textvariable=self.v["x"], width=7).pack(side="left")
        ttk.Entry(pos, textvariable=self.v["y"], width=7).pack(side="left", padx=4)
        self.pick_btn = None
        if self.pick is not None:
            self.pick_btn = ttk.Button(pos, text="3초 후 현재 위치", command=self._pick_later)
            self.pick_btn.pack(side="left")
        row(4, "cursor", "위치", ttk.Checkbutton(f, text="좌표 없이 현재 커서 위치에서 입력",
                                                  variable=self.v_cursor, command=self._on_kind))
        row(5, "pos", "X, Y", pos)
        sc = ttk.Frame(f)
        ttk.Entry(sc, textvariable=self.v["dx"], width=7).pack(side="left")
        ttk.Entry(sc, textvariable=self.v["dy"], width=7).pack(side="left", padx=4)
        ttk.Label(sc, text="(가로, 세로 · 아래로 = 음수)").pack(side="left")
        row(6, "scroll", "스크롤 칸 수", sc)
        delta = ttk.Frame(f)
        ttk.Entry(delta, textvariable=self.v["dx"], width=7).pack(side="left")
        ttk.Entry(delta, textvariable=self.v["dy"], width=7).pack(side="left", padx=4)
        ttk.Label(delta, text="(오른쪽/아래 = 양수)").pack(side="left")
        row(7, "delta", "이동량 X, Y", delta)
        row(8, "hold", "누름 유지(ms)", ttk.Entry(f, textvariable=self.v["hold_ms"], width=10))
        row(9, "duration", "이동 시간(ms)", ttk.Entry(f, textvariable=self.v["duration_ms"], width=10))
        row(10, "scale", "이동량 배율(%)", ttk.Entry(f, textvariable=self.v["scale_pct"], width=10))
        row(11, "count", "반복 횟수", ttk.Entry(f, textvariable=self.v["count"], width=10))
        if self.record is not None:
            rec = ttk.Frame(f)
            ttk.Button(rec, text="● 이동 녹화", command=self._on_record).pack(side="left")
            ttk.Label(rec, text=f"(마우스 이동만 기록 · {HOTKEY_RECORD.upper()}로 종료)").pack(side="left", padx=4)
            row(12, "record", "녹화", rec)
        self.error = ttk.Label(f, foreground="#c62828", wraplength=320)
        self.error.grid(row=13, column=0, columnspan=2, sticky="w")
        btns = ttk.Frame(f)
        btns.grid(row=14, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="확인", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.top.destroy).pack(side="left")
        self.top.bind("<Return>", lambda e: None if self._capturing else self._on_ok())
        self.top.bind("<Escape>", lambda e: None if self._capturing else self.top.destroy())

    @property
    def kind(self) -> str:
        label = self.v_kind.get()
        return next(k for k, lab in self.kinds if lab == label)

    def _on_kind(self) -> None:
        kind = self.kind
        fields = set(em.KIND_FIELDS[kind])
        if "cursor" in fields and self.v_cursor.get():
            fields.discard("pos")  # 현재 커서 위치 사용 시 좌표 입력 숨김
        if kind in em.GROUPED:
            fields.add("record")
        for field, widgets in self.rows.items():
            for w in widgets:
                w.grid() if field in fields else w.grid_remove()
        self.delay_label.configure(text="지연(ms)" if kind == "wait" else "앞 지연(ms)")
        if kind == "repeat_end":
            self.delay_label.configure(text="반복 전 대기(ms)")
        self.rows["pos"][0].configure(text="끝 위치 X, Y" if kind == "path" else "X, Y")
        if "hold" in fields and not self.v["hold_ms"].get():
            self.v["hold_ms"].set(str(em.DEFAULT_HOLD_MS[kind]))

    def _capture_key(self) -> None:
        """다음에 누르는 키 하나를 키 이름으로 지정한다."""
        self._capturing = True
        self.capture_btn.configure(text="키를 누르세요…")
        self.error.configure(text="")
        self.top.bind("<KeyPress>", self._on_capture)
        self.top.focus_set()  # 입력 칸이 키를 받지 않도록

    def _on_capture(self, event) -> str:
        name = keys.name_from_tk(event.keysym, event.keycode, windows=sys.platform == "win32")
        self._capturing = False
        self.top.unbind("<KeyPress>")
        self.capture_btn.configure(text="키 입력으로 지정")
        if name and keys.is_known(name):
            self.v["key"].set(name)
        else:
            self.error.configure(text=f"지원하지 않는 키입니다: {event.keysym}")
        return "break"

    def _on_record(self) -> None:
        relative = self.kind == "rmove"  # 상대 이동 추가 -> 이동량 녹화, 마우스 이동 -> 좌표 녹화
        self.top.destroy()
        self.record(relative=relative)

    def _pick_later(self, n: int = 3) -> None:
        if n > 0:
            self.pick_btn.configure(text=f"{n}…")
            self.top.after(1000, self._pick_later, n - 1)
            return
        self.pick_btn.configure(text="3초 후 현재 위치")
        try:
            x, y = self.pick()
            self.v["x"].set(str(x))
            self.v["y"].set(str(y))
            self.error.configure(text="")
        except Exception as e:
            self.error.configure(text=str(e))

    def _on_ok(self) -> None:
        v = {k: var.get() for k, var in self.v.items()}
        try:
            self.result = em.build_items(self.kind, delay_ms=v["delay_ms"], key=v["key"],
                                         button=v["button"], x=v["x"], y=v["y"], dx=v["dx"],
                                         dy=v["dy"], hold_ms=v["hold_ms"], at_cursor=self.v_cursor.get(),
                                         duration_ms=v["duration_ms"], scale_pct=v["scale_pct"], count=v["count"],
                                         points=self.item.get("points") if self.item else None)
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        self.top.destroy()

    @classmethod
    def ask(cls, parent, kinds, **kw) -> list[dict] | None:
        dlg = cls(parent, kinds, **kw)
        dlg.top.grab_set()
        dlg.top.wait_window()
        return dlg.result


class RegionSelector:
    """캡처한 화면을 전체 화면으로 띄우고 드래그로 사각형을 고른다.
    on_done((x, y, w, h)) — 화면 좌표. Esc / 오른쪽 클릭이면 on_done(None)."""

    def __init__(self, root, image, on_done, hint: str = "드래그해서 영역을 고르세요 · Esc 취소") -> None:
        self.on_done = on_done
        self._start: tuple[int, int] | None = None
        h, w = image.shape[:2]
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.geometry(f"{w}x{h}+0+0")
        self.canvas = tk.Canvas(self.top, width=w, height=h, highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self._photo = tk.PhotoImage(master=self.top, data=vision.png_base64(image))
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.create_rectangle(0, 0, w, 30, fill="#000000", outline="", stipple="gray50")
        self.canvas.create_text(w // 2, 15, text=hint, fill="white", font=("", 12, "bold"))
        self.rect = self.canvas.create_rectangle(0, 0, 0, 0, outline="#ff3030", width=2)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", lambda e: self.finish(None))
        self.top.bind("<Escape>", lambda e: self.finish(None))
        self.top.focus_force()

    def on_press(self, e) -> None:
        self._start = (e.x, e.y)
        self.canvas.coords(self.rect, e.x, e.y, e.x, e.y)

    def on_drag(self, e) -> None:
        if self._start:
            self.canvas.coords(self.rect, *self._start, e.x, e.y)

    def on_release(self, e) -> None:
        if not self._start:
            return
        (x0, y0), (x1, y1) = self._start, (e.x, e.y)
        self._start = None
        x, y, w, h = min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
        if w < 4 or h < 4:
            self.canvas.coords(self.rect, 0, 0, 0, 0)  # 너무 작으면 다시 고르게 한다
            return
        self.finish((x, y, w, h))

    def finish(self, rect) -> None:
        if self.top.winfo_exists():
            self.top.destroy()
        self.on_done(rect)


def search_region_around(rect, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """잘라낸 이미지 주변으로 여유를 둔 검색 영역 (화면 안으로 제한)."""
    x, y, w, h = rect
    mx, my = max(40, w // 2), max(40, h // 2)
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(screen_w, x + w + mx), min(screen_h, y + h + my)
    return x0, y0, x1 - x0, y1 - y0


class ConditionDialog:
    """조건을 쓰는 항목의 추가/수정. result: 편집 항목 (취소 시 None).
    mode="cond" 는 '여러 조건' 안의 조건 하나만 고르는 용도로, result 는 {"cond": 조건}."""

    TITLES = {"wait": "await", "if": "if", "break": "break", "click": "이미지 클릭",
              "set_var": "const", "loop": "while", "cond": "조건"}
    KIND_TEXT = {"count": "횟수", "image": "이미지", "pixel": "범위 색", "var": "변수", "group": "여러 조건"}
    SCREEN_KINDS = ("image", "pixel", "var", "group")
    KINDS_BY_MODE = {"loop": ("count",) + SCREEN_KINDS, "if": SCREEN_KINDS, "break": SCREEN_KINDS,
                     "set_var": SCREEN_KINDS, "wait": SCREEN_KINDS,
                     "click": ("image",), "cond": ("image", "pixel", "var")}

    def __init__(self, editor: "EditorWindow", item: dict | None = None, mode: str = "wait", parent=None) -> None:
        """mode: wait(조건 대기) | if(조건 분기) | break(반복 탈출) | click(이미지 클릭)
        | set_var(변수 저장) | loop(반복문: 횟수 반복/동안 반복) | cond(여러 조건 안의 변수 조건 하나)."""
        self.editor, self.gui = editor, editor.gui
        self.mode = mode
        self.parent = parent or editor.top
        self.editing = item is not None
        self.result: dict | None = None
        self._modal = False
        cond = (item or {}).get("cond", {})
        region = cond.get("region")
        self.kinds = kinds = self.KINDS_BY_MODE[mode]
        self.top = tk.Toplevel(self.parent)
        self.top.withdraw()
        self.top.title(f"{self.TITLES[mode]} {'수정' if item else '추가'}")
        self.top.transient(self.parent)
        self.top.resizable(False, False)

        def var(value) -> tk.StringVar:
            return tk.StringVar(self.top, value=str(value))

        group = cond.get("kind") in vision.GROUP_KINDS
        if (item or {}).get("type") == "repeat_start" or not cond:
            kind = kinds[0]
        else:
            kind = "group" if group else cond["kind"]
        self.v_kind = tk.StringVar(self.top, value=kind)
        self.v_count = var((item or {}).get("count", 2))
        self.v_group_op = tk.StringVar(self.top, value=cond["kind"] if group else "all")
        self.sub_conds: list[dict] = [dict(c) for c in cond.get("conds", [])] if group else []
        self.v_var = tk.StringVar(self.top, value=cond.get("name", "") if cond.get("kind") == "var" else "")
        self.v_name = tk.StringVar(self.top, value=(item or {}).get("name", ""))
        self.v_negate = tk.BooleanVar(self.top, value=bool(cond.get("negate")))
        self.v_delay = var(round((item or {}).get("dt", 0) * 1000))
        self.v_timeout = var(f"{(item or {}).get('timeout', 10):g}")
        self.v_on_timeout = tk.StringVar(self.top, value=(item or {}).get("on_timeout", "stop"))
        self.v_interval = var(round((item or {}).get("interval", 0.1) * 1000))
        self.v_template = tk.StringVar(self.top, value=cond.get("template", ""))
        self.v_image_src = tk.StringVar(self.top, value="var" if cond.get("image_var") else "file")
        self.v_image_var = tk.StringVar(self.top, value=cond.get("image_var", ""))
        self.v_full = tk.BooleanVar(self.top, value=item is not None and cond.get("kind") == "image" and not region)
        self.v_region = [var(v) for v in (region or [0, 0, 0, 0])]
        self.v_threshold = var(round(cond.get("threshold", vision.DEFAULT_THRESHOLD) * 100))
        self.v_px, self.v_py = var(cond.get("x", 0)), var(cond.get("y", 0))
        self.v_pw, self.v_ph = var(cond.get("w", 1)), var(cond.get("h", 1))  # 한 점 조건은 1x1 범위로
        self.v_color = var(cond.get("color", "#000000"))
        self.v_tol = var(cond.get("tolerance", vision.DEFAULT_TOLERANCE))
        self.v_ratio = var(round(cond.get("ratio", vision.DEFAULT_RATIO) * 100))
        self.v_button = tk.StringVar(self.top, value=(item or {}).get("button", "left"))
        offset = (item or {}).get("offset", [0, 0])
        self.v_off_x, self.v_off_y = var(offset[0]), var(offset[1])
        self.v_hold = var(round((item or {}).get("hold", 0.06) * 1000))
        self.v_with_else = tk.BooleanVar(self.top, value=False)
        self._thumb = None
        self._build()
        self._on_kind()
        self._update_thumb()
        center_on_parent(self.top, self.parent)

    def _build(self) -> None:
        f = ttk.Frame(self.top, padding=10)
        f.pack(fill="both")
        if "image" in self.kinds:
            ttk.Label(f, text="독점 전체 화면 게임에서는 화면을 읽지 못할 수 있습니다 (창 모드 권장).",
                      foreground="#777777").pack(anchor="w", pady=(0, 6))
        self.intros = {"wait": "조건이 맞을 때까지 기다린 뒤 다음 이벤트로 진행합니다.",
                 "if": "조건이 맞으면 'if' 구간을, 아니면 'else' 구간(있을 때)을 실행합니다. 선택한 이벤트를 감쌉니다.",
                 "break": "조건이 맞으면 가장 안쪽 반복 구간을 끝냅니다 (구간 밖이면 이번 회차를 끝냄).",
                 "click": "이미지가 나타날 때까지 기다렸다가, 찾은 위치(+보정)를 클릭합니다.",
                 "set_var": "조건을 변수에 지정합니다. 여기서는 판정하지 않고, 이후 조건에서 '변수'로 쓰일 때마다 "
                            "그 자리에서 판정해 참/거짓을 정합니다. 지정 전에 쓰이면 거짓입니다.",
                 "loop": "조건이 맞는 동안 구간을 반복합니다 (매 회차 시작 전에 판정). 선택한 이벤트를 감쌉니다.",
                 "count": "정한 횟수만큼 구간을 반복합니다. 선택한 이벤트를 감쌉니다 (선택이 없으면 끝에 빈 구간).",
                 "cond": "'여러 조건'에 넣을 조건 하나를 고릅니다."}
        self.intro = ttk.Label(f, text=self.intros[self.mode], wraplength=440)
        self.intro.pack(anchor="w", pady=(0, 4))
        if self.mode == "set_var":
            nrow = ttk.Frame(f)
            nrow.pack(fill="x", pady=(0, 4))
            ttk.Label(nrow, text="변수 이름").pack(side="left")
            ttk.Combobox(nrow, textvariable=self.v_name, width=18,
                         values=em.variables_in(self.editor.items)).pack(side="left", padx=4)
        if len(self.kinds) > 1:
            row = ttk.Frame(f)
            row.pack(fill="x")
            ttk.Label(row, text="조건").pack(side="left")
            for val in self.kinds:
                ttk.Radiobutton(row, text=self.KIND_TEXT[val], value=val, variable=self.v_kind,
                                command=self._on_kind).pack(side="left", padx=4)
        self.f_neg = ttk.Frame(f)
        if self.mode != "click":
            neg = {"wait": "반대로 (조건이 '아닐' 때까지 대기)", "if": "반대로 (조건이 '아닐' 때 실행)",
                   "break": "반대로 (조건이 '아닐' 때 탈출)", "set_var": "반대로 (조건이 '아닐' 때 참으로 저장)",
                   "loop": "반대로 (조건이 '아닌' 동안 반복)", "cond": "반대로 (조건이 '아닐' 때 충족)"}[self.mode]
            ttk.Checkbutton(self.f_neg, text=neg, variable=self.v_negate).pack(anchor="w", pady=2)

        # 횟수
        self.f_count = ttk.LabelFrame(f, text="횟수", padding=6)
        ttk.Label(self.f_count, text="반복 횟수").pack(side="left")
        ttk.Entry(self.f_count, textvariable=self.v_count, width=8).pack(side="left", padx=6)
        ttk.Label(self.f_count, text="(0 = 무한, 'break'로 끝냄)", foreground="#555").pack(side="left")

        # 이미지
        self.f_image = ttk.LabelFrame(f, text="이미지", padding=6)
        src = ttk.Frame(self.f_image)
        src.pack(fill="x", pady=(0, 6))
        ttk.Label(src, text="찾을 이미지").pack(side="left")
        for val, text in (("file", "잘라낸 이미지"), ("var", "이미지 변수")):
            ttk.Radiobutton(src, text=text, value=val, variable=self.v_image_src,
                            command=self._on_image_src).pack(side="left", padx=(4, 0))
        ttk.Combobox(src, textvariable=self.v_image_var, width=14,
                     values=em.image_variables_in(self.editor.items)).pack(side="left", padx=4)
        self.v_image_var.trace_add("write", lambda *a: (self.v_image_src.set("var"), self._on_image_src()))
        top = ttk.Frame(self.f_image)
        top.pack(fill="x")
        self.thumb = tk.Label(top, text="(이미지 없음)", width=24, height=4, relief="sunken", bg="#eeeeee")
        self.thumb.pack(side="left")
        btns = ttk.Frame(top)
        btns.pack(side="left", padx=8)
        ttk.Button(btns, text="📷 화면에서 잘라내기", command=self.on_crop).pack(fill="x")
        ttk.Button(btns, text="검색 영역만 다시 지정", command=self.on_pick_region).pack(fill="x", pady=4)
        ttk.Label(btns, textvariable=self.v_template, foreground="#555").pack(anchor="w")
        reg = ttk.Frame(self.f_image)
        reg.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(reg, text="화면 전체에서 찾기", variable=self.v_full, command=self._on_kind).pack(side="left")
        self.region_entries = []
        for label, v in zip(("X", "Y", "너비", "높이"), self.v_region):
            ttk.Label(reg, text=label).pack(side="left", padx=(6, 1))
            e = ttk.Entry(reg, textvariable=v, width=6)
            e.pack(side="left")
            self.region_entries.append(e)
        th = ttk.Frame(self.f_image)
        th.pack(fill="x", pady=(6, 0))
        ttk.Label(th, text="일치도 기준(%)").pack(side="left")
        ttk.Entry(th, textvariable=self.v_threshold, width=6).pack(side="left", padx=4)

        # 범위 색
        self.f_pixel = ttk.LabelFrame(f, text="범위 색", padding=6)
        prow = ttk.Frame(self.f_pixel)
        prow.pack(fill="x")
        ttk.Button(prow, text="🎨 범위를 드래그해 색 선택", command=self.on_pick_color).pack(side="left")
        self.color_info = ttk.Label(prow, text="", foreground="#555")
        self.color_info.pack(side="left", padx=8)
        reg = ttk.Frame(self.f_pixel)
        reg.pack(fill="x", pady=(6, 0))
        for label, v in (("X", self.v_px), ("Y", self.v_py), ("너비", self.v_pw), ("높이", self.v_ph)):
            ttk.Label(reg, text=label).pack(side="left", padx=(0, 1))
            ttk.Entry(reg, textvariable=v, width=6).pack(side="left", padx=(0, 6))
        crow = ttk.Frame(self.f_pixel)
        crow.pack(fill="x", pady=(6, 0))
        ttk.Label(crow, text="색").pack(side="left")
        ttk.Entry(crow, textvariable=self.v_color, width=9).pack(side="left", padx=2)
        self.swatch = tk.Label(crow, width=3, relief="sunken")
        self.swatch.pack(side="left", padx=(2, 10))
        self.v_color.trace_add("write", lambda *a: self._update_swatch())
        ttk.Label(crow, text="허용 오차").pack(side="left")
        ttk.Entry(crow, textvariable=self.v_tol, width=4).pack(side="left", padx=(2, 10))
        ttk.Label(crow, text="이 색의 비율 ≥ (%)").pack(side="left")
        ttk.Entry(crow, textvariable=self.v_ratio, width=4).pack(side="left", padx=2)

        # 변수
        self.f_var = ttk.LabelFrame(f, text="변수", padding=6)
        ttk.Label(self.f_var, text="'const'로 지정한 조건을 지금 판정해 참이면 충족").pack(side="left")
        self.var_box = ttk.Combobox(self.f_var, textvariable=self.v_var, width=18,
                                    values=em.variables_in(self.editor.items))
        self.var_box.pack(side="left", padx=6)
        if not em.variables_in(self.editor.items):
            ttk.Label(self.f_var, text="먼저 📌 const로 화면 판정 조건을 지정하세요",
                      foreground="#c62828").pack(side="left")

        # 여러 조건
        self.f_group = ttk.LabelFrame(f, text="여러 조건", padding=6)
        orow = ttk.Frame(self.f_group)
        orow.pack(fill="x")
        for val, text in (("all", "모두 맞을 때 (그리고)"), ("any", "하나라도 맞을 때 (또는)")):
            ttk.Radiobutton(orow, text=text, value=val, variable=self.v_group_op).pack(side="left", padx=(0, 8))
        lrow = ttk.Frame(self.f_group)
        lrow.pack(fill="x", pady=(6, 0))
        self.sub_list = tk.Listbox(lrow, height=4, width=48, activestyle="none", exportselection=False)
        self.sub_list.pack(side="left", fill="x", expand=True)
        self.sub_list.bind("<Double-Button-1>", lambda e: self.on_sub_edit())
        sbtn = ttk.Frame(lrow)
        sbtn.pack(side="left", padx=(6, 0))
        for text, cmd in (("추가…", self.on_sub_add), ("수정", self.on_sub_edit), ("삭제", self.on_sub_delete)):
            ttk.Button(sbtn, text=text, width=7, command=cmd).pack(fill="x", pady=1)
        self._refresh_subs()

        # 이미지 클릭 설정
        self.f_click = ttk.LabelFrame(f, text="클릭", padding=6)
        ttk.Label(self.f_click, text="버튼").pack(side="left")
        ttk.Combobox(self.f_click, textvariable=self.v_button, state="readonly", width=7,
                     values=sorted(BUTTONS)).pack(side="left", padx=(2, 10))
        ttk.Label(self.f_click, text="보정 X, Y").pack(side="left")
        ttk.Entry(self.f_click, textvariable=self.v_off_x, width=5).pack(side="left", padx=2)
        ttk.Entry(self.f_click, textvariable=self.v_off_y, width=5).pack(side="left", padx=(2, 10))
        ttk.Label(self.f_click, text="누름 유지(ms)").pack(side="left")
        ttk.Entry(self.f_click, textvariable=self.v_hold, width=5).pack(side="left", padx=2)

        # 공통: 앞 지연 (+ 대기형이면 최대 대기·확인 간격·초과 시 동작)
        waits = self.mode in ("wait", "click")
        self.f_common = ttk.LabelFrame(f, text="대기" if waits else "실행", padding=6)
        wraps = self.mode in ("if", "loop")  # 선택 범위를 감싸는 종류는 감쌀 때 지연을 받지 않는다
        rows = [] if self.mode == "cond" else [("앞 지연(ms)", self.v_delay)] if (self.editing or not wraps) else []
        if waits:
            rows += [("최대 대기(초, 0=무제한)", self.v_timeout), ("확인 간격(ms)", self.v_interval)]
        for r, (label, v) in enumerate(rows):
            ttk.Label(self.f_common, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(self.f_common, textvariable=v, width=8).grid(row=r, column=1, sticky="w", padx=6)
        if waits:
            ttk.Label(self.f_common, text="시간 초과 시").grid(row=len(rows), column=0, sticky="w", pady=2)
            ot = ttk.Frame(self.f_common)
            ot.grid(row=len(rows), column=1, sticky="w", padx=6)
            ttk.Radiobutton(ot, text="재생 중지", value="stop", variable=self.v_on_timeout).pack(side="left")
            ttk.Radiobutton(ot, text="계속 진행", value="continue", variable=self.v_on_timeout).pack(side="left", padx=6)
        if self.mode == "if" and not self.editing:
            ttk.Checkbutton(self.f_common, text="'else' 구간도 만들기 (조건이 맞지 않을 때 실행)",
                            variable=self.v_with_else).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w")
        self._common_has_rows = bool(rows) or waits or (self.mode == "if" and not self.editing)

        self.f_test = ttk.Frame(f)
        ttk.Button(self.f_test, text="🔍 지금 찾아보기", command=self.on_test).pack(side="left")
        self.test_label = ttk.Label(self.f_test, text="", wraplength=330)
        self.test_label.pack(side="left", padx=8)
        self.error = ttk.Label(f, foreground="#c62828", wraplength=420)
        self.f_buttons = ttk.Frame(f)
        ttk.Button(self.f_buttons, text="확인", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(self.f_buttons, text="취소", command=self.top.destroy).pack(side="left")
        self.top.bind("<Escape>", lambda e: self.top.destroy())

    def _on_kind(self) -> None:
        kind = self.v_kind.get()
        for w in (self.f_neg, self.f_count, self.f_image, self.f_pixel, self.f_var, self.f_group, self.f_click,
                  self.f_common, self.f_test, self.error, self.f_buttons):
            w.pack_forget()
        if self.mode == "loop":
            self.intro.configure(text=self.intros["count" if kind == "count" else "loop"])
        if kind != "count":
            self.f_neg.pack(fill="x")
        {"count": self.f_count, "image": self.f_image, "pixel": self.f_pixel, "var": self.f_var,
         "group": self.f_group}[kind].pack(fill="x", pady=4)
        if self.mode == "click":
            self.f_click.pack(fill="x", pady=4)
        if self._common_has_rows:
            self.f_common.pack(fill="x", pady=4)
        if self._screen_kind():  # 변수·횟수만으로는 화면을 볼 것이 없다
            self.f_test.pack(fill="x", pady=4)
        self.error.pack(anchor="w")
        self.f_buttons.pack(pady=(8, 0))
        state = ["disabled"] if self.v_full.get() else ["!disabled"]
        for e in self.region_entries:
            e.state(state)
        self._update_swatch()

    def _screen_kind(self) -> bool:
        kind = self.v_kind.get()
        if kind == "group":
            return em.uses_screen({"kind": "all", "conds": self.sub_conds})
        return kind in vision.SCREEN_KINDS

    def _on_image_src(self) -> None:
        if self.v_image_src.get() == "var" and all(v.get().strip() in ("", "0") for v in self.v_region[2:]):
            self.v_full.set(True)  # 검색 영역을 정하지 않았으면 화면 전체에서 찾는다
            self._on_kind()
        self._update_thumb()

    def _update_thumb(self) -> None:
        if self.v_image_src.get() == "var":
            var = self.v_image_var.get().strip()
            name = em.image_defs(self.editor.items).get(var)
            missing = "(이미지 변수를 고르세요)" if not var else f"(파일 없음: {name})" if name else "(재생 중 캡처)"
        else:
            name = self.v_template.get()
            missing = "(이미지 없음)" if not name else f"(파일 없음: {name})"
        self._thumb = show_thumb(self.thumb, self.top, self.editor.assets_dir / name if name else None, missing)

    def _update_swatch(self) -> None:
        color = self.v_color.get().strip()
        try:
            vision.parse_color(color)
            self.swatch.configure(bg=color if len(color) == 7 else "#ffffff")
        except (ValueError, tk.TclError):
            self.swatch.configure(bg="#ffffff")

    # ---- 여러 조건 ----
    def _refresh_subs(self, select: int | None = None) -> None:
        self.sub_list.delete(0, "end")
        for c in self.sub_conds:
            self.sub_list.insert("end", vision.describe_condition(c))
        if select is not None and 0 <= select < len(self.sub_conds):
            self.sub_list.selection_set(select)

    def _sub_selected(self) -> int | None:
        sel = self.sub_list.curselection()
        return sel[0] if sel else None

    def on_sub_add(self) -> None:
        result = ConditionDialog.ask(self.editor, mode="cond", parent=self.top)
        self._regrab()
        if result:
            self.sub_conds.append(result["cond"])
            self._refresh_subs(len(self.sub_conds) - 1)

    def on_sub_edit(self) -> None:
        i = self._sub_selected()
        if i is None:
            return
        result = ConditionDialog.ask(self.editor, {"cond": self.sub_conds[i]}, mode="cond", parent=self.top)
        self._regrab()
        if result:
            self.sub_conds[i] = result["cond"]
            self._refresh_subs(i)

    def on_sub_delete(self) -> None:
        i = self._sub_selected()
        if i is None:
            return
        del self.sub_conds[i]
        self._refresh_subs(min(i, len(self.sub_conds) - 1))

    # ---- 화면 작업 ----
    def _screen_windows(self):
        return list(dict.fromkeys([self.top, self.parent, self.editor.top, self.gui.root]))

    def on_crop(self, region_only: bool = False) -> None:
        def action(restore):
            grabber = self.gui.get_grabber()
            sw, sh = grabber.screen_size()
            shot = grabber.grab(0, 0, sw, sh)
            hint = ("검색할 영역을 드래그하세요" if region_only else "찾을 이미지를 드래그해서 잘라내세요") + " · Esc 취소"
            self.selector = RegionSelector(self.gui.root, shot,
                                           lambda rect: self._on_region(rect, shot, region_only, restore), hint)
        self.gui.run_screen_action(self._screen_windows(), "화면 캡처", action)

    def on_pick_region(self) -> None:
        self.on_crop(region_only=True)

    def _on_region(self, rect, shot, region_only: bool, restore) -> None:
        restore()
        self._regrab()
        if rect is None:
            return
        try:
            ox, oy = self.editor.coord_origin()
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        sh, sw = shot.shape[:2]
        if region_only:
            area = rect
        else:
            x, y, w, h = rect
            name = self.editor.new_template_name()
            vision.save_png(shot[y:y + h, x:x + w], self.editor.assets_dir / name)
            self.v_template.set(name)
            self.v_image_src.set("file")
            self._update_thumb()
            area = search_region_around(rect, sw, sh)
        for v, value in zip(self.v_region, (area[0] - ox, area[1] - oy, area[2], area[3])):
            v.set(str(value))
        self.v_full.set(False)
        self.error.configure(text="")
        self._on_kind()

    def on_pick_color(self) -> None:
        """범위를 드래그하면 그 범위에서 가장 많은 색을 목표 색으로 정한다."""
        def action(restore):
            grabber = self.gui.get_grabber()
            sw, sh = grabber.screen_size()
            shot = grabber.grab(0, 0, sw, sh)
            self.selector = RegionSelector(self.gui.root, shot, lambda rect: self._on_color_region(rect, shot, restore),
                                           "색을 확인할 범위를 드래그하세요 · Esc 취소")
        self.gui.run_screen_action(self._screen_windows(), "화면 캡처", action)

    def _on_color_region(self, rect, shot, restore) -> None:
        restore()
        self._regrab()
        if rect is None:
            return
        try:
            ox, oy = self.editor.coord_origin()
            tol = int(self.v_tol.get())
        except ValueError as e:
            self.error.configure(text=str(e) if "창" in str(e) else "허용 오차를 숫자로 입력하세요")
            return
        x, y, w, h = rect
        color, share = vision.dominant_color(shot[y:y + h, x:x + w], tol)
        for v, value in zip((self.v_px, self.v_py, self.v_pw, self.v_ph), (x - ox, y - oy, w, h)):
            v.set(str(value))
        self.v_color.set(color)
        self.color_info.configure(text=f"가장 많은 색 {color} · 범위의 {share * 100:.0f}%")
        self.error.configure(text="")

    def on_test(self) -> None:
        try:
            item = self._build_item()
            origin = self.editor.coord_origin()
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        self.error.configure(text="")

        cond = item["cond"]

        def action(restore):
            try:
                checker = vision.Vision(self.editor.assets_dir, self.gui.get_grabber())
                for var, tpl in em.image_defs(self.editor.items).items():
                    checker.set_image(var, checker.template(tpl))
                defs = em.variable_defs(self.editor.items)
                with checker.frame(cond, origin, defs):
                    m = vision.evaluate(cond, lambda c: checker.check(c, origin), defs)
                text = "✔ 충족" if m.matched else "✘ 불충족"
                if cond["kind"] in vision.SCREEN_KINDS:
                    measure = "색 비율" if cond["kind"] == "pixel" else "일치도"
                    text += f" · {measure} {m.score * 100:.0f}%"
                if m.pos:
                    text += f" · 찾은 위치 ({m.pos[0] - origin[0]}, {m.pos[1] - origin[1]})"
                if vision.variables_used([item]) - set(defs):
                    text += " · 지정 안 된 변수는 거짓으로 가정"
                if vision.image_vars_used([item]) - set(checker.images):
                    text += " · 재생 중 캡처하는 이미지 변수는 거짓으로 가정"
                self.test_label.configure(text=text, foreground="#2e7d32" if m.matched else "#c62828")
            except Exception as e:
                self.test_label.configure(text=f"오류: {e}", foreground="#c62828")
            restore()
            self._regrab()
        self.gui.run_screen_action(self._screen_windows(), "화면 확인", action)

    def _regrab(self) -> None:
        if self._modal and self.top.winfo_exists():
            try:
                self.top.grab_set()
            except tk.TclError:
                pass

    # ---- 결과 ----
    def _cond_kw(self) -> dict:
        kind = self.v_kind.get()
        if kind == "var":
            if not self.v_var.get().strip():
                raise ValueError("변수 이름을 고르거나 입력하세요")
            return dict(kind="var", name=self.v_var.get(), negate=self.v_negate.get())
        if kind == "group":
            if len(self.sub_conds) < 2:
                raise ValueError("'추가…'로 조건을 2개 이상 넣으세요")
            return dict(kind=self.v_group_op.get(), conds=self.sub_conds, negate=self.v_negate.get())
        by_var = kind == "image" and self.v_image_src.get() == "var"
        if by_var and not self.v_image_var.get().strip():
            raise ValueError("이미지 변수를 고르거나 입력하세요")
        if kind == "image" and not by_var and not self.v_template.get():
            raise ValueError("먼저 '화면에서 잘라내기'로 찾을 이미지를 지정하세요")
        region = None if self.v_full.get() else [v.get() for v in self.v_region]
        return dict(kind=kind, template=self.v_template.get(), image_var=self.v_image_var.get() if by_var else "",
                    region=region,
                    threshold_pct=self.v_threshold.get(), x=self.v_px.get(), y=self.v_py.get(),
                    w=self.v_pw.get(), h=self.v_ph.get(), ratio_pct=self.v_ratio.get(),
                    color=self.v_color.get(), tolerance=self.v_tol.get(), negate=self.v_negate.get())

    def _build_item(self) -> dict:
        if self.v_kind.get() == "count":
            return em.build_loop(loop_kind="count", delay_ms=self.v_delay.get(), count=self.v_count.get())
        cond_kw = self._cond_kw()
        wait_kw = dict(timeout_s=self.v_timeout.get(), on_timeout=self.v_on_timeout.get(),
                       interval_ms=self.v_interval.get())
        delay = self.v_delay.get()
        if self.mode == "cond":
            return {"cond": em.build_condition(**cond_kw)}
        if self.mode == "set_var":
            return em.build_set_var(target=self.v_name.get(), delay_ms=delay, **cond_kw)
        if self.mode == "wait":
            return em.build_wait_until(delay_ms=delay, **wait_kw, **cond_kw)
        if self.mode == "click":
            return em.build_click_image(delay_ms=delay, button=self.v_button.get(), offset_x=self.v_off_x.get(),
                                        offset_y=self.v_off_y.get(), hold_ms=self.v_hold.get(), **wait_kw, **cond_kw)
        if self.mode == "loop":
            return em.build_loop(loop_kind="cond", delay_ms=delay, **cond_kw)
        item = em.build_check("if_start" if self.mode == "if" else "break_if", delay_ms=delay, **cond_kw)
        if self.mode == "if" and not self.editing:
            item["with_else"] = self.v_with_else.get()  # 감쌀 때만 쓰는 값 (저장되지 않음)
        return item

    def _on_ok(self) -> None:
        try:
            self.result = self._build_item()
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        self.top.destroy()

    @classmethod
    def ask(cls, editor: "EditorWindow", item: dict | None = None, mode: str = "wait", parent=None) -> dict | None:
        dlg = cls(editor, item, mode, parent)
        dlg._modal = True
        dlg.top.grab_set()
        dlg.top.wait_window()
        return dlg.result


class ImageVarDialog:
    """이미지 변수 항목 추가/수정. 잘라낸 이미지 파일을 이름으로 담거나(여러 조건에서 재사용),
    재생 중 그 시점의 화면 영역을 캡처해 담는다. result: 편집 항목 (취소 시 None)."""

    def __init__(self, editor: "EditorWindow", item: dict | None = None) -> None:
        self.editor, self.gui = editor, editor.gui
        self.result: dict | None = None
        self._modal = False
        item = item or {}
        self.top = tk.Toplevel(editor.top)
        self.top.withdraw()
        self.top.title(f"이미지 변수 {'수정' if item else '추가'}")
        self.top.transient(editor.top)
        self.top.resizable(False, False)
        self.v_name = tk.StringVar(self.top, value=item.get("name", ""))
        self.v_source = tk.StringVar(self.top, value="capture" if "capture" in item else "file")
        self.v_template = tk.StringVar(self.top, value=item.get("template", ""))
        self.v_rect = [tk.StringVar(self.top, value=str(v)) for v in item.get("capture", [0, 0, 1, 1])]
        self.v_delay = tk.StringVar(self.top, value=str(round(item.get("dt", 0) * 1000)))
        self._thumb = None
        self._build()
        self._on_source()
        center_on_parent(self.top, editor.top)

    def _build(self) -> None:
        f = ttk.Frame(self.top, padding=10)
        f.pack(fill="both")
        ttk.Label(f, text="이미지에 이름을 붙여 둡니다. 이미지 조건·이미지 클릭에서 '이미지 변수'로 골라 찾습니다. "
                          "지정 전에 쓰이면 찾지 못한 것(거짓)으로 봅니다.", wraplength=440).pack(anchor="w", pady=(0, 4))
        nrow = ttk.Frame(f)
        nrow.pack(fill="x", pady=(0, 4))
        ttk.Label(nrow, text="변수 이름").pack(side="left")
        ttk.Combobox(nrow, textvariable=self.v_name, width=18,
                     values=em.image_variables_in(self.editor.items)).pack(side="left", padx=4)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="이미지").pack(side="left")
        for val, text in (("file", "잘라낸 이미지 (고정)"), ("capture", "재생 중 화면 캡처")):
            ttk.Radiobutton(row, text=text, value=val, variable=self.v_source,
                            command=self._on_source).pack(side="left", padx=4)

        self.f_file = ttk.LabelFrame(f, text="잘라낸 이미지", padding=6)
        self.thumb = tk.Label(self.f_file, text="(이미지 없음)", width=24, height=4, relief="sunken", bg="#eeeeee")
        self.thumb.pack(side="left")
        btns = ttk.Frame(self.f_file)
        btns.pack(side="left", padx=8)
        ttk.Button(btns, text="📷 화면에서 잘라내기", command=lambda: self._pick(crop=True)).pack(fill="x")
        ttk.Label(btns, textvariable=self.v_template, foreground="#555").pack(anchor="w", pady=4)

        self.f_capture = ttk.LabelFrame(f, text="재생 중 화면 캡처", padding=6)
        ttk.Label(self.f_capture, text="재생 중 이 항목에 도달한 순간 아래 영역을 캡처해 담습니다 "
                                       "(예: 처음 화면을 기억해 두고, 바뀌면 break).",
                  wraplength=420).pack(anchor="w")
        reg = ttk.Frame(self.f_capture)
        reg.pack(fill="x", pady=(6, 0))
        ttk.Button(reg, text="영역 드래그", command=lambda: self._pick(crop=False)).pack(side="left", padx=(0, 8))
        for label, v in zip(("X", "Y", "너비", "높이"), self.v_rect):
            ttk.Label(reg, text=label).pack(side="left", padx=(0, 1))
            ttk.Entry(reg, textvariable=v, width=6).pack(side="left", padx=(0, 6))

        self.f_common = ttk.Frame(f)
        ttk.Label(self.f_common, text="앞 지연(ms)").pack(side="left")
        ttk.Entry(self.f_common, textvariable=self.v_delay, width=8).pack(side="left", padx=6)
        self.error = ttk.Label(f, foreground="#c62828", wraplength=420)
        self.f_buttons = ttk.Frame(f)
        ttk.Button(self.f_buttons, text="확인", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(self.f_buttons, text="취소", command=self.top.destroy).pack(side="left")
        self.top.bind("<Escape>", lambda e: self.top.destroy())

    def _on_source(self) -> None:
        for w in (self.f_file, self.f_capture, self.f_common, self.error, self.f_buttons):
            w.pack_forget()
        (self.f_file if self.v_source.get() == "file" else self.f_capture).pack(fill="x", pady=4)
        self.f_common.pack(fill="x", pady=4)
        self.error.pack(anchor="w")
        self.f_buttons.pack(pady=(8, 0))
        self._update_thumb()

    def _update_thumb(self) -> None:
        name = self.v_template.get()
        self._thumb = show_thumb(self.thumb, self.top, self.editor.assets_dir / name if name else None,
                                 "(이미지 없음)" if not name else f"(파일 없음: {name})")

    def _pick(self, crop: bool) -> None:
        """crop: 이미지를 잘라 파일로 저장 / 아니면 재생 중 캡처할 영역만 고른다."""
        def action(restore):
            grabber = self.gui.get_grabber()
            sw, sh = grabber.screen_size()
            shot = grabber.grab(0, 0, sw, sh)
            hint = "담을 이미지를 드래그해서 잘라내세요" if crop else "재생 중 캡처할 영역을 드래그하세요"
            self.selector = RegionSelector(self.gui.root, shot,
                                           lambda rect: self._on_region(rect, shot, crop, restore), hint + " · Esc 취소")
        self.gui.run_screen_action(list(dict.fromkeys([self.top, self.editor.top, self.gui.root])), "화면 캡처", action)

    def _on_region(self, rect, shot, crop: bool, restore) -> None:
        restore()
        if self._modal and self.top.winfo_exists():
            try:
                self.top.grab_set()
            except tk.TclError:
                pass
        if rect is None:
            return
        x, y, w, h = rect
        if crop:
            name = self.editor.new_template_name()
            vision.save_png(shot[y:y + h, x:x + w], self.editor.assets_dir / name)
            self.v_template.set(name)
            self._update_thumb()
        else:
            try:
                ox, oy = self.editor.coord_origin()
            except ValueError as e:
                self.error.configure(text=str(e))
                return
            for v, value in zip(self.v_rect, (x - ox, y - oy, w, h)):
                v.set(str(value))
        self.error.configure(text="")

    def _on_ok(self) -> None:
        x, y, w, h = (v.get() for v in self.v_rect)
        try:
            self.result = em.build_set_image(target=self.v_name.get(), delay_ms=self.v_delay.get(),
                                             source=self.v_source.get(), template=self.v_template.get(),
                                             x=x, y=y, w=w, h=h)
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        self.top.destroy()

    @classmethod
    def ask(cls, editor: "EditorWindow", item: dict | None = None) -> dict | None:
        dlg = cls(editor, item)
        dlg._modal = True
        dlg.top.grab_set()
        dlg.top.wait_window()
        return dlg.result


def close_popdowns(widget, path: str | None = None) -> None:
    """widget 안 콤보박스의 펼침 목록(popdown)을 없앤다. 한 번 펼친 목록은 대화상자의 transient 창으로 남아,
    대화상자를 숨겼다 되돌리면 Tk 가 대화상자를 다시 숨겨 버린다 (모달이면 프로그램이 멈춘 것처럼 보임).
    목록은 다음에 펼칠 때 새로 만들어진다."""
    call = widget.tk.call
    for child in widget.tk.splitlist(call("winfo", "children", path or str(widget))):
        if call("winfo", "class", child) == "ComboboxPopdown":
            call("destroy", child)
        else:
            close_popdowns(widget, child)


def show_thumb(label, master, path, missing: str):
    """label 에 path 이미지의 축소본을 보인다 (없으면 missing 문구). 돌려준 PhotoImage 는 호출한 쪽이 붙잡아 둔다."""
    # 이미지를 보이면 크기 단위가 픽셀이 되므로, 글자로 돌아갈 때 처음 크기(글자 단위)로 되돌린다
    text_size = label.__dict__.setdefault("_text_size", dict(width=label.cget("width"), height=label.cget("height")))
    if path is None or not path.is_file():
        label.configure(image="", text=missing, **text_size)
        return None
    try:
        img = tk.PhotoImage(master=master, file=str(path))
    except tk.TclError:
        label.configure(image="", text=f"(미리보기 불가: {path.name})", **text_size)
        return None
    factor = max(1, -(-img.width() // 170), -(-img.height() // 64))
    thumb = img.subsample(factor) if factor > 1 else img
    label.configure(image=thumb, text="", width=170, height=64)
    return thumb


def overlay_xy(position: str, screen_w: int, screen_h: int, w: int, h: int, margin: int = 12) -> tuple[int, int]:
    """8방향 위치 -> 오버레이 좌상단 좌표 (주 모니터 기준)."""
    x = margin if "w" in position else screen_w - w - margin if "e" in position else (screen_w - w) // 2
    y = margin if "n" in position else screen_h - h - margin if "s" in position else (screen_h - h) // 2
    return x, y


class Overlay:
    """화면 가장자리에 뜨는 반투명 상태 표시. 항상 위, 테두리 없음, 클릭은 아래 창(게임)으로 통과한다."""

    def __init__(self, root, position: str = "off") -> None:
        self.root = root
        self.position = position
        self.top: tk.Toplevel | None = None
        self._shown: tuple | None = None
        self.visible = False

    def _create(self) -> None:
        top = self.top = tk.Toplevel(self.root)
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        try:
            top.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        self.label = tk.Label(top, fg="white", font=("", 11, "bold"), padx=12, pady=5)
        self.label.pack()
        top.update_idletasks()
        try:
            input_backend.make_click_through(int(top.wm_frame(), 16))
        except Exception:
            pass

    def set_position(self, position: str) -> None:
        self.position = position
        self._shown = None
        if position == "off":
            self.hide()

    def show(self, text: str, color: str) -> None:
        if self.position == "off":
            self.hide()
            return
        if self.top is None:
            self._create()
        if self._shown != (text, color):
            self._shown = (text, color)
            self.label.configure(text=text, bg=color)
            self.top.configure(bg=color)
            self.top.update_idletasks()
            x, y = overlay_xy(self.position, self.top.winfo_screenwidth(), self.top.winfo_screenheight(),
                              self.top.winfo_reqwidth(), self.top.winfo_reqheight())
            self.top.geometry(f"+{x}+{y}")
        if not self.visible:
            self.top.deiconify()
            self.top.attributes("-topmost", True)
            self.visible = True

    def hide(self) -> None:
        if self.top is not None and self.visible:
            self.top.withdraw()
        self.visible = False

    def destroy(self) -> None:
        if self.top is not None:
            self.top.destroy()
            self.top = None
        self.visible = False


def center_on_parent(top, parent) -> None:
    """작은 창을 부모 창 가운데에 놓는다 (화면 밖으로 나가지 않게). 숨겨 둔 창이면 위치를 잡은 뒤 보인다."""
    top.update_idletasks()
    parent.update_idletasks()
    w, h = top.winfo_reqwidth(), top.winfo_reqheight()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    if pw <= 1 or ph <= 1 or not parent.winfo_viewable():  # 부모가 아직 안 보이면 화면 가운데
        px, py, pw, ph = 0, 0, top.winfo_screenwidth(), top.winfo_screenheight()
    x = px + (pw - w) // 2
    y = py + (ph - h) // 2
    x = max(0, min(x, top.winfo_screenwidth() - w))
    y = max(0, min(y, top.winfo_screenheight() - h))
    top.geometry(f"+{x}+{y}")
    top.deiconify()
    # 위치는 제목 표시줄을 포함한 바깥 테두리 기준이다 (Windows). 안쪽 영역이 가운데 오도록 테두리만큼 보정
    top.update_idletasks()
    dx, dy = top.winfo_rootx() - x, top.winfo_rooty() - y
    if 0 < dx < 50 or 0 < dy < 80:
        top.geometry(f"+{max(0, x - dx)}+{max(0, y - dy)}")


def restore_geometry(window, geometry: str) -> None:
    """저장된 'WxH+X+Y' 복원. 위치가 화면 밖이면(모니터 변경 등) 크기만 복원."""
    import re
    m = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", geometry or "")
    if not m:
        return
    w, h, x, y = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    if 0 <= x < sw - 50 and 0 <= y < sh - 50:
        window.geometry(f"{w}x{h}+{x}+{y}")
    else:
        window.geometry(f"{w}x{h}")


class HotkeyCaptureDialog:
    """누른 키(최대 2개 동시)를 핫키로 지정. result: 'ctrl+f1' 형식 (취소 시 None)."""

    def __init__(self, parent) -> None:
        self.result: str | None = None
        self.confirmed = False
        self._pending: list[str] = []
        self._down: set[str] = set()
        self.top = tk.Toplevel(parent)
        self.top.withdraw()  # 위치를 잡은 뒤 보인다
        self.top.title("핫키 지정")
        self.top.transient(parent)
        self.top.resizable(False, False)
        f = ttk.Frame(self.top, padding=12)
        f.pack(fill="both")
        ttk.Label(f, text="지정할 키를 누르세요.\n두 키를 함께 누르면 조합 핫키가 됩니다 (예: Ctrl + F1).").pack()
        self.shown = ttk.Label(f, text="(입력 대기)", font=("", 14, "bold"), padding=10)
        self.shown.pack()
        btns = ttk.Frame(f)
        btns.pack()
        self.btn_ok = ttk.Button(btns, text="확인", command=self._ok, state="disabled")
        self.btn_ok.pack(side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.top.destroy).pack(side="left")
        self.top.bind("<KeyPress>", self.on_press)
        self.top.bind("<KeyRelease>", self.on_release)
        center_on_parent(self.top, parent)

    def _name(self, event) -> str | None:
        name = keys.name_from_tk(event.keysym, event.keycode, windows=sys.platform == "win32")
        return keys.hotkey_key(name) if name else None

    def on_press(self, event) -> str:
        name = self._name(event)
        if name and name not in self._down:
            if not self._down:
                self._pending = []  # 새 입력 시작
            self._down.add(name)
            if name not in self._pending and len(self._pending) < keys.MAX_HOTKEY_KEYS:
                self._pending.append(name)
            self.shown.configure(text=keys.format_hotkey(self._pending).upper().replace("+", " + "))
        return "break"

    def on_release(self, event) -> str:
        name = self._name(event)
        self._down.discard(name)
        if not self._down and self._pending:
            self.result = keys.format_hotkey(self._pending)
            self.btn_ok.state(["!disabled"])
        return "break"

    def _ok(self) -> None:
        self.confirmed = True
        self.top.destroy()

    @classmethod
    def ask(cls, parent) -> str | None:
        dlg = cls(parent)
        dlg.top.grab_set()
        dlg.top.focus_force()
        dlg.top.wait_window()
        return dlg.result if dlg.confirmed else None


def run_gui(app: App) -> int:
    if input_backend.IS_WINDOWS:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("rmia4.MacroTool")
    root = tk.Tk()
    gui = Gui(root, app, Settings(SETTINGS_PATH))
    gui.hotkey_listener = HotkeyListener(gui.hotkey_bindings(), gui.hotkey_suppressed)
    gui.hotkey_listener.start()
    gui.log("준비 완료. '+ 추가'로 새 매크로를 만들거나 목록에서 선택해 재생하세요.")
    if input_backend.IS_WINDOWS and not input_backend.is_admin():
        gui.log("[참고] 관리자 권한이 아닙니다. 게임이 관리자 권한으로 실행 중이면 입력이 무시되므로 "
                "이 프로그램도 관리자 권한으로 실행하세요.")
    try:
        root.mainloop()
    finally:
        gui.hotkey_listener.stop()
    return 0
