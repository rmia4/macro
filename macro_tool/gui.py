"""tkinter GUI. 로직은 main.App 에 위임하고 화면 표시/입력만 담당한다.

메인 창: 녹화, 매크로 목록(저장/삭제), 로그.  재생 창: 이벤트 목록, 재생 옵션, 진행 상황.
"""
from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from hotkeys import HOTKEY_PLAY, HOTKEY_QUIT, HOTKEY_RECORD, HotkeyListener
from main import App
from player import set_option
from profiles import MacroFormatError, list_macros

# (옵션 이름, 라벨, 종류)  종류: entry | check | combo
PLAY_FIELDS = [
    ("mouse_mode", "마우스 모드", "combo"),
    ("repeat", "반복 횟수 (0=무한)", "entry"),
    ("speed", "속도 배율", "entry"),
    ("loop_delay", "반복 간 대기(초)", "entry"),
    ("time_jitter", "대기 시간 편차 ±%", "entry"),
    ("pos_jitter", "클릭 좌표 편차 ±px", "entry"),
    ("min_key_hold", "키 최소 유지(초)", "entry"),
    ("approach", "루프 시작 시 부드럽게 이동", "check"),
    ("approach_duration", "이동 최대 시간(초)", "entry"),
    ("scale_coords", "창 크기에 맞춰 좌표 조정", "check"),
]
OPTION_FIELDS = [("window_title", "대상 창 제목 (포커스 제한)", "entry")] + PLAY_FIELDS
COLORS = {"idle": "#555555", "rec": "#c62828", "play": "#2e7d32", "wait": "#ef6c00"}
EVENT_LABELS = {"move": "이동", "mdown": "버튼 누름", "mup": "버튼 뗌",
                "scroll": "스크롤", "kdown": "키 누름", "kup": "키 뗌"}


def describe(ev: dict) -> str:
    typ = ev["type"]
    if typ in ("kdown", "kup"):
        return ev["key"]
    pos = f"({ev['x']}, {ev['y']})"
    if typ in ("mdown", "mup"):
        return f"{ev['button']} {pos}"
    if typ == "scroll":
        return f"dx={ev['dx']} dy={ev['dy']} {pos}"
    return pos


