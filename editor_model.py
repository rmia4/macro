"""편집 화면의 순수 로직: 이벤트 <-> 편집 항목 변환, 이벤트 생성, 저장 전 검증.

편집 항목(item)은 이벤트에서 절대 시각 "t" 대신 "dt"(직전 이벤트로부터의 지연, 초)를 가진다.
연속된 마우스 이동은 하나의 "path" 항목으로 묶는다: {"type": "path", "dt", "points": [[dt, x, y], ...]}
(points[0] 의 dt 는 0, 항목의 dt 가 첫 점 앞의 지연). 이벤트로 바꿀 때 다시 move 들로 풀린다.
연속된 상대 이동(rmove)도 같은 방식으로 "relpath" 항목이 된다 (points: [[dt, dx, dy], ...]).
"""
from __future__ import annotations

import re

import keys
import vision
from hotkeys import CONTROL_KEYS
from profiles import BUTTONS, MAX_REPEAT, block_pairs, blocks, macro_path

EVENT_LABELS = {"move": "마우스 이동", "mdown": "마우스 누름", "mup": "마우스 뗌",
                "scroll": "스크롤", "kdown": "키 누름", "kup": "키 뗌", "wait": "지연",
                "path": "마우스 이동 경로", "rmove": "마우스 상대 이동", "relpath": "상대 이동 경로",
                "repeat_start": "🔁 반복 시작", "repeat_end": "🔁 반복 끝",
                "wait_until": "🔍 조건 대기", "if_start": "❓ 만약", "else": "↪ 아니면",
                "if_end": "❓ 분기 끝", "break_if": "⏹ 반복 탈출", "click_image": "🖱 이미지 클릭"}
BLOCK_MARKERS = ("repeat_start", "repeat_end", "if_start", "else", "if_end")

# 추가 가능한 종류 (tap/click 은 누름+뗌 두 개의 이벤트를 만든다)
ADD_KINDS = [("tap", "키 입력 (누르고 떼기)"), ("kdown", "키 누름"), ("kup", "키 뗌"),
             ("click", "마우스 클릭"), ("mdown", "마우스 누름"), ("mup", "마우스 뗌"),
             ("move", "마우스 이동"), ("rmove", "마우스 상대 이동"), ("scroll", "스크롤"), ("wait", "지연")]
EDIT_KINDS = [k for k in ADD_KINDS if k[0] not in ("tap", "click")]
PATH_KINDS = [("path", "마우스 이동 경로")]  # 경로 항목 수정 전용
RELPATH_KINDS = [("relpath", "상대 이동 경로")]
REPEAT_START_KINDS = [("repeat_start", "반복 시작")]
REPEAT_END_KINDS = [("repeat_end", "반복 끝")]
ELSE_KINDS = [("else", "아니면")]
IF_END_KINDS = [("if_end", "분기 끝")]

# 종류별 입력 필드 (cursor: 좌표 대신 현재 커서 위치에서 입력 가능)
KIND_FIELDS = {
    "tap": {"key", "hold"}, "kdown": {"key"}, "kup": {"key"},
    "click": {"button", "pos", "cursor", "hold"}, "mdown": {"button", "pos", "cursor"},
    "mup": {"button", "pos", "cursor"}, "move": {"pos"}, "scroll": {"pos", "cursor", "scroll"},
    "wait": set(), "path": {"pos", "duration"}, "rmove": {"delta"}, "relpath": {"duration", "scale"},
    "repeat_start": {"count"}, "repeat_end": set(), "else": set(), "if_end": set(),
}
GROUPED = {"move": "path", "rmove": "relpath"}  # 연속되면 묶이는 이벤트 -> 묶음 항목 종류
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
        typ = ev["type"]
        if typ in GROUPED and last is not None and last["type"] in (typ, GROUPED[typ]):
            a, b = ("x", "y") if typ == "move" else ("dx", "dy")
            if last["type"] == typ:  # 두 번째 연속 이동부터 경로로 묶는다
                last = items[-1] = {"type": GROUPED[typ], "dt": last["dt"],
                                    "points": [[0.0, last[a], last[b]]]}
            last["points"].append([dt, ev[a], ev[b]])
            continue
        item = {k: v for k, v in ev.items() if k != "t"}
        item["dt"] = dt
        items.append(item)
    return items


