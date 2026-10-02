"""tkinter GUI. 로직은 main.App / editor_model 에 위임하고 화면 표시/입력만 담당한다.

- 메인 화면(Gui): 매크로 목록, 선택 재생/중지, 진행 상황, 추가/편집/복제/삭제
- 기록 화면(EditorWindow): 녹화, 이벤트 목록 개별 수정, 이벤트 추가, 핫키·반복 등 설정
- 이벤트 대화상자(EventDialog): 이벤트 하나(또는 클릭/키 입력 쌍) 추가·수정
"""
from __future__ import annotations

import copy
import queue
import sys
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import editor_model as em
import keys
from hotkeys import CONTROL_KEYS, HOTKEY_PLAY, HOTKEY_QUIT, HOTKEY_RECORD, HotkeyListener
from main import App
from player import PlayOptions, options_from_dict, options_to_dict, set_option
from profiles import BUTTONS, Macro
from settings import SETTINGS_PATH, Settings

# (옵션 이름, 라벨, 종류)  종류: entry | check | combo
PLAY_FIELDS = [
    ("repeat", "반복 횟수 (0=무한)", "entry"),
    ("speed", "속도 배율", "entry"),
    ("loop_delay", "반복 간 대기(초)", "entry"),
    ("mouse_mode", "마우스 모드", "combo"),
    ("time_jitter", "대기 시간 편차 ±%", "entry"),
    ("pos_jitter", "클릭 좌표 편차 ±px", "entry"),
    ("min_key_hold", "키 최소 유지(초)", "entry"),
    ("approach", "루프 시작 시 부드럽게 이동", "check"),
    ("approach_duration", "이동 최대 시간(초)", "entry"),
    ("scale_coords", "창 크기에 맞춰 좌표 조정", "check"),
    ("max_minutes", "최대 실행 시간(분, 0=무제한)", "entry"),
]
UNDO_LIMIT = 100
COLORS = {"idle": "#555555", "rec": "#c62828", "play": "#2e7d32", "wait": "#ef6c00"}
NO_HOTKEY = "(없음)"
COORD_LABELS = {"screen": "화면 기준", "window": "창 기준"}


