"""자연어 -> 매크로 생성 (AI). 제공자는 주입한다(기본: 설치된 Claude Code CLI, 사용자의 요금제로 호출).

AI 는 편집 화면과 같은 dt 기반 단계(delay_ms = 직전으로부터 지연)를 내고, 여기서 절대 시각 t 로 바꿔
profiles 검증기로 확인한다. 1차는 텍스트만: 이미지 조건·이미지 클릭·이미지 변수는 만들지 않는다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Protocol

import keys
from profiles import BUTTONS, Macro, MacroFormatError, macro_path

DEFAULT_NAME = "AI 매크로"
# 1차에서 AI 가 낼 수 있는 단계 (tap/click 은 편의 타입, 여기서 누름/뗌으로 펼친다)
ALLOWED_TYPES = ("move", "rmove", "mdown", "mup", "scroll", "kdown", "kup", "tap", "click", "wait",
                 "repeat_start", "repeat_end", "while_start", "while_end", "if_start", "else", "if_end",
                 "break_if", "wait_until", "set_var")
ALLOWED_COND_KINDS = ("pixel", "var", "all", "any")
DEFAULT_HOLD_MS = 50

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


@dataclass
class GenRequest:
    text: str
    screen: tuple[int, int] = (0, 0)
    coord_space: str = "screen"   # screen | window
    window_title: str = ""
    images: list[bytes] = field(default_factory=list)  # 2차: 화면 캡처 PNG (1차는 비어 있음)


class Provider(Protocol):
    def generate(self, system: str, prompt: str, schema: dict, images: list[bytes]) -> dict: ...


# ---- 프롬프트 ----
def build_system_prompt(req: GenRequest) -> str:
    w, h = req.screen
    if req.coord_space == "window":
        space = f"좌표는 대상 창('{req.window_title}') 클라이언트 영역 좌상단 기준 픽셀."
    else:
        space = "좌표는 화면 좌상단 기준 픽셀" + (f" (화면 {w}x{h})." if w and h else ".")
    key_names = " ".join(sorted(keys.NAME_TO_SCAN))
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

규칙:
- 블록 시작/끝은 반드시 짝을 맞추고 엇갈리지 않게 중첩한다.
- 이미지(그림) 찾기·이미지 클릭은 아직 지원하지 않는다. 필요하면 notes 에 사용자가 기록 화면에서 직접 추가하라고 적는다.
- 좌표·색을 사용자가 주지 않았는데 필요하면 그럴듯한 값을 넣고 notes 에 확인하라고 적는다.
- 무한 반복(count 0)은 사용자가 계속/무한을 원할 때만. 시간 간격 표현(예: 0.5초마다)은 delay_ms 로.
- 변수 이름은 글자·숫자·_ 1~20자.
- button: {" ".join(sorted(BUTTONS))}
- key 이름 (이 목록만 사용): {key_names}
"""


# ---- 변환·검증 ----
def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_cond(cond, where: str) -> None:
    if not isinstance(cond, dict):
        return  # 구조 오류는 profiles 검증기가 알려 준다
    kinds = [cond.get("kind")] + [c.get("kind") for c in cond.get("conds", []) if isinstance(c, dict)]
    for kind in kinds:
        if kind not in ALLOWED_COND_KINDS:
            raise MacroFormatError(f"{where}: 지원하지 않는 조건 종류 {kind!r} (pixel/var/all/any 만)")


def to_events(steps: list) -> list[dict]:
    """AI 단계(delay_ms) -> 저장 이벤트(절대 t). tap/click 은 누름/뗌으로 펼친다."""
    if not isinstance(steps, list) or not steps:
        raise MacroFormatError("events 가 비어 있습니다")
    events: list[dict] = []
    t = 0.0
    for i, step in enumerate(steps):
        where = f"events[{i}]"
        if not isinstance(step, dict):
            raise MacroFormatError(f"{where}: 객체가 아닙니다")
        typ = step.get("type")
        if typ not in ALLOWED_TYPES:
            raise MacroFormatError(f"{where}: 지원하지 않는 type {typ!r}")
        delay = step.get("delay_ms", 0)
        if not _num(delay) or delay < 0:
            raise MacroFormatError(f"{where}: delay_ms 는 0 이상의 숫자여야 합니다")
        t = round(t + delay / 1000, 4)
        if "cond" in step:
            _check_cond(step["cond"], where)
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


