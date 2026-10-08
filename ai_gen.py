"""자연어 -> 매크로 생성 (AI). 제공자는 주입한다: Gemini API(무료 등급 가능), Claude API(사용자 API 키),
설치된 Claude Code CLI(요금제 사용량). 설정에서 고르고 make_provider 로 만든다.

AI 는 편집 화면과 같은 dt 기반 단계(delay_ms = 직전으로부터 지연)를 내고, 여기서 절대 시각 t 로 바꿔
profiles 검증기로 확인한다. 화면 캡처(Capture)를 함께 보내면 AI 는 캡처 이미지 픽셀 좌표에 "capture": n 을 붙여
답하고, 여기서 매크로 좌표로 바꾼다. 이미지 조건은 캡처에서 잘라낼 영역(crop)으로 받아 PNG 자산으로 만든다.
캡처를 모으는 방법(키로 여러 장, 3차: 녹화 중 자동)과는 분리되어 있다.
"""
from __future__ import annotations

import base64
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import keys
import vision
from profiles import BUTTONS, Macro, MacroFormatError, macro_path

DEFAULT_NAME = "AI 매크로"
# AI 가 낼 수 있는 단계 (tap/click 은 편의 타입, 여기서 누름/뗌으로 펼친다). set_image 는 아직 제외
ALLOWED_TYPES = ("move", "rmove", "mdown", "mup", "scroll", "kdown", "kup", "tap", "click", "wait",
                 "repeat_start", "repeat_end", "while_start", "while_end", "if_start", "else", "if_end",
                 "break_if", "wait_until", "set_var", "click_image")
ALLOWED_COND_KINDS = ("pixel", "var", "all", "any", "image")
DEFAULT_HOLD_MS = 50
MAX_CAPTURES = 8        # 한 번에 보내는 화면 수 (사용량·시간)
AI_IMAGE_MAX = 1568     # AI 에 보내는 이미지의 긴 변 (넘으면 줄인다)
MIN_CROP = 8            # 잘라낼 이미지의 최소 변 (원본 픽셀)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "짧은 매크로 이름 (글자·숫자·공백·-_.)"},
        "notes": {"type": "string", "description": "사용자가 확인해야 할 점 (가정한 좌표·키 등). 없으면 빈 문자열"},
        "events": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": list(ALLOWED_TYPES)},
            "delay_ms": {"type": "number", "minimum": 0}}, "required": ["type", "delay_ms"]}},
    },
    "required": ["name", "notes", "events"],
}


class AiError(RuntimeError):
    """사용자에게 그대로 보여 줄 한국어 오류."""


@dataclass(eq=False)  # numpy 이미지라 값 비교하지 않는다
class Capture:
    """AI 에 보여 줄 화면 한 장."""
    img: object                        # 전체 화면 원본 (numpy BGR uint8)
    origin: tuple[int, int] = (0, 0)   # 캡처할 때의 매크로 좌표 원점 (창 기준이면 창 좌상단, 화면 좌표)
    label: str = ""                    # 사용자가 붙인 설명 (선택)
    event_index: int | None = None     # 3차: 녹화 중 자동 캡처면 그 직후 이벤트 인덱스

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.img.shape[:2]
        return w, h

    @property
    def scale(self) -> float:
        """AI 에 보내는 이미지 배율 (원본 픽셀 * scale = 이미지 픽셀)."""
        return min(1.0, AI_IMAGE_MAX / max(self.size))


@dataclass
class GenRequest:
    text: str
    screen: tuple[int, int] = (0, 0)
    coord_space: str = "screen"   # screen | window
    window_title: str = ""
    captures: list[Capture] = field(default_factory=list)
    recorded: list[dict] | None = None  # 3차: 녹화한 이벤트 (캡처의 event_index 가 가리킴)
    current: list[dict] | None = None   # 고칠 기존 매크로 이벤트 (절대 t). 있으면 수정 모드
    templates: tuple[str, ...] = ()     # 이미 있는 이미지 파일 이름 (조건에서 그대로 쓸 수 있음)
    history: list[tuple[str, str]] = field(default_factory=list)  # 이전 대화 (역할 "user"/"ai", 내용)


