"""편집 화면의 순수 로직: 이벤트 <-> 편집 항목 변환, 이벤트 생성, 저장 전 검증.

편집 항목(item)은 이벤트에서 절대 시각 "t" 대신 "dt"(직전 이벤트로부터의 지연, 초)를 가진다.
연속된 마우스 이동은 하나의 "path" 항목으로 묶는다: {"type": "path", "dt", "points": [[dt, x, y], ...]}
(points[0] 의 dt 는 0, 항목의 dt 가 첫 점 앞의 지연). 이벤트로 바꿀 때 다시 move 들로 풀린다.
"""
from __future__ import annotations

import re

import keys
from hotkeys import CONTROL_KEYS
from profiles import BUTTONS, macro_path

EVENT_LABELS = {"move": "마우스 이동", "mdown": "마우스 누름", "mup": "마우스 뗌",
                "scroll": "스크롤", "kdown": "키 누름", "kup": "키 뗌", "wait": "지연",
                "path": "마우스 이동 경로"}

# 추가 가능한 종류 (tap/click 은 누름+뗌 두 개의 이벤트를 만든다)
ADD_KINDS = [("tap", "키 입력 (누르고 떼기)"), ("kdown", "키 누름"), ("kup", "키 뗌"),
             ("click", "마우스 클릭"), ("mdown", "마우스 누름"), ("mup", "마우스 뗌"),
             ("move", "마우스 이동"), ("scroll", "스크롤"), ("wait", "지연")]
EDIT_KINDS = [k for k in ADD_KINDS if k[0] not in ("tap", "click")]
PATH_KINDS = [("path", "마우스 이동 경로")]  # 경로 항목 수정 전용

# 종류별 입력 필드 (cursor: 좌표 대신 현재 커서 위치에서 입력 가능)
KIND_FIELDS = {
    "tap": {"key", "hold"}, "kdown": {"key"}, "kup": {"key"},
    "click": {"button", "pos", "cursor", "hold"}, "mdown": {"button", "pos", "cursor"},
    "mup": {"button", "pos", "cursor"}, "move": {"pos"}, "scroll": {"pos", "cursor", "scroll"},
    "wait": set(), "path": {"pos", "duration"},
}
DEFAULT_HOLD_MS = {"tap": 50, "click": 60}
POSITIONAL = {"move", "mdown", "mup", "scroll", "path"}


def _key_order(k: str):
    if re.fullmatch(r"f\d+", k):
        return (0, int(k[1:]), k)
    return (1 if len(k) == 1 else 2, 0, k)


KEY_CHOICES = sorted(keys.NAME_TO_SCAN, key=_key_order)
HOTKEY_CHOICES = [k for k in KEY_CHOICES if k not in CONTROL_KEYS]


def to_items(events: list[dict]) -> list[dict]:
    items: list[dict] = []
    prev = 0.0
    for ev in events:
        dt = round(max(0.0, ev["t"] - prev), 4)
        prev = ev["t"]
        last = items[-1] if items else None
        if ev["type"] == "move" and last is not None and last["type"] in ("move", "path"):
            if last["type"] == "move":  # 두 번째 연속 이동부터 경로로 묶는다
                last = items[-1] = {"type": "path", "dt": last["dt"],
                                    "points": [[0.0, last["x"], last["y"]]]}
            last["points"].append([dt, ev["x"], ev["y"]])
            continue
        item = {k: v for k, v in ev.items() if k != "t"}
        item["dt"] = dt
        items.append(item)
    return items


def to_events(items: list[dict]) -> list[dict]:
    events, t = [], 0.0
    for item in items:
        if item["type"] == "path":
            for i, (pdt, x, y) in enumerate(item["points"]):
                t = round(t + max(0.0, item.get("dt", 0.0) if i == 0 else pdt), 4)
                events.append({"t": t, "type": "move", "x": x, "y": y})
            continue
        t = round(t + max(0.0, item.get("dt", 0.0)), 4)
        ev = {"t": t}
        ev.update({k: v for k, v in item.items() if k != "dt"})
        events.append(ev)
    return events


def path_duration(item: dict) -> float:
    return round(sum(max(0.0, p[0]) for p in item["points"][1:]), 4)


def total_duration(items: list[dict]) -> float:
    return round(sum(max(0.0, i.get("dt", 0.0)) + (path_duration(i) if i["type"] == "path" else 0)
                     for i in items), 4)


def event_count(items: list[dict]) -> int:
    return sum(len(i["points"]) if i["type"] == "path" else 1 for i in items)


def describe(item: dict) -> str:
    typ = item["type"]
    if typ in ("kdown", "kup"):
        return item["key"]
    if typ == "wait":
        return ""
    if typ == "path":
        pts = item["points"]
        return (f"({pts[0][1]}, {pts[0][2]}) → ({pts[-1][1]}, {pts[-1][2]}) · "
                f"{len(pts)}개 지점 · {round(path_duration(item) * 1000)}ms")
    pos = f"({item['x']}, {item['y']})" if "x" in item else "(현재 커서 위치)"
    if typ in ("mdown", "mup"):
        return f"{item['button']} {pos}"
    if typ == "scroll":
        return f"dx={item['dx']} dy={item['dy']} {pos}"
    return pos


def _int(value, label: str, minimum: int | None = None) -> int:
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{label}: 정수를 입력하세요") from None
    if minimum is not None and n < minimum:
        raise ValueError(f"{label}: {minimum} 이상이어야 합니다")
    return n


