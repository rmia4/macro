import json
import subprocess

import pytest

import ai_gen
from ai_gen import AiError, ClaudeCliProvider, GenRequest, generate_macro, to_events, to_macro
from profiles import MacroFormatError


class FakeProvider:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def generate(self, system, prompt, schema, images):
        self.calls.append((system, prompt))
        return self.answers.pop(0)


class FakeProc:
    def __init__(self, out=b"", err=b"", code=0, timeout=False):
        self.out, self.err, self.returncode, self.timeout = out, err, code, timeout
        self.stdin = None
        self.killed = False

    def communicate(self, data=None, timeout=None):
        if data is not None:
            self.stdin = data
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("claude", timeout)
        return self.out, self.err

    def kill(self):
        self.killed = True


def envelope(**kw):
    env = {"type": "result", "is_error": False, "subtype": "success", "result": ""}
    env.update(kw)
    return json.dumps(env).encode()


# ---- 변환 ----
def test_tap_and_click_expand_with_cumulative_time():
    events = to_events([
        {"type": "tap", "key": "f", "delay_ms": 0},
        {"type": "tap", "key": "f", "delay_ms": 500, "hold_ms": 100},
        {"type": "click", "button": "right", "x": 10, "y": 20, "delay_ms": 200},
        {"type": "wait", "delay_ms": 1000},
    ])
    assert events == [
        {"t": 0.0, "type": "kdown", "key": "f"}, {"t": 0.05, "type": "kup", "key": "f"},
        {"t": 0.55, "type": "kdown", "key": "f"}, {"t": 0.65, "type": "kup", "key": "f"},
        {"t": 0.85, "type": "mdown", "button": "right", "x": 10, "y": 20},
        {"t": 0.9, "type": "mup", "button": "right", "x": 10, "y": 20},
        {"t": 1.9, "type": "wait"},
    ]


def test_click_defaults_to_left_at_cursor():
    events = to_events([{"type": "click", "delay_ms": 0}])
    assert [e["type"] for e in events] == ["mdown", "mup"] and events[0] == {"t": 0.0, "type": "mdown", "button": "left"}


def test_to_macro_validates_blocks_and_fills_screen():
    data = {"name": "F 연타", "notes": " 확인 ", "events": [
        {"type": "repeat_start", "count": 3, "delay_ms": 0},
        {"type": "tap", "key": "f", "delay_ms": 500},
        {"type": "repeat_end", "delay_ms": 0}]}
    name, macro, notes = to_macro(data, GenRequest("x", screen=(1920, 1080)))
    assert name == "F 연타" and notes == "확인"
    assert macro.screen == {"width": 1920, "height": 1080} and macro.coord_space == "screen"
    assert [e["type"] for e in macro.events] == ["repeat_start", "kdown", "kup", "repeat_end"]


def test_to_macro_window_space_sets_play_window():
    data = {"name": "a", "notes": "", "events": [{"type": "click", "x": 1, "y": 2, "delay_ms": 0}]}
    _, macro, _ = to_macro(data, GenRequest("x", coord_space="window", window_title="게임"))
    assert macro.coord_space == "window" and macro.window == {"title": "게임"}
    assert macro.options == {"window_title": "게임"}


@pytest.mark.parametrize("steps, msg", [
    ([], "비어"),
    ([{"type": "click_image", "delay_ms": 0}], "지원하지 않는 type"),
    ([{"type": "tap", "key": "없는키", "delay_ms": 0}], "key"),
    ([{"type": "repeat_start", "count": 2, "delay_ms": 0}], "짝"),
    ([{"type": "wait", "delay_ms": -1}], "delay_ms"),
    ([{"type": "wait_until", "delay_ms": 0, "cond": {"kind": "image", "template": "a.png"}}], "조건 종류"),
    ([{"type": "if_start", "delay_ms": 0, "cond": {"kind": "any", "conds": [
        {"kind": "var", "name": "a"}, {"kind": "image", "template": "a.png"}]}},
      {"type": "if_end", "delay_ms": 0}], "조건 종류"),
])
def test_to_macro_rejects_invalid(steps, msg):
    with pytest.raises(MacroFormatError, match=msg):
        to_macro({"name": "a", "notes": "", "events": steps}, GenRequest("x"))