class Provider(Protocol):
    def generate(self, system: str, prompt: str, schema: dict, images: list[bytes]) -> dict: ...


def capture_screen(grabber, origin: tuple[int, int] = (0, 0), label: str = "") -> Capture:
    w, h = grabber.screen_size()
    return Capture(grabber.grab(0, 0, w, h), origin, label)


def resize(img, scale: float):
    """최근접 축소 (scale >= 1 이면 그대로)."""
    import numpy as np
    if scale >= 1:
        return img
    h, w = img.shape[:2]
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    ys = np.minimum((np.arange(nh) / scale).astype(int), h - 1)
    xs = np.minimum((np.arange(nw) / scale).astype(int), w - 1)
    return img[ys][:, xs]


def capture_png(cap: Capture) -> bytes:
    return vision.encode_png(resize(cap.img, cap.scale))


def image_name(i: int) -> str:
    return f"capture_{i + 1}.png"


def from_events(events: list[dict]) -> list[dict]:
    """저장 이벤트(절대 t) -> AI 단계(delay_ms). 수정 모드에서 현재 매크로를 보여 줄 때 쓴다."""
    steps, prev = [], 0.0
    for ev in events:
        step = {"type": ev["type"], "delay_ms": round(max(0.0, ev["t"] - prev) * 1000)}
        step.update({k: v for k, v in ev.items() if k not in ("t", "type")})
        prev = ev["t"]
        steps.append(step)
    return steps