class Gui:
    def __init__(self, root: tk.Tk, app: App) -> None:
        self.root, self.app = root, app
        self._logs: queue.Queue[str] = queue.Queue()
        self._calls: queue.Queue = queue.Queue()  # 다른 스레드(핫키) -> Tk 스레드
        self._countdown_id = None
        self._after_id = None
        self._was_recording = False
        self._closed = False
        self.play_started: float | None = None
        self.playback: PlaybackWindow | None = None
        app.log = self._logs.put
        # 옵션 변수는 root 소유: 재생 창을 닫아도 값이 유지된다
        self._vars: dict[str, tk.Variable] = {
            name: tk.BooleanVar(root) if kind == "check" else tk.StringVar(root)
            for name, _, kind in OPTION_FIELDS}
        self.delay = tk.StringVar(root, value="3")
        root.title("매크로 도구")
        root.minsize(620, 480)
        self._build()
        self._load_options_into_form()
        self.refresh_list()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll()

    # ---- 화면 구성 ----
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.status = tk.Label(top, text="대기 중", fg="white", bg=COLORS["idle"],
                               font=("", 13, "bold"), width=16, pady=6)
        self.status.pack(side="left")
        self.btn_rec = ttk.Button(top, text=f"● 녹화 ({HOTKEY_RECORD.upper()})", command=self.on_record)
        self.btn_stop = ttk.Button(top, text="■ 중지", command=self.on_stop)
        for b in (self.btn_rec, self.btn_stop):
            b.pack(side="left", padx=4)
        ttk.Label(top, text="시작 지연(초)").pack(side="left", padx=(16, 2))
        ttk.Spinbox(top, from_=0, to=30, width=4, textvariable=self.delay).pack(side="left")

        row = ttk.Frame(self.root, padding=(8, 0))
        row.pack(fill="x")
        ttk.Label(row, text="대상 창 제목").pack(side="left")
        ttk.Entry(row, textvariable=self._vars["window_title"]).pack(
            side="left", fill="x", expand=True, padx=6)

        ttk.Label(self.root, foreground="#666", padding=(8, 4), wraplength=600,
                  text=f"버튼으로 시작하면 지연 후 시작되므로 그 사이 게임 창으로 전환하세요. "
                       f"녹화 종료는 {HOTKEY_RECORD.upper()} 키 권장 (버튼 클릭이 기록됩니다). "
                       f"{HOTKEY_PLAY.upper()} = 재생/중지, {HOTKEY_QUIT.upper()} = 종료.").pack(fill="x")

        box = ttk.LabelFrame(self.root, text="매크로", padding=6)
        box.pack(fill="both", expand=True, padx=8)
        self.listbox = tk.Listbox(box, height=8, exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda e: self.open_playback())
        self.listbox.bind("<Delete>", lambda e: self.on_delete())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="▶ 재생 창 열기", command=self.open_playback).pack(side="left")
        ttk.Button(row, text="삭제", command=self.on_delete).pack(side="left", padx=4)
        ttk.Button(row, text="새로고침", command=self.refresh_list).pack(side="left")
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="현재 매크로 저장 이름").pack(side="left")
        self.name = tk.StringVar(self.root)
        ttk.Entry(row, textvariable=self.name).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="저장", command=self.on_save).pack(side="left")
        self.info = ttk.Label(box, text="매크로: (없음)", foreground="#333")
        self.info.pack(anchor="w", pady=(6, 0))

        self.log_box = scrolledtext.ScrolledText(self.root, height=8, state="disabled")
        self.log_box.pack(fill="both", padx=8, pady=8)

    # ---- 옵션 ----
    def _load_options_into_form(self) -> None:
        for name, var in self._vars.items():
            value = getattr(self.app.options, name)
            var.set(value if isinstance(var, tk.BooleanVar) else str(value))

    def apply_options(self, notify: bool = False) -> bool:
        errors = []
        for name, var in self._vars.items():
            raw = ("on" if var.get() else "off") if isinstance(var, tk.BooleanVar) else str(var.get()).strip()
            if name == "window_title" and not raw:
                raw = "none"
            try:
                set_option(self.app.options, name, raw)
            except ValueError as e:
                errors.append(f"{name}: {e}")
        if errors:
            messagebox.showerror("옵션 오류", "\n".join(errors))
            return False
        if notify:
            self.log("옵션을 적용했습니다")
        return True

    # ---- 동작 ----
    def log(self, msg: str) -> None:
        self._logs.put(msg)

    @property
    def counting_down(self) -> bool:
        return self._countdown_id is not None

    def _cancel_countdown(self) -> bool:
        if self._countdown_id is None:
            return False
        self.root.after_cancel(self._countdown_id)
        self._countdown_id = None
        self.log("시작 취소")
        return True

    def _start_after_delay(self, action) -> None:
        try:
            delay = max(0, int(float(self.delay.get())))
        except ValueError:
            delay = 3

        def tick(n: int) -> None:
            if n == 0:
                self._countdown_id = None
                action()
                return
            self.log(f"{n}초 후 시작...")
            self._countdown_id = self.root.after(1000, tick, n - 1)

        tick(delay)

    def _guard(self, fn) -> None:
        try:
            fn()
        except Exception as e:
            self.log(f"오류: {e}")

    def on_record(self, immediate: bool = False) -> None:
        if self._cancel_countdown():
            return
        if self.app.recording:
            self._guard(self.app.toggle_record)
        elif self.apply_options():
            start = lambda: self._guard(self.app.toggle_record)
            start() if immediate else self._start_after_delay(start)

    def on_play(self, immediate: bool = False) -> None:
        if self._cancel_countdown():
            return
        if self.app.playing:
            self._guard(self.app.toggle_play)
        elif self.apply_options():
            start = lambda: self._guard(self.app.toggle_play)
            start() if immediate else self._start_after_delay(start)

    def on_stop(self) -> None:
        if self._cancel_countdown():
            return
        if self.app.playing:
            self._guard(self.app.toggle_play)
        elif self.app.recording:
            self._guard(self.app.toggle_record)

    def _selected(self) -> str | None:
        sel = self.listbox.curselection()
        return self.listbox.get(sel[0]) if sel else None

    def on_save(self) -> None:
        name = self.name.get().strip()
        if not name:
            messagebox.showinfo("저장", "저장할 이름을 입력하세요")
            return
        try:
            self.app.save(name)
        except (ValueError, OSError) as e:
            messagebox.showerror("저장 실패", str(e))
        self.refresh_list()

    def on_delete(self) -> None:
        name = self._selected()
        if not name:
            messagebox.showinfo("삭제", "목록에서 삭제할 매크로를 선택하세요")
            return
        if not messagebox.askyesno("삭제 확인", f"'{name}' 매크로를 삭제할까요?\n되돌릴 수 없습니다."):
            return
        try:
            self.app.delete(name)
        except (ValueError, OSError) as e:
            messagebox.showerror("삭제 실패", str(e))
        self.refresh_list()

    def load_selected(self) -> bool:
        name = self._selected()
        if name is None:
            return False
        if self.app.playing and name != self.app.macro_name:
            messagebox.showinfo("불러오기", "재생 중에는 다른 매크로를 불러올 수 없습니다")
            return False
        try:
            self.app.load(name)
            self.name.set(name)
            return True
        except (ValueError, OSError, MacroFormatError) as e:
            messagebox.showerror("불러오기 실패", str(e))
            return False

    def open_playback(self) -> None:
        """선택한 매크로(선택이 없으면 현재 매크로)로 재생 창을 연다."""
        if self._selected() is not None and not self.load_selected():
            return
        if self.app.macro is None:
            messagebox.showinfo("재생", "녹화하거나 목록에서 매크로를 선택하세요")
            return
        if self.playback is None:
            self.playback = PlaybackWindow(self)
        else:
            self.playback.show()

    def refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        for n in list_macros(self.app.macros_dir):
            self.listbox.insert("end", n)

    # ---- 핫키 (다른 스레드에서 호출됨) ----
    def hotkey_bindings(self) -> dict:
        def post(fn):
            return lambda: self._calls.put(fn)
        return {HOTKEY_RECORD: post(lambda: self.on_record(True)),
                HOTKEY_PLAY: post(lambda: self.on_play(True)),
                HOTKEY_QUIT: post(self.close)}

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
        if self.playback is not None:
            self.playback.refresh()
        self._after_id = self.root.after(50, self._poll)

    def _refresh_status(self) -> None:
        if self._was_recording and not self.app.recording:
            self.listbox.selection_clear(0, "end")  # 새 녹화가 '재생 창 열기' 대상이 되도록
            self.name.set("")
        self._was_recording = self.app.recording
        if self.app.playing:
            self.play_started = self.play_started or time.monotonic()
        else:
            self.play_started = None
        if self.counting_down:
            text, color = "시작 대기 중…", "wait"
        elif self.app.recording:
            text, color = "● 녹화 중", "rec"
        elif self.app.playing:
            text, color = "▶ 재생 중", "play"
        else:
            text, color = "대기 중", "idle"
        self.status.configure(text=text, bg=COLORS[color])
        busy = self.app.recording or self.app.playing
        self.btn_rec.state(["disabled"] if self.app.playing else ["!disabled"])
        self.btn_stop.state(["!disabled"] if busy or self.counting_down else ["disabled"])
        m = self.app.macro
        name = self.app.macro_name or "저장 안 됨"
        self.info.configure(text=f"매크로: {name} · 이벤트 {len(m.events)}개, {m.duration:.2f}초"
                            if m else "매크로: (없음)")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.app.shutdown()
        self.root.destroy()


