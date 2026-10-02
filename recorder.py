"""입력 녹화. RecorderCore 는 순수 로직(시간은 인자로 받음), Recorder 는 pynput 연결부."""
from __future__ import annotations

import time

import keys
from profiles import Macro

MOVE_INTERVAL = 0.010  # 마우스 이동 최소 샘플링 간격(초)
LEAD_TIME = 0.2        # 첫 실제 입력 전 대기 상한(초)


class RecorderCore:
    def __init__(self, ignore_keys=(), move_interval: float = MOVE_INTERVAL,
                 lead: float = LEAD_TIME, origin: tuple[int, int] = (0, 0), relative: bool = False) -> None:
        """relative: 커서 좌표 대신 Raw Input 이동량(on_raw_move)을 rmove 로 기록하고,
        버튼/스크롤은 좌표 없이(현재 커서 위치) 기록한다."""
        self.ignore_keys = set(ignore_keys)
        self.relative = relative
        self._rel = [0, 0]
        self._rel_t = 0.0
        self._last_rel_t: float | None = None
        self.move_interval = move_interval
        self.lead = lead
        self.origin = origin
        self._raw: list[dict] = []
        self._t0 = 0.0
        self._initial: dict | None = None
        self._pos: tuple[int, int] | None = None
        self._last_move_t: float | None = None
        self._pending: dict | None = None
        self._keys_down: set[str] = set()
        self._buttons_down: set[str] = set()

    def _pt(self, x, y) -> tuple[int, int]:
        return int(round(x)) - self.origin[0], int(round(y)) - self.origin[1]

    def start(self, now: float, cursor: tuple[int, int]) -> None:
        self._t0 = now
        if self.relative:
            return
        x, y = self._pt(*cursor)
        self._initial = {"t": 0.0, "type": "move", "x": x, "y": y}
        self._pos = (x, y)

    def _emit(self, t: float, ev: dict) -> None:
        ev["t"] = t
        self._raw.append(ev)

    def _flush_pending(self) -> None:
        self._flush_rel()
        if self._pending:
            self._emit(self._pending["t"], self._pending)
            self._last_move_t = self._pending["t"]
            self._pending = None

    def _flush_rel(self) -> None:
        if self._rel != [0, 0]:
            self._emit(self._rel_t, {"type": "rmove", "dx": self._rel[0], "dy": self._rel[1]})
            self._last_rel_t = self._rel_t
            self._rel = [0, 0]

    def on_raw_move(self, dx: int, dy: int, now: float) -> None:
        """Raw Input 이동량. move_interval 단위로 합쳐서 기록한다."""
        if not self.relative or (not dx and not dy):
            return
        self._rel[0] += int(dx)
        self._rel[1] += int(dy)
        self._rel_t = now - self._t0
        if self._last_rel_t is None or self._rel_t - self._last_rel_t >= self.move_interval:
            self._flush_rel()

    def on_move(self, x, y, now: float) -> None:
        if self.relative:
            return
        pos = self._pt(x, y)
        if pos == self._pos:
            return
        self._pos = pos
        t = now - self._t0
        ev = {"type": "move", "x": pos[0], "y": pos[1]}
        if self._last_move_t is None or t - self._last_move_t >= self.move_interval:
            self._pending = None
            self._emit(t, ev)
            self._last_move_t = t
        else:
            ev["t"] = t
            self._pending = ev

    def on_click(self, x, y, button: str, pressed: bool, now: float) -> None:
        if pressed:
            self._buttons_down.add(button)
        elif button in self._buttons_down:
            self._buttons_down.discard(button)
        else:
            return  # 녹화 시작 전에 눌려 있던 버튼의 up
        self._flush_pending()
        ev = {"type": "mdown" if pressed else "mup", "button": button}
        if not self.relative:
            px, py = self._pt(x, y)
            self._pos = (px, py)
            ev.update(x=px, y=py)
        self._emit(now - self._t0, ev)

    def on_scroll(self, x, y, dx: int, dy: int, now: float) -> None:
        self._flush_pending()
        ev = {"type": "scroll", "dx": int(dx), "dy": int(dy)}
        if not self.relative:
            px, py = self._pt(x, y)
            self._pos = (px, py)
            ev.update(x=px, y=py)
        self._emit(now - self._t0, ev)

    def on_key(self, name: str | None, pressed: bool, now: float) -> None:
        if name is None or name in self.ignore_keys:
            return
        if pressed:
            if name in self._keys_down:
                return  # auto-repeat
            self._keys_down.add(name)
        else:
            if name not in self._keys_down:
                return
            self._keys_down.discard(name)
        self._flush_pending()
        self._emit(now - self._t0, {"type": "kdown" if pressed else "kup", "key": name})

    def finish(self, now: float) -> list[dict]:
        self._flush_pending()
        t = now - self._t0
        for b in sorted(self._buttons_down):
            ev = {"type": "mup", "button": b}
            if not self.relative:
                ev.update(x=self._pos[0], y=self._pos[1])
            self._emit(t, ev)
        for k in sorted(self._keys_down):
            self._emit(t, {"type": "kup", "key": k})
        self._buttons_down.clear()
        self._keys_down.clear()
        events: list[dict] = [dict(self._initial)] if self._initial else []
        if self._raw:
            offset = max(0.0, self._raw[0]["t"] - self.lead)
            for ev in self._raw:
                ev["t"] = round(max(0.0, ev["t"] - offset), 4)
                events.append(ev)
        return events


