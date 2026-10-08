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
    name, macro, notes, assets = to_macro(data, GenRequest("x", screen=(1920, 1080)))
    assert name == "F 연타" and notes == "확인" and assets == {}
    assert macro.screen == {"width": 1920, "height": 1080} and macro.coord_space == "screen"
    assert [e["type"] for e in macro.events] == ["repeat_start", "kdown", "kup", "repeat_end"]


def test_to_macro_window_space_sets_play_window():
    data = {"name": "a", "notes": "", "events": [{"type": "click", "x": 1, "y": 2, "delay_ms": 0}]}
    _, macro, _, _ = to_macro(data, GenRequest("x", coord_space="window", window_title="게임"))
    assert macro.coord_space == "window" and macro.window == {"title": "게임"}
    assert macro.options == {"window_title": "게임"}


@pytest.mark.parametrize("steps, msg", [
    ([], "비어"),
    ([{"type": "set_image", "delay_ms": 0}], "지원하지 않는 type"),
    ([{"type": "click_image", "delay_ms": 0, "cond": {"kind": "image", "crop": {"capture": 1, "rect": [0, 0, 9, 9]}}}],
     "capture"),
    ([{"type": "tap", "key": "없는키", "delay_ms": 0}], "key"),
    ([{"type": "repeat_start", "count": 2, "delay_ms": 0}], "짝"),
    ([{"type": "wait", "delay_ms": -1}], "delay_ms"),
    ([{"type": "wait_until", "delay_ms": 0, "cond": {"kind": "image", "template": "a.png"}}], "첨부 화면이 없어"),
    ([{"type": "if_start", "delay_ms": 0, "cond": {"kind": "any", "conds": [
        {"kind": "var", "name": "a"}, {"kind": "image_var", "name": "a"}]}},
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
    name, macro, _, _ = generate_macro(GenRequest("a 눌러"), p)
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


def test_cli_cancel():
    p = ClaudeCliProvider(popen=lambda cmd, **kw: FakeProc(out=envelope(structured_output=GOOD)))
    p.cancel()
    with pytest.raises(AiError, match="취소"):
        p.generate("s", "p", {}, [])


# ---- 화면 캡처 ----
np = pytest.importorskip("numpy")
import vision  # noqa: E402
from ai_gen import Capture  # noqa: E402


def screen(w=3136, h=1764):
    img = np.zeros((h, w, 3), np.uint8)
    img[100:140, 200:300] = (0, 0, 255)  # 빨간 버튼 (원본 픽셀)
    img[100:140, 230:240] = (255, 255, 255)
    return img


def test_capture_scale_and_png_size():
    cap = Capture(screen())
    assert cap.scale == 0.5
    png = ai_gen.capture_png(cap)
    assert vision.decode_png(png).shape[:2] == (882, 1568)
    assert Capture(np.zeros((1080, 1920, 3), np.uint8)).scale == pytest.approx(1568 / 1920)
    assert Capture(np.zeros((100, 200, 3), np.uint8)).scale == 1.0


def test_capture_coords_convert_to_macro_coords():
    req = GenRequest("x", coord_space="window", window_title="g",
                     captures=[Capture(screen(), origin=(0, 0)), Capture(screen(), origin=(100, 50))])
    data = {"name": "a", "notes": "", "events": [
        {"type": "click", "x": 125, "y": 60, "capture": 2, "delay_ms": 0},
        {"type": "wait_until", "delay_ms": 0, "cond": {"kind": "pixel", "x": 10, "y": 20, "w": 5, "h": 3,
                                                        "color": "#ff0000", "capture": 1}},
        {"type": "move", "x": 7, "y": 8, "delay_ms": 0}]}  # capture 없으면 매크로 좌표 그대로
    _, macro, _, _ = to_macro(data, req)
    down = macro.events[0]
    assert (down["x"], down["y"]) == (150, 70) and "capture" not in down
    cond = macro.events[2]["cond"]
    assert (cond["x"], cond["y"], cond["w"], cond["h"]) == (20, 40, 10, 6) and "capture" not in cond
    assert (macro.events[3]["x"], macro.events[3]["y"]) == (7, 8)


def test_image_crop_becomes_asset_and_is_reused():
    img = screen()
    req = GenRequest("x", captures=[Capture(img), Capture(img, origin=(10, 10))])
    crop = {"capture": 1, "rect": [100, 50, 50, 20]}  # 이미지 픽셀 (scale 0.5) -> 원본 [200,100,100,40]
    data = {"name": "a", "notes": "", "events": [
        {"type": "wait_until", "delay_ms": 0, "cond": {"kind": "image", "crop": dict(crop)}},
        {"type": "click_image", "delay_ms": 0, "button": "left",
         "cond": {"kind": "image", "crop": dict(crop), "region": [0, 0, 400, 200], "threshold": 0.9}},
        {"type": "if_start", "delay_ms": 0, "cond": {"kind": "all", "conds": [
            {"kind": "image", "crop": {"capture": 2, "rect": [0, 0, 10, 10]}}, {"kind": "var", "name": "v"}]}},
        {"type": "if_end", "delay_ms": 0}]}
    _, macro, _, assets = to_macro(data, req)
    c0, c1 = macro.events[0]["cond"], macro.events[1]["cond"]
    assert c0 == {"kind": "image", "template": "ai_1_1.png"}
    assert c1["template"] == "ai_1_1.png" and c1["region"] == [0, 0, 800, 400] and c1["threshold"] == 0.9
    assert macro.events[2]["cond"]["conds"][0]["template"] == "ai_2_2.png"
    assert set(assets) == {"ai_1_1.png", "ai_2_2.png"}
    cut = vision.decode_png(assets["ai_1_1.png"])
    assert cut.shape[:2] == (40, 100) and (cut == img[100:140, 200:300]).all()
    assert data["events"][0]["cond"]["crop"] == crop  # AI 답은 바꾸지 않는다


@pytest.mark.parametrize("cond, msg", [
    ({"kind": "image", "crop": {"capture": 3, "rect": [0, 0, 10, 10]}}, "화면 번호"),
    ({"kind": "image", "crop": {"capture": 1, "rect": [1560, 0, 20, 20]}}, "화면 밖"),
    ({"kind": "image", "crop": {"capture": 1, "rect": [0, 0, 3, 3]}}, "너무 작습니다"),
    ({"kind": "image", "crop": {"rect": [0, 0, 10, 10]}}, "capture 화면 번호"),
    ({"kind": "image", "template": "x.png"}, "crop"),
    ({"kind": "image", "crop": {"capture": 1, "rect": [0, 0, 10, 10]}, "negate": True}, "보이는"),
])
def test_image_crop_errors(cond, msg):
    req = GenRequest("x", captures=[Capture(screen())])
    with pytest.raises(MacroFormatError, match=msg):
        to_macro({"name": "a", "notes": "", "events": [{"type": "click_image", "delay_ms": 0, "cond": cond}]}, req)


def test_prompt_lists_captures():
    req = GenRequest("x", captures=[Capture(screen(), label="메뉴"), Capture(np.zeros((100, 200, 3), np.uint8))])
    system = ai_gen.build_system_prompt(req)
    assert "capture_1.png (이미지 1568x882) — 메뉴" in system and "capture_2.png (이미지 200x100)" in system
    assert "crop" in system and "Read" in system
    assert "잘라낼 수 없다" in ai_gen.build_system_prompt(GenRequest("x"))


def test_generate_sends_capture_pngs_and_limit():
    p = FakeProvider(GOOD)
    seen = []
    p.generate = lambda system, prompt, schema, images: seen.append(images) or GOOD
    generate_macro(GenRequest("a", captures=[Capture(screen())]), p)
    assert len(seen[0]) == 1 and seen[0][0].startswith(b"\x89PNG")
    many = [Capture(np.zeros((10, 10, 3), np.uint8))] * (ai_gen.MAX_CAPTURES + 1)
    with pytest.raises(AiError, match="장까지"):
        generate_macro(GenRequest("a", captures=many), p)


def test_cli_with_images_uses_read_tool_in_temp_dir():
    seen = {}

    def popen(cmd, **kw):
        from pathlib import Path
        d = Path(kw["cwd"])
        seen.update(cmd=cmd, cwd=d, files=sorted(f.name for f in d.iterdir()),
                    data=(d / "capture_2.png").read_bytes())
        return FakeProc(out=envelope(structured_output=GOOD))
    p = ClaudeCliProvider(popen=popen)
    assert p.generate("s", "p", {}, [b"one", b"two"]) == GOOD
    cmd = seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "Read" and cmd[cmd.index("--allowedTools") + 1] == "Read"
    assert seen["files"] == ["capture_1.png", "capture_2.png"] and seen["data"] == b"two"
    assert not seen["cwd"].exists()  # 끝나면 지운다


# ---- 수정 모드 (기존 매크로 고치기) ----
CURRENT = [{"t": 0.0, "type": "kdown", "key": "a"}, {"t": 0.05, "type": "kup", "key": "a"},
           {"t": 0.05, "type": "wait_until", "cond": {"kind": "image", "template": "btn.png"}},
           {"t": 0.5, "type": "set_image", "name": "first", "capture": [0, 0, 10, 10]}]


def test_from_events_round_trips_through_to_events():
    steps = ai_gen.from_events(CURRENT)
    assert steps[1] == {"type": "kup", "delay_ms": 50, "key": "a"}
    assert steps[3] == {"type": "set_image", "delay_ms": 450, "name": "first", "capture": [0, 0, 10, 10]}
    req = GenRequest("x", current=CURRENT, templates=("btn.png",))
    _, macro, _, assets = to_macro({"name": "", "notes": "", "events": steps}, req)
    assert macro.events == CURRENT and assets == {}


def test_edit_prompt_includes_current_templates_and_history():
    req = GenRequest("x", current=CURRENT, templates=("btn.png",),
                     history=[("user", "a 눌러"), ("ai", "만들었습니다")])
    system = ai_gen.build_system_prompt(req)
    assert "수정 모드" in system and '"key": "a"' in system and "btn.png" in system
    assert "- 사용자: a 눌러" in system and "- AI: 만들었습니다" in system


def test_edit_mode_rejects_unknown_template_and_new_set_image():
    req = GenRequest("x", current=CURRENT, templates=("btn.png",))
    bad = {"type": "wait_until", "delay_ms": 0, "cond": {"kind": "image", "template": "nope.png"}}
    with pytest.raises(MacroFormatError, match="첨부 화면이 없어"):
        to_macro({"name": "", "notes": "", "events": [bad]}, req)
    new_img = {"type": "set_image", "delay_ms": 0, "name": "v", "template": "nope.png"}
    with pytest.raises(MacroFormatError, match="지원하지 않는 type"):
        to_macro({"name": "", "notes": "", "events": [new_img]}, req)
    ok = {"type": "click_image", "delay_ms": 0, "cond": {"kind": "image", "image_var": "first"}}
    to_macro({"name": "", "notes": "", "events": [ok]}, req)


def test_new_crops_avoid_existing_names():
    req = GenRequest("x", captures=[Capture(screen())], templates=("ai_1_1.png",))
    data = {"name": "", "notes": "", "events": [
        {"type": "wait_until", "delay_ms": 0, "cond": {"kind": "image", "crop": {"capture": 1, "rect": [0, 0, 10, 10]}}}]}
    _, macro, _, assets = to_macro(data, req)
    assert list(assets) == ["ai_1_2.png"] and macro.events[0]["cond"]["template"] == "ai_1_2.png"
