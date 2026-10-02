"""재생기. 백엔드·시계·대기 함수를 주입받아 OS 없이 테스트할 수 있다."""
from __future__ import annotations

import dataclasses
import math
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

import keys
from profiles import Macro


@dataclass
class PlayOptions:
    repeat: int = 1               # 0 = 무한
    speed: float = 1.0
    loop_delay: float = 0.0       # 반복 사이 대기(초)
    time_jitter: float = 0.0      # 대기 시간 ±%
    pos_jitter: float = 0.0       # 클릭 좌표 ±px
    approach: bool = True         # 루프 시작 시 첫 위치로 보간 이동
    approach_duration: float = 0.4
    min_key_hold: float = 0.04    # 키 최소 유지 시간(초)
    window_title: str = ""        # 비우면 포커스 제한 없음
    mouse_mode: str = "absolute"  # absolute | relative
    scale_coords: bool = False    # 창 크기가 녹화 때와 다르면 좌표 비례 조정
    max_minutes: float = 0.0      # 최대 실행 시간(분). 0 = 제한 없음
    focus_poll: float = 0.1


_MIN = {"repeat": 0, "speed": 0.01, "loop_delay": 0, "time_jitter": 0, "pos_jitter": 0,
        "approach_duration": 0, "min_key_hold": 0, "focus_poll": 0.01, "max_minutes": 0}
_MAX = {"time_jitter": 100}
_TRUE, _FALSE = {"on", "true", "1", "yes"}, {"off", "false", "0", "no"}


def set_option(opts: PlayOptions, name: str, value: str) -> None:
    """`set <옵션> <값>` 처리. 잘못된 이름/값은 ValueError."""
    types = {f.name: f.type for f in dataclasses.fields(opts)}
    if name not in types:
        raise ValueError(f"알 수 없는 옵션: {name} (가능: {', '.join(types)})")
    typ = types[name]
    if typ == "bool":
        v = value.lower()
        if v not in _TRUE | _FALSE:
            raise ValueError("on/off 로 입력하세요")
        parsed = v in _TRUE
    elif typ == "str":
        parsed = "" if value in ("-", "none") else value
        if name == "mouse_mode" and parsed not in ("absolute", "relative"):
            raise ValueError("absolute 또는 relative")
    else:
        parsed = int(value) if typ == "int" else float(value)
        if name in _MIN and parsed < _MIN[name]:
            raise ValueError(f"{name} 은(는) {_MIN[name]} 이상이어야 합니다")
        if name in _MAX and parsed > _MAX[name]:
            raise ValueError(f"{name} 은(는) {_MAX[name]} 이하여야 합니다")
    setattr(opts, name, parsed)


OPTION_KEYS = [f.name for f in dataclasses.fields(PlayOptions) if f.name != "focus_poll"]


def options_to_dict(opts: PlayOptions) -> dict:
    return {k: getattr(opts, k) for k in OPTION_KEYS}


def options_from_dict(data: dict | None) -> PlayOptions:
    """매크로 파일의 options -> PlayOptions. 값은 set_option 으로 검증 (ValueError)."""
    opts = PlayOptions()
    for k, v in (data or {}).items():
        if k not in OPTION_KEYS:
            continue
        if isinstance(v, bool):
            v = "on" if v else "off"
        elif v is None or v == "":
            v = "none"
        set_option(opts, k, str(v))
    return opts