def to_events(items: list[dict]) -> list[dict]:
    events, t = [], 0.0
    for item in items:
        if item["type"] in ("path", "relpath"):
            typ, a, b = ("move", "x", "y") if item["type"] == "path" else ("rmove", "dx", "dy")
            for i, (pdt, u, v) in enumerate(item["points"]):
                t = round(t + max(0.0, item.get("dt", 0.0) if i == 0 else pdt), 4)
                events.append({"t": t, "type": typ, a: u, b: v})
            continue
        t = round(t + max(0.0, item.get("dt", 0.0)), 4)
        ev = {"t": t}
        ev.update({k: v for k, v in item.items() if k != "dt"})
        events.append(ev)
    return events


def path_duration(item: dict) -> float:
    return round(sum(max(0.0, p[0]) for p in item["points"][1:]), 4)


def item_duration(item: dict) -> float:
    return max(0.0, item.get("dt", 0.0)) + (path_duration(item) if "points" in item else 0.0)


def expanded_duration(items: list[dict]) -> float:
    """반복 구간을 펼쳤을 때의 실행 시간. 한 바퀴 = 구간 안 이벤트들 + '반복 끝' 지연.
    무한 반복이 있으면 inf. 조건 분기·조건 대기는 판정 결과를 알 수 없어 모든 항목을 그대로 더한 추정값."""
    stack = [[0.0, 1]]  # [누적 시간, 반복 횟수]
    for it in items:
        if it["type"] == "repeat_start":
            stack[-1][0] += item_duration(it)
            count = it.get("count", 1)
            stack.append([0.0, count if count > 0 else float("inf")])
        elif it["type"] == "repeat_end" and len(stack) > 1:
            inner, count = stack.pop()
            stack[-1][0] += (inner + item_duration(it)) * count
        else:
            stack[-1][0] += item_duration(it)
    while len(stack) > 1:  # 짝이 안 맞는 경우(편집 중)는 1회로 계산
        inner, _ = stack.pop()
        stack[-1][0] += inner
    total = stack[0][0]
    return total if total == float("inf") else round(total, 4)


def depths(items: list[dict]) -> list[int]:
    """각 항목의 반복 구간 중첩 깊이 (표시용 들여쓰기)."""
    out, d = [], 0
    for it in items:
        typ = it["type"]
        if typ in ("repeat_end", "if_end"):
            d = max(0, d - 1)
        out.append(max(0, d - 1) if typ == "else" else d)
        if typ in ("repeat_start", "if_start"):
            d += 1
    return out


def block_partner(items: list[dict], i: int) -> int | None:
    """반복 시작/끝 표시의 짝 인덱스."""
    try:
        pairs = block_pairs(items)
    except ValueError:
        return None
    rev = {v: k for k, v in pairs.items()}
    return pairs.get(i, rev.get(i))


def block_members(items: list[dict], i: int) -> set[int]:
    """구간 표시 i 를 지울 때 함께 지울 표시들. 시작/끝은 서로(+'아니면'), '아니면'은 혼자."""
    try:
        b = blocks(items)
    except ValueError:
        return {i}
    for start, end in b.repeat.items():
        if i in (start, end):
            return {start, end}
    for start, end in b.if_end.items():
        if i in (start, end):
            return {start, end} | ({b.if_else[start]} if start in b.if_else else set())
    return {i}


def block_error(items: list[dict]) -> str | None:
    try:
        blocks(items)
        return None
    except ValueError as e:
        return str(e)


def wrap_repeat(items: list[dict], selected: list[int], count: int) -> tuple[list[dict], list[int]]:
    """선택한 범위(첫~마지막)를 반복 구간으로 감싼다. 선택이 없으면 끝에 빈 구간을 추가.

    범위가 다른 구간과 엇갈리면(시작만 포함 등) ValueError. (새 목록, 새 선택) 반환.
    """
    if not 0 <= count <= MAX_REPEAT:
        raise ValueError(f"반복 횟수는 0(무한)~{MAX_REPEAT} 이어야 합니다")
    return _wrap(items, selected, {"type": "repeat_start", "count": count, "dt": 0.0}, [{"type": "repeat_end", "dt": 0.0}])


def wrap_if(items: list[dict], selected: list[int], cond: dict, with_else: bool = False) -> tuple[list[dict], list[int]]:
    """선택 범위를 '만약 [조건]' ~ ('아니면') ~ '분기 끝'으로 감싼다."""
    vision.validate_condition(cond)
    tail = ([{"type": "else", "dt": 0.0}] if with_else else []) + [{"type": "if_end", "dt": 0.0}]
    return _wrap(items, selected, {"type": "if_start", "cond": cond, "dt": 0.0}, tail)


