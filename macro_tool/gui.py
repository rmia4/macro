"""tkinter GUI. 로직은 main.App 에 위임하고 화면 표시/입력만 담당한다."""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from hotkeys import HOTKEY_PLAY, HOTKEY_QUIT, HOTKEY_RECORD, HotkeyListener
from main import App
from player import set_option
from profiles import MacroFormatError, list_macros

# (옵션 이름, 라벨, 종류)  종류: entry | check | combo
OPTION_FIELDS = [
    ("window_title", "대상 창 제목 (포커스 제한)", "entry"),
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
COLORS = {"idle": "#555555", "rec": "#c62828", "play": "#2e7d32", "wait": "#ef6c00"}


class Gui:
    def __init__(self, root: tk.Tk, app: App) -> None:
        self.root, self.app = root, app
        self._logs: queue.Queue[str] = queue.Queue()
        self._calls: queue.Queue = queue.Queue()  # 다른 스레드(핫키) -> Tk 스레드
        self._countdown_id = None
        self._vars: dict[str, tk.Variable] = {}
        app.log = self._logs.put
        root.title("매크로 도구")
        root.minsize(760, 560)
        self._build()
        self._load_options_into_form()
        self.refresh_list()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = None
        self._poll()

    # ---- 화면 구성 ----
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.status = tk.Label(top, text="대기 중", fg="white", bg=COLORS["idle"],
                               font=("", 13, "bold"), width=18, pady=6)
        self.status.pack(side="left")
        self.btn_rec = ttk.Button(top, text=f"● 녹화 ({HOTKEY_RECORD.upper()})", command=self.on_record)
        self.btn_play = ttk.Button(top, text=f"▶ 재생 ({HOTKEY_PLAY.upper()})", command=self.on_play)
        self.btn_stop = ttk.Button(top, text="■ 중지", command=self.on_stop)
        for b in (self.btn_rec, self.btn_play, self.btn_stop):
            b.pack(side="left", padx=4)
        ttk.Label(top, text="시작 지연(초)").pack(side="left", padx=(16, 2))
        self.delay = tk.StringVar(value="3")
        ttk.Spinbox(top, from_=0, to=30, width=4, textvariable=self.delay).pack(side="left")

        ttk.Label(self.root, foreground="#666", padding=(8, 0), wraplength=720,
                  text=f"버튼으로 시작하면 지연 후 시작되므로 그 사이 게임 창으로 전환하세요. "
                       f"녹화 종료는 {HOTKEY_RECORD.upper()} 키 권장 (버튼 클릭이 기록됩니다). "
                       f"{HOTKEY_QUIT.upper()} = 프로그램 종료.").pack(fill="x")

        mid = ttk.Frame(self.root, padding=8)
        mid.pack(fill="both", expand=True)

        left = ttk.LabelFrame(mid, text="매크로", padding=6)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.listbox = tk.Listbox(left, height=8, exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda e: self.on_load())
        row = ttk.Frame(left)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="불러오기", command=self.on_load).pack(side="left")
        ttk.Button(row, text="새로고침", command=self.refresh_list).pack(side="left", padx=4)
        row = ttk.Frame(left)
        row.pack(fill="x")
        self.name = tk.StringVar()
        ttk.Entry(row, textvariable=self.name).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="저장", command=self.on_save).pack(side="left", padx=4)
        self.info = ttk.Label(left, text="매크로: (없음)", foreground="#333")
        self.info.pack(anchor="w", pady=(6, 0))

        right = ttk.LabelFrame(mid, text="재생 옵션", padding=6)
        right.pack(side="left", fill="both", expand=True)
        for i, (name, label, kind) in enumerate(OPTION_FIELDS):
            ttk.Label(right, text=label).grid(row=i, column=0, sticky="w", pady=2)
            if kind == "check":
                var = tk.BooleanVar()
                w = ttk.Checkbutton(right, variable=var)
            elif kind == "combo":
                var = tk.StringVar()
                w = ttk.Combobox(right, textvariable=var, state="readonly",
                                 values=("absolute", "relative"), width=18)
            else:
                var = tk.StringVar()
                w = ttk.Entry(right, textvariable=var, width=22)
            self._vars[name] = var
            w.grid(row=i, column=1, sticky="w", padx=6)
        ttk.Button(right, text="옵션 적용", command=lambda: self.apply_options(True)).grid(
            row=len(OPTION_FIELDS), column=0, columnspan=2, pady=8)

        self.log_box = scrolledtext.ScrolledText(self.root, height=8, state="disabled")
        self.log_box.pack(fill="both", padx=8, pady=(0, 8))

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

    def on_load(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("불러오기", "목록에서 매크로를 선택하세요")
            return
        name = self.listbox.get(sel[0])
        try:
            self.app.load(name)
            self.name.set(name)
        except (ValueError, OSError, MacroFormatError) as e:
            messagebox.showerror("불러오기 실패", str(e))

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
        lines = []
        while not self._logs.empty():
            lines.append(self._logs.get_nowait())
        if lines:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", "\n".join(lines) + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self._refresh_status()
        self._after_id = self.root.after(50, self._poll)

    def _refresh_status(self) -> None:
        if self._countdown_id is not None:
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
        self.btn_play.state(["disabled"] if self.app.recording else ["!disabled"])
        self.btn_stop.state(["!disabled"] if busy or self._countdown_id else ["disabled"])
        m = self.app.macro
        self.info.configure(text=f"매크로: 이벤트 {len(m.events)}개, {m.duration:.2f}초, "
                                 f"좌표계 {m.coord_space}" if m else "매크로: (없음)")

    def close(self) -> None:
        try:
            self.root.after_cancel(self._after_id)
        except Exception:
            pass
        self.app.shutdown()
        self.root.destroy()


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