class Gui:
    """메인 화면: 매크로 목록과 재생."""

    def __init__(self, root: tk.Tk, app: App, settings: Settings | None = None) -> None:
        self.root, self.app = root, app
        self.settings = settings or Settings(None)
        self._logs: queue.Queue[str] = queue.Queue()
        self._calls: queue.Queue = queue.Queue()  # 다른 스레드(핫키) -> Tk 스레드
        self._countdown_id = None
        self._countdown_owner: str | None = None  # "main" | "editor"
        self._countdown_left = 0
        self._countdown_what = ""
        self.macros_enabled = bool(self.settings["macros_enabled"])  # 전체 매크로 실행 가능 여부
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
        ttk.Button(top, text="단축키 변경", command=self.change_toggle_hotkey).pack(side="right", padx=(4, 0))
        self.btn_power = tk.Button(top, command=self.toggle_macros_enabled, width=26, pady=4,
                                   fg="white", relief="raised", font=("", 10, "bold"))
        self.btn_power.pack(side="right")
        self._update_power_button()

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
        for text, cmd in (("+ 추가", self.on_add), ("편집", self.on_edit), ("복제", self.on_duplicate),
                          ("삭제", self.on_delete), ("새로고침", self.reload)):
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=(0, 4))

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
        self.open_editor(None)

    def on_edit(self) -> None:
        name = self.selected()
        if name is not None:
            self.open_editor(name)

    def on_duplicate(self) -> None:
        name = self.selected()
        if name is None:
            return
        dup = Macro.from_dict(copy.deepcopy(self.app.library[name].to_dict()))
        dup.hotkey = None  # 핫키는 중복될 수 없다
        new = em.unique_name(f"{name} 복사", self.app.library)
        if self.guard(lambda: self.app.store(new, dup)):
            self.refresh_list(select=new)

    def on_delete(self) -> None:
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
        self.settings["macros_enabled"] = self.macros_enabled
        self.settings.save()
        self.log(f"매크로 실행 {'가능' if self.macros_enabled else '불가'} 상태")

    @property
    def toggle_hotkey(self) -> str:
        return self.settings["toggle_hotkey"]

    def _update_power_button(self) -> None:
        key = self.toggle_hotkey.upper().replace("+", " + ")
        if self.macros_enabled:
            self.btn_power.configure(text=f"● 매크로 실행 가능 ({key})", bg="#2e7d32", activebackground="#388e3c")
        else:
            self.btn_power.configure(text=f"○ 매크로 실행 불가 ({key})", bg="#757575", activebackground="#8a8a8a")

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

        prog = self.app.progress
        macro = self.app.playing_macro
        if prog is None or macro is None or not macro.events:
            self.progress["value"] = 0
            self.progress_label.configure(text="재생 대기")
            return
        loop, idx, repeat = prog
        total = len(macro.events)
        elapsed = time.monotonic() - self.play_started
        self.progress["value"] = (idx + 1) / total * 100
        self.progress_label.configure(
            text=f"{self.app.playing_name or ''} · 루프 {loop}/{repeat or '∞'} · "
                 f"이벤트 {idx + 1}/{total} · {elapsed:.1f}초")

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

        self.top = tk.Toplevel(gui.root)
        self.top.minsize(900, 560)
        restore_geometry(self.top, gui.settings["editor_geometry"])
        self._undo: list[list[dict]] = []
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
        self._build()
        self.refresh_tree()
        for var in [self.v_name, self.v_hotkey, self.v_title, self.v_coord, *self.v_opts.values()]:
            var.trace_add("write", lambda *a: self._mark_dirty())
        self.v_title.trace_add("write", lambda *a: self._auto_coord())
        self._update_title()
        self.refresh()

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

        left = ttk.LabelFrame(body, text="이벤트", padding=6)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.banner = tk.Label(left, text="대기", fg="white", bg=COLORS["idle"],
                               font=("", 12, "bold"), pady=5)
        self.banner.pack(fill="x", pady=(0, 6))
        bar = ttk.Frame(left)
        bar.pack(fill="x")
        self.btn_rec = ttk.Button(bar, text=f"● 녹화 ({HOTKEY_RECORD.upper()})", command=self.toggle_record)
        self.btn_rec.pack(side="left")
        self.btn_test = ttk.Button(bar, text="▶ 테스트 재생", command=self.test_play)
        self.btn_test.pack(side="left", padx=4)

        frame = ttk.Frame(left)
        frame.pack(fill="both", expand=True, pady=4)
        cols = ("no", "delay", "type", "detail")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=16, selectmode="extended")
        for col, text, width in zip(cols, ("#", "앞 지연(ms)", "종류", "내용"), (50, 90, 125, 200)):
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
        for text, kind in (("키 입력", "tap"), ("마우스 클릭", "click"), ("지연", "wait"), ("기타…", None)):
            ttk.Button(add, text=text, command=lambda k=kind: self.on_add(k)).pack(side="left", padx=2)
        edit = ttk.Frame(left)
        edit.pack(fill="x", pady=(4, 0))
        for text, cmd in (("수정", self.on_edit), ("삭제", self.on_delete),
                          ("▲ 위로", lambda: self.on_move(-1)), ("▼ 아래로", lambda: self.on_move(1))):
            ttk.Button(edit, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.btn_undo = ttk.Button(edit, text="↶ 되돌리기", command=self.undo)
        self.btn_redo = ttk.Button(edit, text="↷ 다시 실행", command=self.redo)
        self.btn_undo.pack(side="left", padx=(8, 4))
        self.btn_redo.pack(side="left")
        for seq, fn in (("<Control-z>", self.undo), ("<Control-Z>", self.redo), ("<Control-y>", self.redo)):
            self.top.bind(seq, lambda e, f=fn: self._shortcut(f))
        self.summary = ttk.Label(edit, foreground="#444")
        self.summary.pack(side="right")

        right = ttk.LabelFrame(body, text="매크로 설정", padding=6)
        right.pack(side="left", fill="y")
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
            elif kind == "combo":
                w = ttk.Combobox(right, textvariable=var, state="readonly",
                                 values=("absolute", "relative"), width=10)
            else:
                w = ttk.Entry(right, textvariable=var, width=10)
            w.grid(row=i, column=1, sticky="w", padx=6)
        ttk.Label(right, foreground="#666", wraplength=260, justify="left",
                  text="대상 창 제목을 넣으면 그 창이 앞에 있을 때만 입력을 보내고, 좌표를 창 기준으로 저장합니다. "
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
        for i, it in enumerate(self.items):
            self.tree.insert("", "end", iid=str(i), values=(
                i + 1, round(it.get("dt", 0) * 1000), em.EVENT_LABELS[it["type"]], em.describe(it)))
        shown = [str(i) for i in (select or []) if self.tree.exists(str(i))]
        if shown:
            self.tree.selection_set(shown)
            self.tree.see(shown[-1])
        self.summary.configure(text=f"항목 {len(self.items)}개 (이벤트 {em.event_count(self.items)}개) · "
                                    f"{em.total_duration(self.items):.2f}초")
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

    def on_add(self, kind: str | None = None) -> None:
        result = EventDialog.ask(self.top, em.ADD_KINDS, kind=kind or "tap", pick=self.pick_position)
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
        kinds = em.PATH_KINDS if self.items[i]["type"] == "path" else em.EDIT_KINDS
        result = EventDialog.ask(self.top, kinds, item=self.items[i], pick=self.pick_position)
        if result:
            self._snapshot()
            self.items[i:i + 1] = result
            self._changed([i])

    def on_delete(self) -> None:
        sel = self.selected_indices()
        if not sel:
            return
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
        self._snapshot()
        self.items.insert(j, self.items.pop(i))
        self._changed([j])

    def pick_position(self) -> tuple[int, int]:
        """현재 커서 위치를 이 매크로의 좌표 기준으로 반환."""
        x, y = self.app.backend.cursor_pos()
        if self.coord_space == "window":
            title = self.v_title.get().strip()
            rect = self.app.backend.find_window_rect(title) if title else None
            if rect is None:
                raise ValueError("창 기준 좌표: 대상 창을 찾을 수 없습니다")
            x, y = x - rect[0], y - rect[1]
        return x, y

    # ---- 녹화 / 테스트 재생 ----
    def toggle_record(self, immediate: bool = False) -> None:
        if self.gui.cancel_countdown():
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
        start = lambda: self.gui.guard(lambda: self.app.start_record(title, coord, ignore))
        start() if immediate else self.gui.start_after_delay(start, owner="editor", what="녹화")

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
        self.gui.start_after_delay(lambda: self.gui.guard(lambda: self.app.start_play(macro, opts, label)),
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
                                      reserved=(self.gui.toggle_hotkey,))
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
            saved = self.app.store(name, macro, self.old_name)
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
            text, color = f"● 녹화 중 — {HOTKEY_RECORD.upper()}로 종료 (목록 끝에 추가됨)", "rec"
        elif playing:
            text, color = f"▶ 재생 중 — {self.app.playing_name or ''}", "play"
        else:
            text, color = "대기", "idle"
        self.banner.configure(text=text, bg=COLORS[color])


class EventDialog:
    """이벤트 추가/수정 대화상자. result: 편집 항목 리스트 (취소 시 None)."""

    def __init__(self, parent, kinds: list[tuple[str, str]], item: dict | None = None,
                 kind: str = "tap", pick=None) -> None:
        self.kinds = kinds
        self.pick = pick
        self.item = item
        self.result: list[dict] | None = None
        init = em.item_fields(item) if item else {}
        kind = init.get("kind", kind)
        labels = dict(kinds)
        self.top = tk.Toplevel(parent)
        self.top.title("이벤트 수정" if item else "이벤트 추가")
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.v_kind = tk.StringVar(self.top, value=labels.get(kind, kinds[0][1]))
        self.v = {k: tk.StringVar(self.top, value=str(init.get(k, default))) for k, default in (
            ("delay_ms", 100), ("key", ""), ("button", "left"), ("x", 0), ("y", 0),
            ("dx", 0), ("dy", -1), ("hold_ms", ""), ("duration_ms", 0))}
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
        row(2, "key", "키", ttk.Combobox(f, textvariable=self.v["key"], values=em.KEY_CHOICES, width=14))
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
        row(7, "hold", "누름 유지(ms)", ttk.Entry(f, textvariable=self.v["hold_ms"], width=10))
        row(8, "duration", "이동 시간(ms)", ttk.Entry(f, textvariable=self.v["duration_ms"], width=10))
        self.error = ttk.Label(f, foreground="#c62828", wraplength=320)
        self.error.grid(row=9, column=0, columnspan=2, sticky="w")
        btns = ttk.Frame(f)
        btns.grid(row=10, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="확인", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.top.destroy).pack(side="left")
        self.top.bind("<Return>", lambda e: self._on_ok())
        self.top.bind("<Escape>", lambda e: self.top.destroy())

    @property
    def kind(self) -> str:
        label = self.v_kind.get()
        return next(k for k, lab in self.kinds if lab == label)

    def _on_kind(self) -> None:
        kind = self.kind
        fields = set(em.KIND_FIELDS[kind])
        if "cursor" in fields and self.v_cursor.get():
            fields.discard("pos")  # 현재 커서 위치 사용 시 좌표 입력 숨김
        for field, widgets in self.rows.items():
            for w in widgets:
                w.grid() if field in fields else w.grid_remove()
        self.delay_label.configure(text="지연(ms)" if kind == "wait" else "앞 지연(ms)")
        self.rows["pos"][0].configure(text="끝 위치 X, Y" if kind == "path" else "X, Y")
        if "hold" in fields and not self.v["hold_ms"].get():
            self.v["hold_ms"].set(str(em.DEFAULT_HOLD_MS[kind]))

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
                                         duration_ms=v["duration_ms"],
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
    root = tk.Tk()
    gui = Gui(root, app, Settings(SETTINGS_PATH))
    gui.hotkey_listener = HotkeyListener(gui.hotkey_bindings(), gui.hotkey_suppressed)
    gui.hotkey_listener.start()
    gui.log("준비 완료. '+ 추가'로 새 매크로를 만들거나 목록에서 선택해 재생하세요.")
    try:
        root.mainloop()
    finally:
        gui.hotkey_listener.stop()
    return 0