def test_bad_name_falls_back_to_default():
    data = {"name": "a/b", "notes": "", "events": [{"type": "wait", "delay_ms": 10}]}
    assert to_macro(data, GenRequest("x"))[0] == ai_gen.DEFAULT_NAME


def test_system_prompt_lists_keys_and_coords():
    system = ai_gen.build_system_prompt(GenRequest("x", screen=(2560, 1440)))
    assert "2560x1440" in system and "page_down" in system and "num_add" in system
    system = ai_gen.build_system_prompt(GenRequest("x", coord_space="window", window_title="게임"))
    assert "'게임'" in system


# ---- 생성 (재요청) ----
GOOD = {"name": "ok", "notes": "", "events": [{"type": "tap", "key": "a", "delay_ms": 0}]}


def test_generate_retries_once_with_error():
    bad = {"name": "x", "notes": "", "events": [{"type": "tap", "key": "zz", "delay_ms": 0}]}
    p = FakeProvider(bad, GOOD)
    name, macro, _ = generate_macro(GenRequest("a 눌러"), p)
    assert name == "ok" and len(macro.events) == 2
    assert p.calls[0][1] == "a 눌러" and "[이전 답의 오류]" in p.calls[1][1] and "zz" in p.calls[1][1]


def test_generate_gives_up_after_retries():
    bad = {"name": "x", "notes": "", "events": []}
    with pytest.raises(AiError, match="형식에 맞지 않습니다"):
        generate_macro(GenRequest("a"), FakeProvider(bad, bad))


def test_generate_needs_text():
    with pytest.raises(AiError, match="설명"):
        generate_macro(GenRequest("  "), FakeProvider())


# ---- Claude Code CLI 제공자 ----
def test_cli_command_and_structured_output():
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        seen["proc"] = FakeProc(out=envelope(structured_output=GOOD))
        return seen["proc"]
    p = ClaudeCliProvider("claude-not-on-path", model="sonnet", popen=popen)
    assert p.generate("SYS", "한글 설명", {"type": "object"}, []) == GOOD
    cmd = seen["cmd"]
    assert cmd[0] == "claude-not-on-path" and cmd[1] == "-p"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert cmd[cmd.index("--tools") + 1] == "" and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == {"type": "object"}
    assert seen["proc"].stdin == "한글 설명".encode("utf-8")


def test_cli_falls_back_to_result_text():
    p = ClaudeCliProvider(popen=lambda cmd, **kw: FakeProc(out=envelope(result=json.dumps(GOOD))))
    assert p.generate("s", "p", {}, []) == GOOD


@pytest.mark.parametrize("proc, msg", [
    (FakeProc(out=envelope(is_error=True, result="rate limited")), "rate limited"),
    (FakeProc(out=b"", err=b"Invalid API key \xc2\xb7 Please run /login", code=1), "로그인"),
    (FakeProc(out=b"garbage", code=2), "코드 2"),
    (FakeProc(out=envelope(result="그냥 글")), "읽지 못했습니다"),
])
def test_cli_errors(proc, msg):
    p = ClaudeCliProvider(popen=lambda cmd, **kw: proc)
    with pytest.raises(AiError, match=msg):
        p.generate("s", "p", {}, [])


def test_cli_not_installed():
    def popen(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    with pytest.raises(AiError, match="찾을 수 없습니다"):
        ClaudeCliProvider(popen=popen).generate("s", "p", {}, [])


def test_cli_timeout_kills_process():
    proc = FakeProc(timeout=True)
    with pytest.raises(AiError, match="초 안에"):
        ClaudeCliProvider(timeout=5, popen=lambda cmd, **kw: proc).generate("s", "p", {}, [])
    assert proc.killed


def test_cli_rejects_images_and_cancel():
    p = ClaudeCliProvider(popen=lambda cmd, **kw: FakeProc(out=envelope(structured_output=GOOD)))
    with pytest.raises(AiError, match="이미지"):
        p.generate("s", "p", {}, [b"png"])
    p.cancel()
    with pytest.raises(AiError, match="취소"):
        p.generate("s", "p", {}, [])
