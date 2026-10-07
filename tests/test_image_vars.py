"""이미지 변수: 잘라낸 이미지에 이름 붙이기 · 재생 중 화면 캡처."""
import numpy as np
import pytest

import editor_model as em
import vision
from profiles import Macro, MacroFormatError
from test_flow import ev, keys_pressed
from test_player import _kd, build
from test_vision import FakeGrabber, make_screen

IMG = lambda name, **kw: {"kind": "image", "image_var": name, **kw}  # noqa: E731


@pytest.fixture
def setup(tmp_path):
    screen, btn = make_screen()
    vision.save_png(btn, tmp_path / "btn.png")
    grabber = FakeGrabber(screen)
    return vision.Vision(tmp_path, grabber), grabber, screen, btn


# ---- 검증 ----
def test_validate_image_var_condition_and_event():
    vision.validate_condition(IMG("버튼"))
    for bad in (IMG("a b"), {"kind": "image"}, {"kind": "image", "template": "a.png", "image_var": "a"}):
        with pytest.raises(ValueError):
            vision.validate_condition(bad)
    Macro.from_dict({"version": 1, "events": [ev(0, "set_image", name="a", template="a.png"),
                                              ev(0, "set_image", name="b", capture=[0, 0, 5, 5]),
                                              ev(0, "wait_until", cond=IMG("a"))]})
    for bad in (dict(name="a"), dict(name="a", template="a.png", capture=[0, 0, 1, 1]),
                dict(name="a", capture=[0, 0, 0, 1]), dict(name="a b", template="a.png"),
                dict(name="a", template="../a.png")):
        with pytest.raises(MacroFormatError):
            Macro.from_dict({"version": 1, "events": [ev(0, "set_image", **bad)]})


def test_describe_and_helpers():
    items = [{"type": "set_image", "dt": 0, "name": "a", "template": "a.png"},
             {"type": "set_image", "dt": 0, "name": "b", "capture": [1, 2, 3, 4]},
             {"type": "wait_until", "dt": 0, "cond": IMG("a")}]
    assert em.describe(items[0]) == "a = 이미지 'a.png'"
    assert em.describe(items[1]) == "b = 재생 중 화면 캡처 [1, 2, 3, 4]"
    assert "이미지 변수 'a'" in em.describe(items[2])
    assert vision.templates_in(items) == {"a.png"} and vision.image_vars_used(items) == {"a"}
    assert em.image_variables_in(items) == ["a", "b"] and em.image_defs(items) == {"a": "a.png"}
    assert em.has_positional(items[1:2]) and vision.needs_vision(items[:1])
    # 같은 이름을 캡처로 다시 지정하면 편집 화면에서는 알 수 없다
    assert em.image_defs(items + [dict(items[1], name="a")]) == {}


def test_build_set_image_and_condition():
    it = em.build_set_image(target="버튼", source="file", template="a.png", delay_ms="100")
    assert it == {"type": "set_image", "dt": 0.1, "name": "버튼", "template": "a.png"}
    it = em.build_set_image(target="c", source="capture", x="1", y="2", w="3", h="4")
    assert it["capture"] == [1, 2, 3, 4] and "template" not in it
    with pytest.raises(ValueError):
        em.build_set_image(target="c", source="file")              # 이미지 없음
    with pytest.raises(ValueError):
        em.build_set_image(target="c", source="capture", w="0")
    cond = em.build_condition(kind="image", image_var="버튼", template="ignored.png", region=["0", "0", "9", "9"])
    assert cond == {"kind": "image", "image_var": "버튼", "threshold": 0.85, "region": [0, 0, 9, 9]}
    ck = em.build_click_image(kind="image", image_var="버튼")
    assert ck["cond"]["image_var"] == "버튼"


def test_validate_for_save_reports_undefined_image_var():
    items = [{"type": "wait_until", "dt": 0, "cond": IMG("x")}]
    errors = em.validate_for_save("m", None, items, {}, None)
    assert any("이미지 변수" in e and "x" in e for e in errors)
    items.insert(0, {"type": "set_image", "dt": 0, "name": "x", "template": "x.png"})
    assert em.validate_for_save("m", None, items, {}, None, available_templates={"x.png"}) == []
    assert em.validate_for_save("m", None, items, {}, None, available_templates=set())   # 파일 없음


# ---- 판정 ----
def test_image_var_check(setup):
    v, g, screen, btn = setup
    assert not v.check(IMG("b")).matched                 # 지정 전엔 거짓
    assert v.check(IMG("b", negate=True)).matched
    v.set_image("b", v.template("btn.png"))
    m = v.check(IMG("b"))
    assert m.matched and m.pos == (325, 215)
    assert v.capture_image("c", [300, 200, 50, 30])     # 같은 버튼을 화면에서 캡처
    assert v.check(IMG("c", region=[290, 190, 70, 50])).matched
    v.clear_images()
    assert not v.check(IMG("b")).matched
    assert not v.capture_image("d", [1000, 1000, 5, 5]) and "d" not in v.images   # 화면 밖


def test_image_var_cache_is_dropped_when_image_changes(setup):
    v, g, screen, btn = setup
    cond = IMG("c", region=[280, 180, 100, 70])
    v.capture_image("c", [300, 200, 50, 30])
    assert v.check(cond).matched
    old = set(v._last_full)
    v.capture_image("c", [0, 0, 50, 30])                # 다른 곳(잡음)을 캡처 -> 그 버튼 영역엔 없다
    assert not (old & set(v._last_full))
    assert not v.check(cond).matched
    v.capture_image("c", [300, 200, 50, 30])
    assert v.check(cond).matched


# ---- 재생 ----
def test_capture_then_break_when_screen_changes(setup):
    v, g, screen, btn = setup
    changed = screen.copy()
    changed[200:230, 300:350] = 0
    p, be, clock, m = build([
        ev(0, "set_image", name="처음", capture=[300, 200, 50, 30]),
        ev(0, "repeat_start", count=0),
        ev(0.1, "break_if", cond=IMG("처음", region=[300, 200, 50, 30], negate=True)),
        *_kd(0.1, "a"),
        ev(0.2, "repeat_end"),
        *_kd(0.3, "z")])
    orig_grab = g.grab
    # 'a' 를 세 번 누른 뒤 화면이 바뀐다
    g.grab = lambda x, y, w, h: (changed if keys_pressed(be).count("a") >= 3 else screen)[y:y + h, x:x + w].copy()
    p.vision = v
    p.run(m)
    g.grab = orig_grab
    assert keys_pressed(be) == "aaaz"


def test_named_file_image_used_by_click_and_cleared_each_round(setup):
    v, g, screen, btn = setup
    evs = [ev(0, "if_start", cond=IMG("btn", negate=True)), *_kd(0.1, "n"), ev(0.1, "if_end"),
           ev(0.2, "set_image", name="btn", template="btn.png"),
           ev(0.3, "click_image", cond=IMG("btn"), button="left")]
    p, be, clock, m = build(evs, repeat=2)
    p.vision = v
    p.run(m)
    assert keys_pressed(be) == "nn"                     # 회차마다 지정이 비워진다
    downs = [c for c in be.calls if c[1] == "mdown"]
    assert len(downs) == 2


def test_missing_named_file_fails_before_input(setup, tmp_path):
    v, g, screen, btn = setup
    p, be, clock, m = build([*_kd(0, "a"), ev(0.1, "set_image", name="x", template="없음.png")])
    p.vision = v
    with pytest.raises(FileNotFoundError):
        p.run(m)
    assert not be.calls or keys_pressed(be) == ""
