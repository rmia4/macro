"""매크로 JSON 입출력과 스키마 검증."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import keys

VERSION = 1
EVENT_TYPES = {"move", "rmove", "mdown", "mup", "scroll", "kdown", "kup", "wait"}  # rmove: 상대 이동, wait: 지연만
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