class PlaybackWindow:
    """매크로 재생 전용 창: 이벤트 목록, 재생 옵션, 진행 상황."""

    def __init__(self, gui: Gui) -> None:
        self.gui, self.app = gui, gui.app
        self.top = tk.Toplevel(gui.root)
        self.top.minsize(760, 460)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.hide_moves = tk.BooleanVar(self.top, value=True)
        self._macro = None
        self._visible_at: list[str | None] = []
        self._last_idx: int | None = None
        self._build()
        self.load_macro()

    def _build(self) -> None:
        head = ttk.Frame(self.top, padding=8)
        head.pack(fill="x")
        self.title_label = ttk.Label(head, font=("", 12, "bold"))
        self.title_label.pack(anchor="w")
        self.info = ttk.Label(head, foreground="#444")
        self.info.pack(anchor="w")

        bar = ttk.Frame(self.top, padding=(8, 0))
        bar.pack(fill="x")
        self.btn_play = ttk.Button(bar, text=f"▶ 재생 ({HOTKEY_PLAY.upper()})", command=self.gui.on_play)
        self.btn_stop = ttk.Button(bar, text="■ 중지", command=self.gui.on_stop)
        self.btn_play.pack(side="left")
        self.btn_stop.pack(side="left", padx=4)
        self.progress = ttk.Progressbar(bar, maximum=100, length=220)
        self.progress.pack(side="left", padx=8)
        self.state_label = ttk.Label(bar, text="재생 대기")
        self.state_label.pack(side="left")

        body = ttk.Frame(self.top, padding=8)
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(body, text="이벤트", padding=6)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ttk.Checkbutton(left, text="마우스 이동 숨기기", variable=self.hide_moves,
                        command=self.load_macro).pack(anchor="w")
        frame = ttk.Frame(left)
        frame.pack(fill="both", expand=True)
        cols = ("no", "t", "type", "detail")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=14, selectmode="browse")
        for col, text, width in zip(cols, ("#", "시간(초)", "종류", "내용"), (50, 70, 80, 180)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w", stretch=col == "detail")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        right = ttk.LabelFrame(body, text="재생 옵션", padding=6)
        right.pack(side="left", fill="y")
        for i, (name, label, kind) in enumerate(PLAY_FIELDS):
            ttk.Label(right, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = self.gui._vars[name]
            if kind == "check":
                w = ttk.Checkbutton(right, variable=var)
            elif kind == "combo":
                w = ttk.Combobox(right, textvariable=var, state="readonly",
                                 values=("absolute", "relative"), width=12)
            else:
                w = ttk.Entry(right, textvariable=var, width=14)
            w.grid(row=i, column=1, sticky="w", padx=6)
        ttk.Button(right, text="옵션 적용", command=lambda: self.gui.apply_options(True)).grid(
            row=len(PLAY_FIELDS), column=0, columnspan=2, pady=8)

    def show(self) -> None:
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()

    def load_macro(self) -> None:
        m = self._macro = self.app.macro
        name = self.app.macro_name or "저장 안 됨"
        self.top.title(f"재생 - {name}")
        self.title_label.configure(text=name)
        self.tree.delete(*self.tree.get_children())
        self._visible_at, self._last_idx = [], None
        if m is None:
            self.info.configure(text="(매크로 없음)")
            return
        win = (m.window or {}).get("title")
        self.info.configure(text=f"이벤트 {len(m.events)}개 · {m.duration:.2f}초 · 좌표계 {m.coord_space}"
                                 + (f" · 녹화 창 '{win}'" if win else ""))
        last = None
        hide = self.hide_moves.get()
        for i, ev in enumerate(m.events):
            if not (hide and ev["type"] == "move"):
                last = str(i)
                self.tree.insert("", "end", iid=last, values=(
                    i + 1, f"{ev['t']:.3f}", EVENT_LABELS[ev["type"]], describe(ev)))
            self._visible_at.append(last)

    def refresh(self) -> None:
        if self.app.macro is not self._macro:
            self.load_macro()
        playing = self.app.playing
        idle = not (self.app.recording or playing or self.gui.counting_down)
        self.btn_play.state(["!disabled"] if idle else ["disabled"])
        self.btn_stop.state(["!disabled"] if playing or self.gui.counting_down else ["disabled"])
        prog = self.app.progress
        total = len(self._macro.events) if self._macro else 0
        if self.gui.counting_down:
            self.state_label.configure(text="시작 대기 중…")
        elif prog is None or not total:
            self.state_label.configure(text="재생 대기")
            self.progress["value"] = 0
            self._last_idx = None
            return
        else:
            loop, idx, repeat = prog
            elapsed = time.monotonic() - (self.gui.play_started or time.monotonic())
            self.state_label.configure(
                text=f"루프 {loop}/{repeat or '∞'} · 이벤트 {idx + 1}/{total} · {elapsed:.1f}초")
            self.progress["value"] = (idx + 1) / total * 100
            if idx != self._last_idx and 0 <= idx < len(self._visible_at):
                self._last_idx = idx
                iid = self._visible_at[idx]
                if iid is not None:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)

    def close(self) -> None:
        self.top.destroy()
        self.gui.playback = None


def run_gui(app: App) -> int:
    root = tk.Tk()
    gui = Gui(root, app)
    hotkeys = HotkeyListener(gui.hotkey_bindings())
    hotkeys.start()
    gui.log("준비 완료. 대상 창 제목을 입력하고 녹화를 시작하세요.")
    try:
        root.mainloop()
    finally:
        hotkeys.stop()
    return 0