# ---- 프롬프트 ----
def build_system_prompt(req: GenRequest, image_files: bool = True) -> str:
    """image_files: 화면을 작업 폴더 파일로 두고 Read 도구로 읽게 할지 (CLI). 아니면 요청에 바로 첨부된다."""
    w, h = req.screen
    if req.coord_space == "window":
        space = f"좌표는 대상 창('{req.window_title}') 클라이언트 영역 좌상단 기준 픽셀."
    else:
        space = "좌표는 화면 좌상단 기준 픽셀" + (f" (화면 {w}x{h})." if w and h else ".")
    key_names = " ".join(sorted(keys.NAME_TO_SCAN))
    if req.captures:
        shots = "\n".join(
            f"- 화면 {i + 1}: {image_name(i)} (이미지 {round(c.size[0] * c.scale)}x{round(c.size[1] * c.scale)})"
            + (f" — {c.label}" if c.label else "") for i, c in enumerate(req.captures))
        where = ("작업 폴더에 있음). 답하기 전에 Read 도구로 모두 열어 본다" if image_files
                 else "요청에 이 순서대로 첨부됨). 답하기 전에 모두 살펴본다")
        screens = f"""
첨부 화면 (사용자가 게임에서 차례로 캡처한 화면, {where}:
{shots}
- 화면에서 본 위치를 쓸 때는 그 이미지의 픽셀 좌표로 적고 같은 객체에 "capture": 화면 번호 를 붙인다
  (클릭·이동의 x,y / 범위 색 조건의 x,y,w,h). 매크로 좌표로의 변환은 프로그램이 한다.
- 이미지 조건: {{"kind":"image","crop":{{"capture":n,"rect":[x,y,w,h]}},"threshold":0~1(선택, 기본 0.85),
  "region":[x,y,w,h](선택, 같은 화면의 검색 범위)}}. 버튼·아이콘처럼 그 화면을 대표하는 부분을 여백 없이
  {MIN_CROP}px 이상으로 잘라 지정한다. 화면마다 달라지는 숫자·글자(점수, 시간)는 피한다.
- click_image(cond: 이미지 조건, button, offset [x,y](선택), timeout 초(기본 10), on_timeout "stop"|"continue"):
  이미지를 찾을 때까지 기다렸다가 그 가운데를 클릭. 화면이 바뀐 뒤 나오는 버튼 클릭에 쓴다.
- 화면이 바뀌는 동작 뒤에는 wait_until(이미지 조건) 또는 click_image 로 다음 화면을 기다린다.
"""
        image_rule = ("- 새 이미지 조건은 첨부 화면의 crop 으로 만든다 (이미 있는 이미지 파일·이미지 변수는 "
                      "template·image_var 로 그대로 써도 된다).")
    else:
        screens = ""
        image_rule = ("- 첨부 화면이 없으므로 새 이미지는 잘라낼 수 없다 (이미 있는 이미지 파일·이미지 변수만 "
                      "template·image_var 로 쓸 수 있다). 필요하면 notes 에 화면을 캡처해서 다시 요청하라고 적는다.")
    current = ""
    if req.current is not None:
        current = ("\n지금 매크로 (수정 모드): 아래 단계 목록을 사용자 요청대로 고친 **전체** 목록으로 답한다. "
                   "요청과 관계없는 단계는 값·순서를 그대로 둔다(kdown/kup 등 펼쳐진 형태 그대로 써도 된다). "
                   "name 은 지금 이름을 유지하려면 빈 문자열로 둔다.\n"
                   + json.dumps(from_events(req.current), ensure_ascii=False)[:30000] + "\n")
    if req.templates:
        current += ("이미 있는 이미지 파일 (이미지 조건 {\"kind\":\"image\",\"template\":이름} 으로 그대로 쓸 수 있음): "
                    + ", ".join(req.templates) + "\n")
    if req.history:
        current += "\n이전 대화 (참고):\n" + "\n".join(
            f"- {'사용자' if role == 'user' else 'AI'}: {text}" for role, text in req.history[-10:]) + "\n"
    recorded = ""
    if req.recorded:
        recorded = ("\n사용자가 녹화한 입력(절대 시각 t): "
                    + json.dumps(req.recorded, ensure_ascii=False)[:20000]
                    + "\n이 입력을 바탕으로 설명대로 다듬는다.\n")
    return f"""너는 Windows 게임용 입력 매크로 도구의 매크로 작성기다. 사용자의 설명을 입력 단계 목록으로 바꿔 구조화 출력으로만 답한다.

각 단계: {{"type": ..., "delay_ms": 직전 단계 후 기다릴 밀리초, ...필드}}. 단계는 순서대로 실행된다.
{space}

단계 종류:
- tap: 키 한 번 누르고 떼기. key, hold_ms(선택, 기본 {DEFAULT_HOLD_MS})
- kdown / kup: 키 누름 / 뗌 (key). 누른 키는 반드시 뗀다. 조합키는 kdown ctrl, tap c, kup ctrl 처럼.
- click: 마우스 클릭. button, x·y(선택, 없으면 현재 커서 위치), hold_ms(선택)
- mdown / mup: 마우스 버튼 누름 / 뗌 (button, x·y 선택)
- move: 커서를 x, y 로 이동. rmove: 상대 이동 dx, dy
- scroll: dx, dy (dy 양수 = 위로 한 칸, 음수 = 아래로), x·y 선택
- wait: 지연만 (delay_ms 만 사용)
- repeat_start(count: 1~100000, 0 = 무한) ... repeat_end : 사이 단계를 count 번 반복
- while_start(cond) ... while_end : 조건이 맞는 동안 반복
- if_start(cond) ... [else] ... if_end : 조건 분기
- break_if(cond): 조건이 맞으면 가장 안쪽 반복을 끝냄 (반복 안에서만)
- wait_until(cond, timeout 초(0=무제한, 기본 10), on_timeout "stop"|"continue")
- set_var(name, cond): 변수에 조건을 지정 (쓰일 때마다 판정). 이후 {{"kind":"var","name":...}} 로 사용

조건 cond:
- 범위 색: {{"kind":"pixel","x":..,"y":..,"w":정수,"h":정수,"color":"#rrggbb","tolerance":0~255,"ratio":0~1}} (w,h 생략 = 한 점)
- 변수: {{"kind":"var","name":"이름"}}
- 여러 조건: {{"kind":"all"|"any","conds":[잎 조건 2개 이상]}} (중첩 금지)
- 모든 조건에 "negate": true 로 반대 조건.
{screens}{recorded}{current}
규칙:
- 블록 시작/끝은 반드시 짝을 맞추고 엇갈리지 않게 중첩한다.
{image_rule}
- 좌표·색을 사용자가 주지 않았고 화면에서도 알 수 없으면 그럴듯한 값을 넣고 notes 에 확인하라고 적는다.
- 무한 반복(count 0)은 사용자가 계속/무한을 원할 때만. 시간 간격 표현(예: 0.5초마다)은 delay_ms 로.
- 변수 이름은 글자·숫자·_ 1~20자.
- button: {" ".join(sorted(BUTTONS))}
- key 이름 (이 목록만 사용): {key_names}

출력: JSON 객체 하나 {{"name": 짧은 이름, "notes": 확인할 점(없으면 ""), "events": [단계, ...]}}
"""