class Recorder:
    def __init__(self, backend, window_title: str = "", ignore_keys=(),
                 clock=time.perf_counter, coord_space: str | None = None, relative: bool = False) -> None:
        """coord_space: "window"(창 필수) / "screen" / None(창을 찾으면 window).
        relative: 마우스 이동을 Raw Input 이동량으로 녹화 (3D 시점 회전용)."""
        self.backend = backend
        self.coord_space = coord_space
        self.relative = relative
        self._raw = None
        self.window_title = window_title
        self.ignore_keys = ignore_keys
        self._clock = clock
        self._core: RecorderCore | None = None
        self._listeners: list = []
        self._window: dict | None = None

    @property
    def recording(self) -> bool:
        return self._core is not None

    def start(self) -> str:
        """녹화 시작. 좌표계("window"/"screen")를 반환."""
        origin = (0, 0)
        self._window = None
        if self.relative:
            self.coord_space = "screen"  # 좌표를 쓰지 않는다
        if self.coord_space == "window" and not self.window_title:
            raise ValueError("창 기준 좌표는 대상 창 제목이 필요합니다")
        if self.window_title and self.coord_space != "screen":
            rect = self.backend.find_window_rect(self.window_title)
            if rect is None and self.coord_space == "window":
                raise ValueError(f"대상 창을 찾을 수 없습니다: {self.window_title!r}")
            if rect:
                origin = (rect[0], rect[1])
                self._window = {"title": self.window_title,
                                "width": rect[2] - rect[0], "height": rect[3] - rect[1]}
        from pynput import keyboard, mouse  # Windows 환경에서만 필요

        self._core = core = RecorderCore(self.ignore_keys, origin=origin, relative=self.relative)
        core.start(self._clock(), self.backend.cursor_pos())
        clock = self._clock
        if self.relative:
            from input_backend import RawMouseListener
            self._raw = RawMouseListener(lambda dx, dy: core.on_raw_move(dx, dy, clock()))
            try:
                self._raw.start()
            except Exception:
                self._core, self._raw = None, None
                raise
        self._listeners = [
            mouse.Listener(
                on_move=lambda x, y: core.on_move(x, y, clock()),
                on_click=lambda x, y, b, p: core.on_click(x, y, b.name, p, clock()),
                on_scroll=lambda x, y, dx, dy: core.on_scroll(x, y, dx, dy, clock())),
            keyboard.Listener(
                on_press=lambda k: core.on_key(keys.name_from_pynput(k), True, clock()),
                on_release=lambda k: core.on_key(keys.name_from_pynput(k), False, clock())),
        ]
        for lst in self._listeners:
            lst.start()
        return "relative" if self.relative else ("window" if self._window else "screen")

    def stop(self) -> Macro:
        core, self._core = self._core, None
        if core is None:
            raise RuntimeError("녹화 중이 아닙니다")
        for lst in self._listeners:
            lst.stop()
        self._listeners = []
        if self._raw is not None:
            self._raw.stop()
            self._raw = None
        events = core.finish(self._clock())
        w, h = self.backend.screen_size()
        return Macro(events=events, screen={"width": w, "height": h},
                     coord_space="window" if self._window else "screen",
                     window=self._window)
