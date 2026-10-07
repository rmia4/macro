"""재생기. 백엔드·시계·대기 함수를 주입받아 OS 없이 테스트할 수 있다."""
from __future__ import annotations

import dataclasses
import math
import random
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable

import keys
from profiles import Macro, blocks
from vision import conditions_in, evaluate


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


MIN_WHILE_LAP = 0.01  # 동안 반복 한 바퀴의 최소 시간(초)

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
                 log: Callable[[str], None] | None = None,
                 vision=None) -> None:
        """vision: 화면 조건 판정기 (vision.Vision). 조건 이벤트가 있는 매크로에 필요."""
        self.backend = backend
        self.options = options or PlayOptions()
        self._stop = threading.Event()
        self._clock = clock
        self._waiter = waiter or self._stop.wait
        self._rng = rng or random.Random()
        self._log = log or (lambda msg: None)
        self._held_keys: dict[str, float] = {}
        self._held_buttons: set[str] = set()
        self.vision = vision
        self.last_match = None  # 마지막 조건 판정 결과 (표시용)
        self.variables: dict[str, dict] = {}  # 변수 이름 -> 지정된 조건 (쓰일 때 판정, 재생 회차마다 비움)
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
        conds = conditions_in(macro.events)
        images = [ev for ev in macro.events if ev["type"] == "set_image"]
        if conds or images:  # 입력을 보내기 전에 화면 인식 준비가 되었는지 확인
            if self.vision is None:
                raise ValueError("화면 조건 이벤트가 있지만 화면 인식을 사용할 수 없습니다")
            self.vision.preload(conds)
            for ev in images:
                if "template" in ev:
                    self.vision.template(ev["template"])
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
        bl = blocks(events)
        ends = bl.repeat                                 # 반복 시작 -> 끝
        starts = {end: start for start, end in ends.items()}
        while_starts = {end: start for start, end in bl.while_end.items()}
        self.variables = {}
        if self.vision is not None and hasattr(self.vision, "clear_images"):
            self.vision.clear_images()  # 이미지 변수도 회차마다 비운다
        lap_cum: dict[int, float] = {}                   # 동안 반복 -> 이번 회차 시작 시각(cum)
        first = next((e for e in events if "x" in e), None)
        self._roll_offset()
        if o.mouse_mode == "relative":
            self._last_rel = (first["x"], first["y"]) if first else None
        else:
            self._cursor = self.backend.cursor_pos()
            if not self._approach(first):
                return False
        self._origin = self._clock()
        cum = 0.0
        j = o.time_jitter / 100.0
        gaps = [max(0.0, ev["t"] - (events[i - 1]["t"] if i else 0.0)) for i, ev in enumerate(events)]
        remaining: dict[int, int] = {}  # 반복 끝 인덱스 -> 남은 반복 횟수
        i = 0
        while i < len(events):
            ev = events[i]
            dt = gaps[i] / o.speed
            if j and dt > 0:
                dt *= 1 + self._rng.uniform(-j, j)
            cum += dt
            if not self._wait_until(cum) or not self._wait_focus(macro):
                return False
            typ = ev["type"]
            if typ in ("wait_until", "click_image"):
                result = self._wait_condition(ev, macro)
                if result == "stopped":
                    return False
                if result == "timeout" and ev.get("on_timeout", "stop") == "stop":
                    self._log(f"조건 대기 시간 초과로 재생을 멈춥니다 ({ev.get('timeout', 10):g}초)")
                    self._stop.set()
                    return False
                if result == "timeout":
                    self._log("조건 대기 시간 초과 — 계속 진행합니다")
                elif typ == "click_image" and not self._click_found(ev):
                    return False
                self._origin = self._clock() - cum  # 대기한 만큼 이후 시간표를 뒤로 민다
            elif typ in ("if_start", "break_if", "while_start"):
                self._check(ev["cond"])
            elif typ == "set_var":
                self.variables[ev["name"]] = ev["cond"]  # 지금 판정하지 않고, 변수가 쓰일 때 판정한다
            elif typ == "set_image":
                if "template" in ev:
                    self.vision.set_image(ev["name"], self.vision.template(ev["template"]))
                elif not self.vision.capture_image(ev["name"], ev["capture"], self._win):
                    self._log(f"이미지 변수 '{ev['name']}': 캡처 영역이 화면 밖이라 비워 둡니다")
            elif not self._dispatch(ev):
                return False
            self.event_index = i
            if typ == "repeat_start":
                remaining[ends[i]] = ev["count"] - 1 if ev["count"] > 0 else -1  # -1 = 무한
            elif typ == "repeat_end" and remaining.get(i, 0) != 0:
                if remaining[i] > 0:
                    remaining[i] -= 1
                start = starts[i]
                i = start + 1  # 구간 처음으로 (반복 시작 표시는 다시 실행하지 않음)
                continue
            elif typ == "while_start":
                if not self.last_match.matched:
                    i = bl.while_end[i] + 1  # 조건이 맞지 않으면 동안 반복 끝 다음으로
                    continue
                lap_cum[i] = cum
            elif typ == "while_end":
                start = while_starts[i]
                # 한 바퀴에 지연이 없어도 CPU 를 독점하지 않도록 최소 간격을 둔다
                cum = max(cum, lap_cum.get(start, cum) + MIN_WHILE_LAP)
                i = start  # 다시 조건 판정
                continue
            elif typ == "if_start" and not self.last_match.matched:
                i = (bl.if_else.get(i, bl.if_end[i])) + 1  # 아니면 구간(없으면 분기 끝 다음)으로
                continue
            elif typ == "else":
                i = bl.else_end[i] + 1  # '만약' 구간을 실행하고 왔으면 '아니면' 구간은 건너뛴다
                continue
            elif typ == "break_if" and self.last_match.matched:
                start = bl.parent_loop.get(i)
                if start is None:
                    return True  # 반복 구간 밖이면 이번 회차 종료
                if start in bl.while_end:
                    i = bl.while_end[start] + 1
                    continue
                remaining[ends[start]] = 0
                i = ends[start] + 1
                continue
            i += 1
        return True

    def _check(self, cond: dict):
        """조건 판정 (변수·여러 조건 포함). 결과는 last_match 에도 남긴다."""
        frame = getattr(self.vision, "frame", None)  # 여러 화면 조건이면 캡처를 한 번에
        with frame(cond, self._win, self.variables) if frame else nullcontext():
            self.last_match = evaluate(cond, lambda c: self.vision.check(c, self._win), self.variables)
        return self.last_match

    def _wait_until(self, cum: float) -> bool:
        while True:
            remaining = self._origin + cum - self._clock()
            if remaining <= 0:
                if self._deadline is not None and self._clock() >= self._deadline:
                    self._expire()
                return not self._stop.is_set()
            if self._sleep(remaining):
                return False

    # ---- 화면 조건 ----
    def _wait_condition(self, ev: dict, macro: Macro) -> str:
        """조건이 맞을 때까지 대기. "ok" | "timeout" | "stopped"."""
        timeout = ev.get("timeout", 10)
        interval = ev.get("interval", 0.1)
        start = self._clock()
        while True:
            if not self._wait_focus(macro):
                return "stopped"
            if self._check(ev["cond"]).matched:
                return "ok"
            waited = self._clock() - start
            if timeout > 0 and waited >= timeout:
                return "timeout"
            step = interval if timeout <= 0 else min(interval, timeout - waited)
            if self._sleep(step):
                return "stopped"

    def _click_found(self, ev: dict) -> bool:
        """click_image: 찾은 위치(+보정, +클릭 좌표 편차)를 클릭. 중단되면 False."""
        dx, dy = ev.get("offset", [0, 0])
        x, y = self.last_match.pos
        x, y = int(round(x + dx)) + self._offset[0], int(round(y + dy)) + self._offset[1]
        button = ev.get("button", "left")
        self.backend.mouse_move_abs(x, y)
        self._cursor = (x, y)
        self._held_buttons.add(button)
        self.backend.mouse_down(button)
        if self._sleep(ev.get("hold", 0.06)):
            return False  # 눌린 버튼은 run 의 finally 에서 해제
        self.backend.mouse_up(button)
        self._held_buttons.discard(button)
        self._roll_offset()
        return True

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
        elif typ == "rmove":  # Raw Input 으로 녹화한 상대 이동량
            if ev["dx"] or ev["dy"]:
                b.mouse_move_rel(int(ev["dx"]), int(ev["dy"]))
            self._cursor = None  # 커서 위치를 알 수 없게 되었으므로 다음 절대 이동은 반드시 전송
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
        # "wait", "repeat_start", "repeat_end": 대기는 이미 타임라인에 반영됨, 반복은 _play_once 가 처리
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