# ---- 변환·검증 ----
def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class _Converter:
    """캡처 좌표 -> 매크로 좌표, 이미지 crop -> PNG 자산."""

    def __init__(self, req: GenRequest) -> None:
        self.captures = req.captures
        self.templates = set(req.templates)
        self.assets: dict[str, bytes] = {}
        self._by_png: dict[bytes, str] = {}

    def capture(self, obj: dict, where: str) -> Capture | None:
        """obj 의 "capture": 화면 번호를 꺼내 그 캡처를 돌려준다 (없으면 None)."""
        if "capture" not in obj:
            return None
        n = obj.pop("capture")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(self.captures):
            raise MacroFormatError(f"{where}: capture 는 1~{len(self.captures)} 화면 번호여야 합니다 ({n!r})")
        return self.captures[n - 1]

    def _rect(self, cap: Capture, rect, where: str, label: str) -> list[int]:
        """이미지 픽셀 [x,y,w,h] -> 원본 픽셀 (화면 밖이면 오류)."""
        if not (isinstance(rect, list) and len(rect) == 4 and all(_num(v) for v in rect)):
            raise MacroFormatError(f"{where}: {label} 는 [x, y, w, h] 숫자여야 합니다")
        s = cap.scale
        x, y = round(rect[0] / s), round(rect[1] / s)
        w, h = round(rect[2] / s), round(rect[3] / s)
        W, H = cap.size
        if w < 1 or h < 1 or x < 0 or y < 0 or x + w > W or y + h > H:
            raise MacroFormatError(f"{where}: {label} 가 화면 밖입니다 ({rect})")
        return [x, y, w, h]

    def keeps_set_image(self, step: dict) -> bool:
        """수정 모드에서 기존 이미지 변수 지정은 그대로 둘 수 있다 (새로 만들지는 않는다)."""
        return ("capture" in step and isinstance(step["capture"], list)) or step.get("template") in self.templates

    def point(self, obj: dict, where: str) -> None:
        cap = self.capture(obj, where)
        if cap is None:
            return
        for a, b in (("x", 0), ("y", 1)):
            if a in obj:
                if not _num(obj[a]):
                    raise MacroFormatError(f"{where}: {a} 가 숫자가 아닙니다")
                obj[a] = round(obj[a] / cap.scale) - cap.origin[b]
        for a in ("w", "h"):
            if a in obj and _num(obj[a]):
                obj[a] = max(1, round(obj[a] / cap.scale))

    def cond(self, cond, where: str) -> None:
        if not isinstance(cond, dict):
            return  # 구조 오류는 profiles 검증기가 알려 준다
        kind = cond.get("kind")
        if kind not in ALLOWED_COND_KINDS:
            raise MacroFormatError(f"{where}: 지원하지 않는 조건 종류 {kind!r}")
        if kind in ("all", "any"):
            for c in cond.get("conds", []) if isinstance(cond.get("conds"), list) else []:
                self.cond(c, where)
        elif kind == "pixel":
            self.point(cond, where)
        elif kind == "image":
            self.image(cond, where)

    def image(self, cond: dict, where: str) -> None:
        crop = cond.pop("crop", None)
        if crop is None and "image_var" in cond:
            return  # 이미지 변수 참조 (이름은 profiles 검증기가 본다)
        if crop is None and cond.get("template") in self.templates:
            return  # 이미 있는 이미지 파일
        if "template" in cond or "image_var" in cond or not isinstance(crop, dict):
            if not self.captures:
                raise MacroFormatError(f"{where}: 첨부 화면이 없어 이미지 조건을 만들 수 없습니다")
            raise MacroFormatError(f"{where}: 이미지 조건은 crop {{capture, rect}} 로 지정해야 합니다")
        cap = self.capture(crop, where)
        if cap is None:
            raise MacroFormatError(f"{where}: crop 에 capture 화면 번호가 필요합니다")
        x, y, w, h = self._rect(cap, crop.get("rect"), where, "crop.rect")
        if w < MIN_CROP or h < MIN_CROP:
            raise MacroFormatError(f"{where}: 잘라낸 이미지가 너무 작습니다 ({w}x{h}, {MIN_CROP}px 이상)")
        png = vision.encode_png(cap.img[y:y + h, x:x + w])
        name = self._by_png.get(png)
        if name is None:
            n = next(i for i, c in enumerate(self.captures) if c is cap) + 1
            k = len(self.assets) + 1
            while f"ai_{n}_{k}.png" in self.templates:  # 이전 대화에서 만든 이미지와 겹치지 않게
                k += 1
            name = self._by_png[png] = f"ai_{n}_{k}.png"
            self.templates.add(name)
            self.assets[name] = png
        cond["template"] = name
        if "region" in cond:
            rx, ry, rw, rh = self._rect(cap, cond["region"], where, "region")
            cond["region"] = [rx - cap.origin[0], ry - cap.origin[1], rw, rh]