class Player:
    def __init__(self, backend, options: PlayOptions | None = None, *,
                 clock: Callable[[], float] = time.perf_counter,
                 waiter: Callable[[float], bool] | None = None,
                 rng: random.Random | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.backend = backend
        self.options = options or PlayOptions()
        self._stop = threading.Event()
        self._clock = clock
        self._waiter = waiter or self._stop.wait
        self._rng = rng or random.Random()
        self._log = log or (lambda msg: None)
        self._held_keys: dict[str, float] = {}
        self._held_buttons: set[str] = set()
        self._offset = (0, 0)
        self._origin = 0.0
        self._win = (0, 0)
        self._scale = (1.0, 1.0)
        self._cursor: tuple[int, int] | None = None
        self._last_rel: tuple[int, int] | None = None
        self._deadline: float | None = None
        self.loop_index = 0     # 진행 중인 루프 (1부터), GUI 표시용
        self.event_index = -1   # 마지막으로 전송한 이벤트 인덱스

    def stop(self) -> None:
        self._stop.set()

    def _sleep(self, seconds: float) -> bool:
        """seconds 동안 대기. 중단 요청이 있거나 최대 실행 시간에 도달하면 True."""
        if self._deadline is not None:
            left = self._deadline - self._clock()
            if seconds >= left:  # 대기 도중 시간 초과: 남은 만큼만 기다리고 종료
                if left > 0 and self._waiter(left):
                    return True
                self._expire()
                return True
        if seconds > 0 and self._waiter(seconds):
            return True
        return self._stop.is_set()

    def _expire(self) -> None:
        if not self._stop.is_set():
            self._log(f"최대 실행 시간({self.opt.max_minutes:g}분)이 지나 재생을 멈춥니다")
        self._stop.set()

    # ---- 실행 ----
    def run(self, macro: Macro) -> None:
        for ev in macro.events:
            if ev["type"] in ("kdown", "kup") and not keys.is_known(ev["key"]):
                raise ValueError(f"알 수 없는 키: {ev['key']}")
        self.opt = dataclasses.replace(self.options)
        self._stop.clear()
        self._deadline = self._clock() + self.opt.max_minutes * 60 if self.opt.max_minutes > 0 else None
        n = 0
        try:
            while not self._stop.is_set() and (self.opt.repeat == 0 or n < self.opt.repeat):
                if n and self._sleep(self.opt.loop_delay):
                    break
                self.loop_index, self.event_index = n + 1, -1
                if not self._play_once(macro):
                    break
                n += 1
                self._log(f"루프 {n} 완료")
        finally:
            self._release_all()

    def _play_once(self, macro: Macro) -> bool:
        o = self.opt
        if not self._locate_window(macro):
            return False
        events = macro.events
        first = next((e for e in events if "x" in e), None)
        self._roll_offset()
        if o.mouse_mode == "relative":
            self._last_rel = (first["x"], first["y"]) if first else None
        else:
            self._cursor = self.backend.cursor_pos()
            if not self._approach(first):
                return False
        self._origin = self._clock()
        cum = prev = 0.0
        j = o.time_jitter / 100.0
        for i, ev in enumerate(events):
            dt = max(0.0, ev["t"] - prev) / o.speed
            prev = ev["t"]
            if j and dt > 0:
                dt *= 1 + self._rng.uniform(-j, j)
            cum += dt
            if not self._wait_until(cum) or not self._wait_focus(macro):
                return False
            if not self._dispatch(ev):
                return False
            self.event_index = i
        return True

    def _wait_until(self, cum: float) -> bool:
        while True:
            remaining = self._origin + cum - self._clock()
            if remaining <= 0:
                if self._deadline is not None and self._clock() >= self._deadline:
                    self._expire()
                return not self._stop.is_set()
            if self._sleep(remaining):
                return False

    # ---- 창 / 포커스 ----
    def _locate_window(self, macro: Macro) -> bool:
        self._win, self._scale = (0, 0), (1.0, 1.0)
        if macro.coord_space != "window":
            return True
        title = self.opt.window_title or (macro.window or {}).get("title", "")
        rect = self.backend.find_window_rect(title) if title else None
        if rect is None:
            self._log(f"창을 찾을 수 없어 중단합니다: {title!r}")
            return False
        self._win = (rect[0], rect[1])
        rec = macro.window or {}
        if self.opt.scale_coords and rec.get("width") and rec.get("height"):
            self._scale = ((rect[2] - rect[0]) / rec["width"], (rect[3] - rect[1]) / rec["height"])
        return True

    def _focused(self) -> bool:
        return self.opt.window_title.lower() in self.backend.foreground_title().lower()

    def _wait_focus(self, macro: Macro) -> bool:
        if not self.opt.window_title or self._focused():
            return True
        self._log("대상 창이 포커스를 잃어 일시정지합니다")
        self._release_all()
        start = self._clock()
        while not self._focused():
            if self._sleep(self.opt.focus_poll):
                return False
        self._origin += self._clock() - start
        self._cursor = None
        return self._locate_window(macro)

    # ---- 좌표 ----
    def _roll_offset(self) -> None:
        j = self.opt.pos_jitter
        if j > 0 and self.opt.mouse_mode == "absolute":
            self._offset = (round(self._rng.uniform(-j, j)), round(self._rng.uniform(-j, j)))
        else:
            self._offset = (0, 0)

    def _map(self, x, y) -> tuple[int, int]:
        return (round(x * self._scale[0]) + self._win[0] + self._offset[0],
                round(y * self._scale[1]) + self._win[1] + self._offset[1])

    def _goto(self, ev: dict) -> None:
        if self.opt.mouse_mode == "relative":
            if self._last_rel is None:
                self._last_rel = (ev["x"], ev["y"])
                return
            dx, dy = ev["x"] - self._last_rel[0], ev["y"] - self._last_rel[1]
            if dx or dy:
                self.backend.mouse_move_rel(dx, dy)
            self._last_rel = (ev["x"], ev["y"])
            return
        p = self._map(ev["x"], ev["y"])
        if p != self._cursor:
            self.backend.mouse_move_abs(*p)
            self._cursor = p

    def _goto_if_pos(self, ev: dict) -> None:
        """좌표가 없는 버튼/스크롤 이벤트는 현재 커서 위치에서 입력한다."""
        if "x" in ev:
            self._goto(ev)

    def _approach(self, first: dict | None) -> bool:
        o = self.opt
        if not o.approach or o.approach_duration <= 0 or first is None:
            return True
        tx, ty = self._map(first["x"], first["y"])
        sx, sy = self._cursor
        dist = math.hypot(tx - sx, ty - sy)
        if dist < 3:
            return True
        dur = min(o.approach_duration, max(0.1, dist / 1500))
        steps = max(2, int(dur / 0.008))
        for i in range(1, steps + 1):
            s = i / steps
            e = s * s * (3 - 2 * s)  # smoothstep 가감속
            x, y = round(sx + (tx - sx) * e), round(sy + (ty - sy) * e)
            self.backend.mouse_move_abs(x, y)
            self._cursor = (x, y)
            if self._sleep(dur / steps):
                return False
        return True

    # ---- 이벤트 전송 ----
    def _dispatch(self, ev: dict) -> bool:
        typ, b = ev["type"], self.backend
        if typ == "move":
            self._goto(ev)
        elif typ == "mdown":
            self._goto_if_pos(ev)
            self._held_buttons.add(ev["button"])  # 전송 전에 등록: 예외 시에도 해제 대상
            b.mouse_down(ev["button"])
        elif typ == "mup":
            self._goto_if_pos(ev)
            b.mouse_up(ev["button"])
            self._held_buttons.discard(ev["button"])
            if not self._held_buttons:
                self._roll_offset()
        elif typ == "scroll":
            self._goto_if_pos(ev)
            b.scroll(ev["dx"], ev["dy"])
        elif typ == "kdown":
            self._held_keys[ev["key"]] = self._clock()
            b.key_down(ev["key"])
        elif typ == "kup":
            pressed_at = self._held_keys.get(ev["key"])
            if pressed_at is not None:
                if self._sleep(self.opt.min_key_hold - (self._clock() - pressed_at)):
                    return False
            b.key_up(ev["key"])
            self._held_keys.pop(ev["key"], None)
        # "wait": 대기는 이미 타임라인에 반영됨
        return True

    def _release_all(self) -> None:
        for key in list(self._held_keys):
            try:
                self.backend.key_up(key)
            except Exception as e:
                self._log(f"키 해제 실패 {key}: {e}")
        for button in list(self._held_buttons):
            try:
                self.backend.mouse_up(button)
            except Exception as e:
                self._log(f"버튼 해제 실패 {button}: {e}")
        self._held_keys.clear()
        self._held_buttons.clear()