def to_macro(data, req: GenRequest) -> tuple[str, Macro, str]:
    """AI 출력 -> (이름, 매크로, 메모). 잘못되면 MacroFormatError."""
    if not isinstance(data, dict):
        raise MacroFormatError("최상위 값이 객체가 아닙니다")
    events = to_events(data.get("events"))
    window = {"title": req.window_title} if req.coord_space == "window" and req.window_title else None
    raw = {"version": 1, "coord_space": req.coord_space, "window": window,
           "screen": {"width": req.screen[0], "height": req.screen[1]}, "events": events}
    if window:
        raw["options"] = {"window_title": req.window_title}
    macro = Macro.from_dict(raw)
    name = data.get("name") if isinstance(data.get("name"), str) else ""
    notes = data.get("notes") if isinstance(data.get("notes"), str) else ""
    return clean_name(name), macro, notes.strip()


def clean_name(name: str) -> str:
    name = " ".join(name.split())[:40]
    try:
        macro_path(".", name)
    except ValueError:
        return DEFAULT_NAME
    return name


def generate_macro(req: GenRequest, provider: Provider, retries: int = 1) -> tuple[str, Macro, str]:
    """설명 -> (이름, 매크로, 메모). 결과가 형식에 맞지 않으면 오류를 알려 주고 retries 번 다시 요청한다."""
    text = req.text.strip()
    if not text:
        raise AiError("만들 매크로를 설명해 주세요.")
    system = build_system_prompt(req)
    prompt = text
    for attempt in range(retries + 1):
        data = provider.generate(system, prompt, OUTPUT_SCHEMA, req.images)
        try:
            return to_macro(data, req)
        except MacroFormatError as e:
            error = str(e)
        prompt = (f"{text}\n\n[이전 답의 오류] {error}\n"
                  f"이전 답: {json.dumps(data, ensure_ascii=False)[:4000]}\n오류를 고쳐 전체를 다시 답하라.")
    raise AiError(f"AI 가 만든 매크로가 형식에 맞지 않습니다: {error}")


# ---- 제공자: Claude Code CLI ----
class ClaudeCliProvider:
    """설치된 Claude Code 를 `claude -p` 로 호출한다 (로그인한 요금제 사용량에서 차감)."""

    def __init__(self, path: str = "", model: str = "", timeout: float = 180, popen=subprocess.Popen) -> None:
        self.path = path or "claude"
        self.model = model
        self.timeout = timeout
        self._popen = popen
        self._proc = None
        self.cancelled = False

    def command(self, system: str, schema: dict) -> list[str]:
        cmd = [self.path, "-p", "--output-format", "json",
               "--json-schema", json.dumps(schema, ensure_ascii=False),
               "--system-prompt", system, "--tools", "", "--no-session-persistence"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def generate(self, system: str, prompt: str, schema: dict, images: list[bytes]) -> dict:
        if images:
            raise AiError("화면 이미지 첨부는 아직 지원하지 않습니다.")
        if self.cancelled:
            raise AiError("취소했습니다.")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            path = shutil.which(self.path) or self.path
            self._proc = self._popen([path, *self.command(system, schema)[1:]], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=flags)
        except FileNotFoundError:
            raise AiError("Claude Code(claude)를 찾을 수 없습니다. 설치하고 로그인한 뒤 다시 시도하세요. "
                          "다른 위치에 있으면 settings.json 의 ai_cli_path 에 경로를 적으세요.") from None
        except OSError as e:
            raise AiError(f"Claude Code 실행 실패: {e}") from None
        try:
            out, err = self._proc.communicate(prompt.encode("utf-8"), timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.communicate()
            raise AiError(f"AI 응답이 {int(self.timeout)}초 안에 오지 않았습니다.") from None
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