def to_events(steps: list, conv: _Converter | None = None) -> list[dict]:
    """AI 단계(delay_ms) -> 저장 이벤트(절대 t). tap/click 은 누름/뗌으로 펼친다."""
    if not isinstance(steps, list) or not steps:
        raise MacroFormatError("events 가 비어 있습니다")
    conv = conv or _Converter(GenRequest(""))
    events: list[dict] = []
    t = 0.0
    for i, step in enumerate(copy.deepcopy(steps)):
        where = f"events[{i}]"
        if not isinstance(step, dict):
            raise MacroFormatError(f"{where}: 객체가 아닙니다")
        typ = step.get("type")
        if typ not in ALLOWED_TYPES and not (typ == "set_image" and conv.keeps_set_image(step)):
            raise MacroFormatError(f"{where}: 지원하지 않는 type {typ!r}")
        delay = step.get("delay_ms", 0)
        if not _num(delay) or delay < 0:
            raise MacroFormatError(f"{where}: delay_ms 는 0 이상의 숫자여야 합니다")
        t = round(t + delay / 1000, 4)
        if typ != "set_image":  # set_image 의 capture 는 화면 번호가 아니라 캡처 영역
            conv.point(step, where)
        if "cond" in step:
            conv.cond(step["cond"], where)
        fields = {k: v for k, v in step.items() if k not in ("type", "delay_ms", "hold_ms")}
        hold = step.get("hold_ms", DEFAULT_HOLD_MS)
        if typ in ("tap", "click"):
            if not _num(hold) or hold < 0:
                raise MacroFormatError(f"{where}: hold_ms 는 0 이상의 숫자여야 합니다")
            down, up = ("kdown", "kup") if typ == "tap" else ("mdown", "mup")
            if typ == "click":
                fields.setdefault("button", "left")
            events.append({"t": t, "type": down, **fields})
            t = round(t + hold / 1000, 4)
            events.append({"t": t, "type": up, **fields})
        else:
            events.append({"t": t, "type": typ, **fields})
    return events


def to_macro(data, req: GenRequest) -> tuple[str, Macro, str, dict[str, bytes]]:
    """AI 출력 -> (이름, 매크로, 메모, 잘라낸 이미지 {파일 이름: PNG}). 잘못되면 MacroFormatError."""
    if not isinstance(data, dict):
        raise MacroFormatError("최상위 값이 객체가 아닙니다")
    conv = _Converter(req)
    events = to_events(data.get("events"), conv)
    window = {"title": req.window_title} if req.coord_space == "window" and req.window_title else None
    raw = {"version": 1, "coord_space": req.coord_space, "window": window,
           "screen": {"width": req.screen[0], "height": req.screen[1]}, "events": events}
    if window:
        raw["options"] = {"window_title": req.window_title}
    macro = Macro.from_dict(raw)
    name = data.get("name") if isinstance(data.get("name"), str) else ""
    notes = data.get("notes") if isinstance(data.get("notes"), str) else ""
    return clean_name(name), macro, notes.strip(), conv.assets


def clean_name(name: str) -> str:
    name = " ".join(name.split())[:40]
    try:
        macro_path(".", name)
    except ValueError:
        return DEFAULT_NAME
    return name