def build_items(kind: str, *, delay_ms="0", key: str = "", button: str = "left",
                x="0", y="0", dx="0", dy="0", hold_ms=None, at_cursor: bool = False,
                duration_ms=None, points: list | None = None) -> list[dict]:
    """입력값으로 편집 항목을 만든다. 잘못된 값은 ValueError.

    at_cursor: 버튼/스크롤을 좌표 없이 현재 커서 위치에서 입력.
    kind == "path": points(원래 경로)를 끝 위치 (x, y)·이동 시간 duration_ms 에 맞게 보정한다.
    """
    if kind not in KIND_FIELDS:
        raise ValueError(f"알 수 없는 종류: {kind}")
    fields = KIND_FIELDS[kind]
    dt = _int(delay_ms, "지연(ms)", 0) / 1000
    if kind == "path":
        if not points:
            raise ValueError("경로 정보가 없습니다")
        return [{"type": "path", "dt": dt,
                 "points": reshape_path(points, _int(x, "X"), _int(y, "Y"),
                                        _int(duration_ms, "이동 시간(ms)", 0) / 1000)}]
    base: dict = {}
    if "key" in fields:
        key = key.strip().lower()
        if not keys.is_known(key):
            raise ValueError(f"알 수 없는 키: {key!r}")
        base["key"] = key
    if "pos" in fields and not ("cursor" in fields and at_cursor):
        base["x"], base["y"] = _int(x, "X"), _int(y, "Y")
    if "button" in fields:
        if button not in BUTTONS:
            raise ValueError(f"알 수 없는 버튼: {button!r}")
        base["button"] = button
    if "scroll" in fields:
        base["dx"], base["dy"] = _int(dx, "가로 스크롤"), _int(dy, "세로 스크롤")
        if base["dx"] == 0 and base["dy"] == 0:
            raise ValueError("스크롤 양이 0 입니다")
    if "hold" in fields:
        hold = _int(DEFAULT_HOLD_MS[kind] if hold_ms in (None, "") else hold_ms, "누름 유지(ms)", 0) / 1000
        down, up = ("kdown", "kup") if kind == "tap" else ("mdown", "mup")
        return [{"type": down, **base, "dt": dt}, {"type": up, **base, "dt": hold}]
    return [{"type": kind, **base, "dt": dt}]


def reshape_path(points: list, end_x: int, end_y: int, duration: float) -> list:
    """시작점은 그대로 두고, 끝점 이동량을 경로를 따라 비례 분배하고 시간 간격을 비례 조정."""
    n = len(points)
    old = sum(max(0.0, p[0]) for p in points[1:])
    ddx, ddy = end_x - points[-1][1], end_y - points[-1][2]
    out = []
    for i, (pdt, x, y) in enumerate(points):
        f = i / (n - 1) if n > 1 else 1.0
        if i == 0:
            new_dt = 0.0
        elif old > 0:
            new_dt = round(max(0.0, pdt) * duration / old, 4)
        else:
            new_dt = round(duration / (n - 1), 4)
        out.append([new_dt, round(x + ddx * f), round(y + ddy * f)])
    return out


def item_fields(item: dict) -> dict:
    """편집 대화상자 초기값."""
    out = {"kind": item["type"], "delay_ms": round(item.get("dt", 0.0) * 1000)}
    if item["type"] == "path":
        out["x"], out["y"] = item["points"][-1][1], item["points"][-1][2]
        out["duration_ms"] = round(path_duration(item) * 1000)
        return out
    for k in ("key", "button", "x", "y", "dx", "dy"):
        if k in item:
            out[k] = item[k]
    out["at_cursor"] = "cursor" in KIND_FIELDS[item["type"]] and "x" not in item
    return out


def has_positional(items: list[dict]) -> bool:
    """좌표가 저장된 항목이 있는지 (있으면 좌표 기준을 바꿀 수 없다)."""
    return any(i["type"] == "path" or (i["type"] in POSITIONAL and "x" in i) for i in items)


def unique_name(base: str, existing) -> str:
    lower = {n.lower() for n in existing}
    if base.lower() not in lower:
        return base
    n = 2
    while f"{base} {n}".lower() in lower:
        n += 1
    return f"{base} {n}"


def validate_for_save(name: str, hotkey: str | None, items: list[dict],
                      library: dict, old_name: str | None) -> list[str]:
    errors = []
    try:
        macro_path(".", name)
    except ValueError as e:
        errors.append(f"이름: {e}")
    else:
        for other in library:
            if other != old_name and other.lower() == name.strip().lower():
                errors.append(f"이름: 같은 이름의 매크로가 이미 있습니다 ({other})")
    if hotkey:
        if not keys.is_known(hotkey):
            errors.append(f"핫키: 알 수 없는 키 {hotkey!r}")
        elif hotkey in CONTROL_KEYS:
            errors.append(f"핫키: {', '.join(k.upper() for k in CONTROL_KEYS)} 는 제어 키라 쓸 수 없습니다")
        else:
            for other, m in library.items():
                if other != old_name and m.hotkey == hotkey:
                    errors.append(f"핫키: {hotkey.upper()} 는 '{other}' 매크로가 사용 중입니다")
    if not items:
        errors.append("이벤트가 없습니다 (녹화하거나 추가하세요)")
    forbidden = set(CONTROL_KEYS) | ({hotkey} if hotkey else set())
    used = sorted({i["key"] for i in items if i.get("key") in forbidden})
    if used:
        errors.append(f"이벤트에 핫키/제어 키가 들어 있습니다: {', '.join(used)} (재생 시 충돌)")
    return errors