def _wrap(items, selected, start: dict, tail: list[dict]) -> tuple[list[dict], list[int]]:
    if not selected:
        return items + [start] + tail, [len(items), len(items) + len(tail)]
    a, b = min(selected), max(selected)
    inner = items[a:b + 1]
    if block_error(inner):
        raise ValueError("선택 범위가 다른 구간과 겹칩니다. 구간 전체를 포함하도록 선택하세요")
    start = dict(start, dt=inner[0].get("dt", 0.0))  # 첫 이벤트 앞의 지연은 구간 앞으로 옮긴다
    inner = [dict(inner[0], dt=0.0)] + inner[1:]
    return items[:a] + [start] + inner + tail + items[b + 1:], [a, b + 1 + len(tail)]


def total_duration(items: list[dict]) -> float:
    return round(sum(max(0.0, i.get("dt", 0.0)) + (path_duration(i) if "points" in i else 0)
                     for i in items), 4)


def event_count(items: list[dict]) -> int:
    return sum(len(i["points"]) if "points" in i else 1 for i in items)


def describe(item: dict) -> str:
    typ = item["type"]
    if typ in ("kdown", "kup"):
        return item["key"]
    if typ == "wait":
        return ""
    if typ == "repeat_start":
        return f"×{item['count']}회 반복" if item["count"] > 0 else "무한 반복 (반복 탈출로 종료)"
    if typ == "if_start":
        return f"{vision.describe_condition(item['cond'])} 이면"
    if typ == "break_if":
        return f"{vision.describe_condition(item['cond'])} 이면 반복 구간 종료"
    if typ == "click_image":
        timeout = item.get("timeout", 10)
        limit = f"최대 {timeout:g}초" if timeout > 0 else "무제한"
        after = "중지" if item.get("on_timeout", "stop") == "stop" else "계속"
        dx, dy = item.get("offset", [0, 0])
        shift = f" ({dx:+g}, {dy:+g})" if dx or dy else ""
        return f"'{item['cond']['template']}'{shift} {item.get('button', 'left')} 클릭 · {limit} · 초과 시 {after}"
    if typ in ("else", "if_end"):
        return ""
    if typ == "wait_until":
        timeout = item.get("timeout", 10)
        limit = f"최대 {timeout:g}초" if timeout > 0 else "무제한"
        after = "중지" if item.get("on_timeout", "stop") == "stop" else "계속"
        return f"{vision.describe_condition(item['cond'])} · {limit} · 초과 시 {after}"
    if typ == "repeat_end":
        return ""
    if typ == "path":
        pts = item["points"]
        return (f"({pts[0][1]}, {pts[0][2]}) → ({pts[-1][1]}, {pts[-1][2]}) · "
                f"{len(pts)}개 지점 · {round(path_duration(item) * 1000)}ms")
    if typ == "relpath":
        pts = item["points"]
        return (f"총 이동 ({sum(p[1] for p in pts)}, {sum(p[2] for p in pts)}) · "
                f"{len(pts)}회 · {round(path_duration(item) * 1000)}ms")
    if typ == "rmove":
        return f"이동량 ({item['dx']}, {item['dy']})"
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
                duration_ms=None, points: list | None = None, scale_pct="100", count="2") -> list[dict]:
    """입력값으로 편집 항목을 만든다. 잘못된 값은 ValueError.

    at_cursor: 버튼/스크롤을 좌표 없이 현재 커서 위치에서 입력.
    kind == "path": points(원래 경로)를 끝 위치 (x, y)·이동 시간 duration_ms 에 맞게 보정한다.
    kind == "relpath": points 의 시간 간격을 duration_ms 에, 이동량을 scale_pct(%) 배율에 맞춘다.
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
    if kind == "relpath":
        if not points:
            raise ValueError("경로 정보가 없습니다")
        scale = _int(scale_pct, "이동량 배율(%)", 1) / 100
        return [{"type": "relpath", "dt": dt,
                 "points": rescale_relpath(points, _int(duration_ms, "이동 시간(ms)", 0) / 1000, scale)}]
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
    if "count" in fields:
        base["count"] = _int(count, "반복 횟수", 0)
        if base["count"] > MAX_REPEAT:
            raise ValueError(f"반복 횟수는 {MAX_REPEAT} 이하여야 합니다")
    if "delta" in fields:
        base["dx"], base["dy"] = _int(dx, "이동량 X"), _int(dy, "이동량 Y")
        if base["dx"] == 0 and base["dy"] == 0:
            raise ValueError("이동량이 0 입니다")
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


def rescale_relpath(points: list, duration: float, scale: float) -> list:
    """상대 이동 경로의 시간 간격과 이동량을 비례 조정. 반올림 오차는 누적해서 총 이동량을 보존한다."""
    n = len(points)
    old = sum(max(0.0, p[0]) for p in points[1:])
    out, acc, sent = [], [0.0, 0.0], [0, 0]
    for i, (pdt, dx, dy) in enumerate(points):
        if i == 0:
            new_dt = 0.0
        elif old > 0:
            new_dt = round(max(0.0, pdt) * duration / old, 4)
        else:
            new_dt = round(duration / (n - 1), 4)
        acc[0] += dx * scale
        acc[1] += dy * scale
        ndx, ndy = round(acc[0]) - sent[0], round(acc[1]) - sent[1]
        sent[0] += ndx
        sent[1] += ndy
        out.append([new_dt, ndx, ndy])
    return out


def build_condition(*, kind="image", template="", region=None, threshold_pct="85",
                    x="0", y="0", w=None, h=None, color="#000000", tolerance="20", ratio_pct="50",
                    negate=False) -> dict:
    """조건. 이미지 region: None(화면 전체) 또는 [x, y, w, h]. 색: (x, y)에서 w x h 범위
    (w, h 가 None 이면 한 점). 값은 문자열도 허용. 잘못되면 ValueError."""
    if kind == "image":
        cond = {"kind": "image", "template": str(template).strip(),
                "threshold": _int(threshold_pct, "일치도 기준(%)", 1) / 100}
        if region is not None:
            cond["region"] = [_int(region[0], "영역 X"), _int(region[1], "영역 Y"),
                              _int(region[2], "영역 너비", 1), _int(region[3], "영역 높이", 1)]
    else:
        cond = {"kind": "pixel", "x": _int(x, "X"), "y": _int(y, "Y"),
                "color": str(color).strip().lower(), "tolerance": _int(tolerance, "허용 오차", 0)}
        if w is not None or h is not None:
            cond["w"], cond["h"] = _int(w, "범위 너비", 1), _int(h, "범위 높이", 1)
            cond["ratio"] = _int(ratio_pct, "색 비율(%)", 1) / 100
    if negate:
        cond["negate"] = True
    vision.validate_condition(cond)
    return cond


def _wait_fields(timeout_s, on_timeout, interval_ms) -> dict:
    try:
        timeout = float(str(timeout_s).strip())
    except ValueError:
        raise ValueError("최대 대기(초): 숫자를 입력하세요") from None
    if timeout < 0:
        raise ValueError("최대 대기(초): 0 이상이어야 합니다 (0 = 무제한)")
    if on_timeout not in ("stop", "continue"):
        raise ValueError("시간 초과 시 동작이 잘못되었습니다")
    return {"timeout": timeout, "on_timeout": on_timeout, "interval": _int(interval_ms, "확인 간격(ms)", 10) / 1000}


def build_wait_until(*, delay_ms="0", timeout_s="10", on_timeout="stop", interval_ms="100", **cond_kw) -> dict:
    """조건 대기 항목."""
    cond = build_condition(**cond_kw)
    return {"type": "wait_until", "dt": _int(delay_ms, "앞 지연(ms)", 0) / 1000, "cond": cond,
            **_wait_fields(timeout_s, on_timeout, interval_ms)}


def build_check(kind_event: str, *, delay_ms="0", **cond_kw) -> dict:
    """판정 한 번 하는 항목: 'if_start'(만약) 또는 'break_if'(반복 탈출)."""
    if kind_event not in ("if_start", "break_if"):
        raise ValueError(f"알 수 없는 종류: {kind_event}")
    return {"type": kind_event, "dt": _int(delay_ms, "앞 지연(ms)", 0) / 1000, "cond": build_condition(**cond_kw)}


def build_click_image(*, delay_ms="0", button="left", offset_x="0", offset_y="0", hold_ms="60",
                      timeout_s="10", on_timeout="stop", interval_ms="100", **cond_kw) -> dict:
    """이미지 클릭 항목: 이미지가 보일 때까지 기다렸다가 찾은 위치(+보정)를 클릭."""
    cond = build_condition(**dict(cond_kw, negate=False))
    if cond["kind"] != "image":
        raise ValueError("이미지 클릭은 이미지 조건만 쓸 수 있습니다")
    if button not in BUTTONS:
        raise ValueError(f"알 수 없는 버튼: {button!r}")
    return {"type": "click_image", "dt": _int(delay_ms, "앞 지연(ms)", 0) / 1000, "cond": cond,
            "button": button, "offset": [_int(offset_x, "보정 X"), _int(offset_y, "보정 Y")],
            "hold": _int(hold_ms, "누름 유지(ms)", 0) / 1000, **_wait_fields(timeout_s, on_timeout, interval_ms)}


def item_fields(item: dict) -> dict:
    """편집 대화상자 초기값."""
    out = {"kind": item["type"], "delay_ms": round(item.get("dt", 0.0) * 1000)}
    if item["type"] == "path":
        out["x"], out["y"] = item["points"][-1][1], item["points"][-1][2]
        out["duration_ms"] = round(path_duration(item) * 1000)
        return out
    if item["type"] == "relpath":
        out["duration_ms"] = round(path_duration(item) * 1000)
        out["scale_pct"] = 100
        return out
    for k in ("key", "button", "x", "y", "dx", "dy", "count"):
        if k in item:
            out[k] = item[k]
    out["at_cursor"] = "cursor" in KIND_FIELDS[item["type"]] and "x" not in item
    return out


def has_positional(items: list[dict]) -> bool:
    """좌표가 저장된 항목이 있는지 (있으면 좌표 기준을 바꿀 수 없다)."""
    return any(i["type"] == "path" or (i["type"] in POSITIONAL and "x" in i) or "cond" in i for i in items)


def unique_name(base: str, existing) -> str:
    lower = {n.lower() for n in existing}
    if base.lower() not in lower:
        return base
    n = 2
    while f"{base} {n}".lower() in lower:
        n += 1
    return f"{base} {n}"


def validate_for_save(name: str, hotkey: str | None, items: list[dict],
                      library: dict, old_name: str | None, reserved=(),
                      available_templates: set[str] | None = None) -> list[str]:
    """reserved: 매크로가 쓸 수 없는 다른 단축키 (예: 전체 실행 전환 키).
    available_templates: 사용 가능한 조건 이미지 이름 (None 이면 검사하지 않음)."""
    errors = []
    try:
        macro_path(".", name)
    except ValueError as e:
        errors.append(f"이름: {e}")
    else:
        for other in library:
            if other != old_name and other.lower() == name.strip().lower():
                errors.append(f"이름: 같은 이름의 매크로가 이미 있습니다 ({other})")
    parts: list[str] = []
    if hotkey:
        try:
            hotkey = keys.parse_hotkey(hotkey)
            parts = keys.hotkey_parts(hotkey)
        except ValueError as e:
            errors.append(f"핫키: {e}")
            hotkey = None
    if hotkey:
        if any(p in CONTROL_KEYS for p in parts):
            errors.append(f"핫키: {', '.join(k.upper() for k in CONTROL_KEYS)} 는 제어 키라 쓸 수 없습니다")
        elif hotkey in reserved:
            errors.append(f"핫키: {hotkey.upper()} 는 전체 실행 전환 키로 쓰이고 있습니다")
        else:
            for other, m in library.items():
                if other != old_name and m.hotkey == hotkey:
                    errors.append(f"핫키: {hotkey.upper()} 는 '{other}' 매크로가 사용 중입니다")
    if not items:
        errors.append("이벤트가 없습니다 (녹화하거나 추가하세요)")
    err = block_error(items)
    if err:
        errors.append(f"반복 구간: {err}")
    if available_templates is not None:
        missing = sorted(vision.templates_in(items) - available_templates)
        if missing:
            errors.append(f"조건 이미지가 없습니다: {', '.join(missing)}")
    used_keys = {keys.hotkey_key(i["key"]) for i in items if "key" in i}
    bad = sorted(k for k in CONTROL_KEYS if k in used_keys)
    for combo in ([hotkey] if parts else []) + [r for r in reserved if r]:
        if all(p in used_keys for p in keys.hotkey_parts(combo)):
            bad.append(combo)
    if bad:
        errors.append(f"이벤트에 핫키/제어 키가 들어 있습니다: {', '.join(bad)} (재생 시 충돌)")
    return errors