def generate_macro(req: GenRequest, provider: Provider,
                   retries: int = 1) -> tuple[str, Macro, str, dict[str, bytes]]:
    """설명 -> (이름, 매크로, 메모, 이미지 자산). 형식이 맞지 않으면 오류를 알려 주고 retries 번 다시 요청한다."""
    text = req.text.strip()
    if not text:
        raise AiError("만들 매크로를 설명해 주세요.")
    if len(req.captures) > MAX_CAPTURES:
        raise AiError(f"화면은 {MAX_CAPTURES}장까지 보낼 수 있습니다.")
    system = build_system_prompt(req, getattr(provider, "image_files", True))
    images = [capture_png(c) for c in req.captures]
    prompt = text
    for attempt in range(retries + 1):
        data = provider.generate(system, prompt, OUTPUT_SCHEMA, images)
        try:
            return to_macro(data, req)
        except MacroFormatError as e:
            error = str(e)
        prompt = (f"{text}\n\n[이전 답의 오류] {error}\n"
                  f"이전 답: {json.dumps(data, ensure_ascii=False)[:4000]}\n오류를 고쳐 전체를 다시 답하라.")
    raise AiError(f"AI 가 만든 매크로가 형식에 맞지 않습니다: {error}")


# ---- 제공자: Claude Code CLI ----
class ClaudeCliProvider:
    """설치된 Claude Code 를 `claude -p` 로 호출한다 (로그인한 요금제 사용량에서 차감).
    화면 이미지는 임시 작업 폴더에 capture_n.png 로 두고 Read 도구로만 읽게 한다."""
    image_files = True

    def __init__(self, path: str = "", model: str = "", timeout: float = 180, image_timeout: float = 300,
                 popen=subprocess.Popen) -> None:
        self.path = path or "claude"
        self.model = model
        self.timeout = timeout
        self.image_timeout = image_timeout
        self._popen = popen
        self._proc = None
        self.cancelled = False

    def command(self, system: str, schema: dict, with_images: bool = False) -> list[str]:
        tools = ["--tools", "Read", "--allowedTools", "Read"] if with_images else ["--tools", ""]
        cmd = [self.path, "-p", "--output-format", "json",
               "--json-schema", json.dumps(schema, ensure_ascii=False),
               "--system-prompt", system, *tools, "--no-session-persistence"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def generate(self, system: str, prompt: str, schema: dict, images: list[bytes]) -> dict:
        if self.cancelled:
            raise AiError("취소했습니다.")
        workdir = Path(tempfile.mkdtemp(prefix="macro_ai_")) if images else None
        try:
            for i, png in enumerate(images):
                (workdir / image_name(i)).write_bytes(png)
            return self._run(system, prompt, schema, workdir)
        finally:
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)

    def _run(self, system: str, prompt: str, schema: dict, workdir: Path | None) -> dict:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        timeout = self.image_timeout if workdir else self.timeout
        try:
            path = shutil.which(self.path) or self.path
            cmd = self.command(system, schema, workdir is not None)
            self._proc = self._popen([path, *cmd[1:]], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=str(workdir) if workdir else None, creationflags=flags)
        except FileNotFoundError:
            raise AiError("Claude Code(claude)를 찾을 수 없습니다. 설치하고 로그인한 뒤 다시 시도하세요. "
                          "다른 위치에 있으면 settings.json 의 ai_cli_path 에 경로를 적으세요.") from None
        except OSError as e:
            raise AiError(f"Claude Code 실행 실패: {e}") from None
        try:
            out, err = self._proc.communicate(prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.communicate()
            raise AiError(f"AI 응답이 {int(timeout)}초 안에 오지 않았습니다.") from None
        code = self._proc.returncode
        self._proc = None
        if self.cancelled:
            raise AiError("취소했습니다.")
        return parse_envelope(out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), code)

    def cancel(self) -> None:
        self.cancelled = True
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass


