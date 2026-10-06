"""매크로 JSON 입출력과 스키마 검증."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import keys
import vision

VERSION = 1
EVENT_TYPES = {"move", "rmove", "mdown", "mup", "scroll", "kdown", "kup", "wait",  # rmove: 상대 이동, wait: 지연만
               "repeat_start", "repeat_end",  # 반복 구간: 사이의 이벤트를 count 번 반복 (0 = 무한, 중첩 가능)
               "wait_until",                  # 화면 조건이 맞을 때까지 대기
               "if_start", "else", "if_end",  # 조건 분기
               "break_if",                    # 조건이 맞으면 가장 안쪽 반복 구간 종료
               "click_image",                 # 이미지를 찾아 그 위치를 클릭
               "set_var",                     # 조건 판정 결과(참/거짓)를 변수에 저장
               "while_start", "while_end"}    # 조건이 맞는 동안 반복
COND_EVENTS = {"wait_until", "if_start", "break_if", "click_image", "set_var", "while_start"}
ON_TIMEOUT = ("stop", "continue")
MAX_REPEAT = 100000
BUTTONS = {"left", "right", "middle", "x1", "x2"}
COORD_SPACES = {"screen", "window"}
_NAME_RE = re.compile(r"^[\w\-. ]+$")


class MacroFormatError(ValueError):
    pass


@dataclass
class Macro:
    events: list[dict] = field(default_factory=list)
    screen: dict = field(default_factory=lambda: {"width": 0, "height": 0})
    coord_space: str = "screen"
    window: dict | None = None  # {"title", "width", "height"}
    version: int = VERSION
    hotkey: str | None = None   # 이 매크로를 시작/중지하는 전역 핫키 ('f6', 'ctrl+f1' 등)
    options: dict = field(default_factory=dict)  # 재생 옵션 (player.options_to_dict 형식)

    @property
    def duration(self) -> float:
        return self.events[-1]["t"] if self.events else 0.0

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "screen": self.screen,
            "coord_space": self.coord_space,
            "window": self.window,
            "hotkey": self.hotkey,
            "options": self.options,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Macro":
        if not isinstance(data, dict):
            raise MacroFormatError("최상위 값이 객체가 아닙니다")
        version = data.get("version")
        if not isinstance(version, int) or version < 1 or version > VERSION:
            raise MacroFormatError(f"지원하지 않는 version: {version!r}")
        coord_space = data.get("coord_space", "screen")
        if coord_space not in COORD_SPACES:
            raise MacroFormatError(f"잘못된 coord_space: {coord_space!r}")
        events = data.get("events")
        if not isinstance(events, list):
            raise MacroFormatError("events 배열이 없습니다")
        _validate_events(events)
        screen = data.get("screen") or {"width": 0, "height": 0}
        window = data.get("window")
        if window is not None and not isinstance(window, dict):
            raise MacroFormatError("window 는 객체여야 합니다")
        try:
            hotkey = keys.parse_hotkey(data.get("hotkey"))
        except ValueError as e:
            raise MacroFormatError(f"hotkey: {e}") from None
        options = data.get("options") or {}
        if not isinstance(options, dict):
            raise MacroFormatError("options 는 객체여야 합니다")
        return cls(events=events, screen=screen, coord_space=coord_space, window=window,
                   version=version, hotkey=hotkey, options=options)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass
class Blocks:
    """반복 구간/조건 분기/동안 반복 구조 (모두 인덱스)."""
    repeat: dict[int, int] = field(default_factory=dict)      # 반복 시작 -> 반복 끝
    while_end: dict[int, int] = field(default_factory=dict)   # 동안 반복 -> 동안 반복 끝
    if_end: dict[int, int] = field(default_factory=dict)      # 만약 -> 분기 끝
    if_else: dict[int, int] = field(default_factory=dict)     # 만약 -> 아니면 (있을 때만)
    else_end: dict[int, int] = field(default_factory=dict)    # 아니면 -> 분기 끝
    parent_loop: dict[int, int] = field(default_factory=dict)  # 인덱스 -> 가장 안쪽 반복(반복 구간/동안 반복) 시작
    loops: dict[int, tuple[int, ...]] = field(default_factory=dict)  # 인덱스 -> 감싸는 반복 시작들 (바깥 -> 안)


_OPEN = {"repeat_start": "반복 시작", "if_start": "만약", "while_start": "동안 반복"}
_CLOSE = {"repeat_end": ("repeat_start", "반복 끝"), "if_end": ("if_start", "분기 끝"),
          "while_end": ("while_start", "동안 반복 끝")}
_LOOP_OPEN = ("repeat_start", "while_start")


def blocks(events: list) -> Blocks:
    """반복/분기 구조를 분석한다. 짝이 맞지 않거나 엇갈리면 ValueError.
    반복 시작/끝 표시 자신은 그 반복의 바깥에 속한다."""
    b = Blocks()
    stack: list[int] = []   # 열린 블록의 시작 인덱스
    repeats: list[int] = []  # 열린 반복(반복 구간/동안 반복)
    for i, ev in enumerate(events):
        typ = ev.get("type") if isinstance(ev, dict) else None
        if typ in ("repeat_end", "while_end") and repeats:
            outer = repeats[:-1]  # 끝 표시는 자기 반복의 바깥
        else:
            outer = repeats
        b.loops[i] = tuple(outer)
        if outer:
            b.parent_loop[i] = outer[-1]
        if typ in _OPEN:
            stack.append(i)
            if typ in _LOOP_OPEN:
                repeats.append(i)
        elif typ == "else":
            if not stack or events[stack[-1]]["type"] != "if_start":
                raise ValueError(f"{i + 1}번째 '아니면'이 '만약' 구간 안에 있지 않습니다")
            if stack[-1] in b.if_else:
                raise ValueError(f"{i + 1}번째 '아니면'이 중복되었습니다")
            b.if_else[stack[-1]] = i
        elif typ in _CLOSE:
            want, label = _CLOSE[typ]
            if not stack or events[stack[-1]]["type"] != want:
                raise ValueError(f"{i + 1}번째 '{label}'에 짝이 되는 '{_OPEN[want]}'이 없습니다")
            start = stack.pop()
            if typ == "repeat_end":
                b.repeat[start] = i
                repeats.pop()
            elif typ == "while_end":
                b.while_end[start] = i
                repeats.pop()
            else:
                b.if_end[start] = i
                if start in b.if_else:
                    b.else_end[b.if_else[start]] = i
    if stack:
        i = stack[-1]
        raise ValueError(f"{i + 1}번째 '{_OPEN[events[i]['type']]}'에 짝이 되는 끝이 없습니다")
    return b


def block_pairs(events: list) -> dict[int, int]:
    """반복 구간·동안 반복의 시작 인덱스 -> 끝 인덱스. 구조가 잘못되면 ValueError."""
    b = blocks(events)
    return {**b.repeat, **b.while_end}


def var_scopes(events: list, b: Blocks | None = None) -> dict[str, int | None]:
    """변수 이름 -> 초기화 기준 반복의 시작 인덱스 (None = 재생 회차마다).

    변수를 저장하는 곳과 조건에서 읽는 곳을 모두 감싸는 반복 중 가장 안쪽 것의 회차마다 초기화한다.
    """
    b = b or blocks(events)
    chains: dict[str, tuple[int, ...]] = {}
    for i, ev in enumerate(events):
        names = set(vision.variables_used([ev]))
        if ev.get("type") == "set_var" and isinstance(ev.get("name"), str):
            names.add(ev["name"])
        for name in names:
            chain = b.loops.get(i, ())
            if name not in chains:
                chains[name] = chain
            else:
                old, n = chains[name], 0
                while n < min(len(old), len(chain)) and old[n] == chain[n]:
                    n += 1
                chains[name] = old[:n]
    return {name: (chain[-1] if chain else None) for name, chain in chains.items()}


def _validate_events(events: list) -> None:
    prev = 0.0
    for i, ev in enumerate(events):
        where = f"events[{i}]"
        if not isinstance(ev, dict):
            raise MacroFormatError(f"{where}: 객체가 아닙니다")
        typ = ev.get("type")
        if typ not in EVENT_TYPES:
            raise MacroFormatError(f"{where}: 알 수 없는 type {typ!r}")
        t = ev.get("t")
        if not _is_num(t) or t < 0:
            raise MacroFormatError(f"{where}: t 가 잘못되었습니다")
        if t < prev:
            raise MacroFormatError(f"{where}: t 가 감소합니다")
        prev = t
        has_pos = "x" in ev or "y" in ev
        if typ == "move" or (typ in ("mdown", "mup", "scroll") and has_pos):
            # 버튼/스크롤은 좌표가 없으면 현재 커서 위치에서 입력한다
            if not _is_num(ev.get("x")) or not _is_num(ev.get("y")):
                raise MacroFormatError(f"{where}: x, y 가 필요합니다")
        if typ in ("mdown", "mup") and ev.get("button") not in BUTTONS:
            raise MacroFormatError(f"{where}: 잘못된 button {ev.get('button')!r}")
        if typ in ("scroll", "rmove") and not (_is_num(ev.get("dx")) and _is_num(ev.get("dy"))):
            raise MacroFormatError(f"{where}: dx, dy 가 필요합니다")
        if typ in ("kdown", "kup") and not keys.is_known(ev.get("key", "")):
            raise MacroFormatError(f"{where}: 알 수 없는 key {ev.get('key')!r}")
        if typ in COND_EVENTS:
            try:
                vision.validate_condition(ev.get("cond"))
            except ValueError as e:
                raise MacroFormatError(f"{where}: {e}") from None
        if typ == "click_image":
            if ev["cond"].get("kind") != "image" or ev["cond"].get("negate"):
                raise MacroFormatError(f"{where}: 이미지 클릭은 '보이는' 이미지 조건이어야 합니다")
            if ev.get("button", "left") not in BUTTONS:
                raise MacroFormatError(f"{where}: 잘못된 button {ev.get('button')!r}")
            off = ev.get("offset", [0, 0])
            if not (isinstance(off, list) and len(off) == 2 and all(_is_num(v) for v in off)):
                raise MacroFormatError(f"{where}: offset 은 [x, y] 여야 합니다")
            if not _is_num(ev.get("hold", 0.06)) or ev.get("hold", 0.06) < 0:
                raise MacroFormatError(f"{where}: hold 는 0 이상이어야 합니다")
        if typ in ("wait_until", "click_image"):
            if not _is_num(ev.get("timeout", 10)) or ev.get("timeout", 10) < 0:
                raise MacroFormatError(f"{where}: timeout 은 0(무제한) 이상의 초여야 합니다")
            if ev.get("on_timeout", "stop") not in ON_TIMEOUT:
                raise MacroFormatError(f"{where}: on_timeout 은 stop 또는 continue 입니다")
            if not _is_num(ev.get("interval", 0.1)) or ev.get("interval", 0.1) < 0.01:
                raise MacroFormatError(f"{where}: interval 은 0.01초 이상이어야 합니다")
        if typ == "set_var":
            try:
                vision.validate_var_name(ev.get("name"))
            except ValueError as e:
                raise MacroFormatError(f"{where}: {e}") from None
        if typ == "repeat_start":
            count = ev.get("count")
            if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_REPEAT:
                raise MacroFormatError(f"{where}: 반복 횟수는 0(무한)~{MAX_REPEAT} 정수여야 합니다")
    try:
        blocks(events)
    except ValueError as e:
        raise MacroFormatError(str(e)) from None


def save_macro(macro: Macro, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(macro.to_dict(), f, ensure_ascii=False, indent=1)
    return path


def load_macro(path: str | Path) -> Macro:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise MacroFormatError(f"JSON 파싱 실패: {e}") from e
    return Macro.from_dict(data)


def macro_path(directory: str | Path, name: str) -> Path:
    """이름 -> 파일 경로. 경로 구분자·상위 디렉터리 접근을 막는다."""
    name = name.strip()
    if name.endswith(".json"):
        name = name[:-5]
    if not name or not _NAME_RE.match(name) or name.strip(".") == "":
        raise ValueError(f"잘못된 매크로 이름: {name!r}")
    return Path(directory) / f"{name}.json"


def list_macros(directory: str | Path) -> list[str]:
    d = Path(directory)
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


def delete_macro(directory: str | Path, name: str) -> Path:
    path = macro_path(directory, name)
    if not path.is_file():
        raise ValueError(f"매크로가 없습니다: {name}")
    path.unlink()
    return path