def parse_envelope(out: str, err: str, code: int) -> dict:
    """`claude -p --output-format json` 출력 -> 구조화 결과."""
    try:
        env = json.loads(out)
    except ValueError:
        env = None
    if not isinstance(env, dict):
        detail = (err or out).strip()[-300:]
        if "login" in detail.lower() or "auth" in detail.lower():
            raise AiError(f"Claude Code 로그인이 필요합니다. 터미널에서 claude 를 실행해 로그인하세요. ({detail})")
        raise AiError(f"Claude Code 가 실패했습니다 (코드 {code}): {detail or '출력 없음'}")
    if env.get("is_error") or code:
        detail = str(env.get("result") or env.get("api_error_status") or err.strip() or env.get("subtype"))[-300:]
        raise AiError(f"Claude Code 오류: {detail}")
    data = env.get("structured_output")
    if data is None:
        try:
            data = json.loads(env.get("result") or "")
        except ValueError:
            raise AiError("AI 응답에서 매크로를 읽지 못했습니다.") from None
    return data


# ---- 제공자: HTTP API (사용자 API 키) ----
class _ApiProvider:
    """API 키로 직접 호출하는 제공자 공통: JSON POST, 오류를 한국어로. 이미지는 요청에 바로 첨부한다.
    urlopen 은 테스트에서 주입. 취소는 응답을 버린다 (요청 자체는 timeout 까지 작업 스레드에 남는다)."""
    image_files = False
    label = ""
    default_model = ""
    key_url = ""
    busy_hint = ""  # 503(서버 과부하) 때 덧붙일 안내

    def __init__(self, key: str = "", model: str = "", timeout: float = 120, image_timeout: float = 240,
                 urlopen=None) -> None:
        self.key = key.strip()
        self.model = model.strip() or self.default_model
        self.timeout = timeout
        self.image_timeout = image_timeout
        self._urlopen = urlopen
        self.cancelled = False

    def generate(self, system: str, prompt: str, schema: dict, images: list[bytes]) -> dict:
        if not self.key:
            raise AiError(f"{self.label} API 키가 없습니다. AI 설정에서 키를 입력하세요 (발급: {self.key_url}).")
        if self.cancelled:
            raise AiError("취소했습니다.")
        url, headers, body = self.request(system, prompt, schema, [base64.b64encode(b).decode() for b in images])
        resp = self._post(url, headers, body, self.image_timeout if images else self.timeout)
        if self.cancelled:
            raise AiError("취소했습니다.")
        return self.parse(resp)

    def cancel(self) -> None:
        self.cancelled = True

    def request(self, system: str, prompt: str, schema: dict, images: list[str]) -> tuple[str, dict, dict]:
        """-> (url, 헤더, 본문). images 는 base64 PNG."""
        raise NotImplementedError

    def parse(self, resp: dict) -> dict:
        raise NotImplementedError

    def _post(self, url: str, headers: dict, body: dict, timeout: float) -> dict:
        import urllib.error
        import urllib.request
        urlopen = self._urlopen or urllib.request.urlopen
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"content-type": "application/json", **headers}, method="POST")
        try:
            with urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise AiError(self._http_error(e.code, _error_message(e.read()))) from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise AiError(f"AI 응답이 {int(timeout)}초 안에 오지 않았습니다.") from None
            raise AiError(f"{self.label} 에 연결하지 못했습니다. 인터넷 연결을 확인하세요. ({e.reason})") from None
        except TimeoutError:
            raise AiError(f"AI 응답이 {int(timeout)}초 안에 오지 않았습니다.") from None
        except OSError as e:
            raise AiError(f"{self.label} 요청 실패: {e}") from None
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise AiError(f"{self.label} 응답을 읽지 못했습니다.")
        return data

    def _http_error(self, code: int, detail: str) -> str:
        if code in (401, 403) or "api key" in detail.lower():
            return f"{self.label} API 키가 맞지 않거나 권한이 없습니다. AI 설정에서 확인하세요. ({detail})"
        if code == 429:
            return (f"{self.label} 사용 한도를 넘었습니다 (무료 등급은 분당·하루 요청 수 제한). "
                    f"잠시 후 다시 시도하세요. ({detail})")
        if code == 404:
            return f"{self.label} 모델 '{self.model}' 을 찾을 수 없습니다. AI 설정에서 모델을 확인하세요. ({detail})"
        if code == 503:
            return (f"{self.label} 서버가 지금 바빠 요청을 받지 못했습니다 (일시적). "
                    f"잠시 후 다시 보내 보세요.{self.busy_hint} ({detail})")
        return f"{self.label} 오류 (HTTP {code}): {detail}"


def _error_message(raw: bytes) -> str:
    """API 오류 본문 {"error": {"message": ...}} -> 메시지."""
    text = raw.decode("utf-8", "replace")
    try:
        err = json.loads(text).get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    except (ValueError, AttributeError):
        pass
    return text.strip()[:300] or "내용 없음"


def _json_text(text: str) -> dict:
    """모델이 낸 JSON 글 -> 객체 (```json 울타리 허용)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise AiError("AI 응답에서 매크로를 읽지 못했습니다.")
    return data


class GeminiProvider(_ApiProvider):
    """Google Gemini API (generateContent). AI Studio 에서 무료로 키를 받을 수 있다 (무료 등급은 요청 수 제한).
    responseSchema 는 선언한 필드만 남겨 단계별 필드가 사라지므로 쓰지 않고 JSON 출력만 요구한다 (형식은 프롬프트·검증)."""
    label = "Gemini"
    default_model = "gemini-flash-latest"
    key_url = "https://aistudio.google.com/apikey"
    busy_hint = (" 무료 등급은 사용자가 많을 때 자주 생깁니다. 계속되면 AI 설정의 모델을 "
                 "gemini-flash-lite-latest 로 바꿔 보세요.")

    def request(self, system, prompt, schema, images):
        parts = [{"inlineData": {"mimeType": "image/png", "data": b}} for b in images] + [{"text": prompt}]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseMimeType": "application/json"}}
        return url, {"x-goog-api-key": self.key}, body

    def parse(self, resp):
        cands = resp.get("candidates") or []
        if not cands:
            reason = (resp.get("promptFeedback") or {}).get("blockReason")
            raise AiError(f"Gemini 가 답하지 않았습니다{f' ({reason})' if reason else ''}.")
        cand = cands[0]
        if cand.get("finishReason") == "MAX_TOKENS":
            raise AiError("AI 답이 너무 길어 잘렸습니다. 요청을 나눠 보세요.")
        parts = (cand.get("content") or {}).get("parts") or []
        return _json_text("".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")))


class AnthropicProvider(_ApiProvider):
    """Anthropic Messages API (Claude, API 키 종량제). 출력은 도구 하나를 강제해 그 입력으로 받는다."""
    label = "Claude"
    default_model = "claude-sonnet-5-5"
    key_url = "https://console.anthropic.com/settings/keys"
    max_tokens = 16000

    def request(self, system, prompt, schema, images):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b}}
                   for b in images] + [{"type": "text", "text": prompt}]
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                "messages": [{"role": "user", "content": content}],
                "tools": [{"name": "macro", "description": "만든 매크로를 낸다", "input_schema": schema}],
                "tool_choice": {"type": "tool", "name": "macro"}}
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        return "https://api.anthropic.com/v1/messages", headers, body

    def parse(self, resp):
        if resp.get("stop_reason") == "max_tokens":
            raise AiError("AI 답이 너무 길어 잘렸습니다. 요청을 나눠 보세요.")
        blocks = [b for b in resp.get("content") or [] if isinstance(b, dict)]
        for b in blocks:
            if b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
                return b["input"]
        return _json_text("".join(b.get("text", "") for b in blocks))


PROVIDERS = {  # 설정 값 -> 화면 이름
    "gemini": "Gemini API (무료 등급 가능)",
    "anthropic": "Claude API (API 키, 종량제)",
    "claude_cli": "Claude Code (Pro·Max 구독)",
}
DEFAULT_PROVIDER = "gemini"


def make_provider(cfg) -> Provider:
    """설정(settings 의 ai_* 값) -> 제공자."""
    kind = cfg["ai_provider"] if cfg["ai_provider"] in PROVIDERS else DEFAULT_PROVIDER
    if kind == "gemini":
        return GeminiProvider(cfg["ai_gemini_key"], cfg["ai_gemini_model"])
    if kind == "anthropic":
        return AnthropicProvider(cfg["ai_anthropic_key"], cfg["ai_anthropic_model"])
    return ClaudeCliProvider(cfg["ai_cli_path"], cfg["ai_model"])
