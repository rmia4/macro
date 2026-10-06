"""변수 · 동안 반복 · 변수/여러 조건."""
import pytest

import editor_model as em
import vision
from profiles import Macro, MacroFormatError, blocks
from test_player import PIX, ScriptedVision, _kd, build

VAR = lambda name, neg=False: {"kind": "var", "name": name, **({"negate": True} if neg else {})}  # noqa: E731


def ev(t, typ, **kw):
    return {"t": t, "type": typ, **kw}


def keys_pressed(be):
    return "".join(c[2] for c in be.calls if c[1] == "kdown")


# ---- 검증 ----
def test_validate_var_and_group_conditions():
    vision.validate_condition(VAR("보스_1"))
    vision.validate_condition({"kind": "all", "conds": [PIX, VAR("a")], "negate": True})
    for bad in ({"kind": "var", "name": ""}, {"kind": "var", "name": "a b"}, {"kind": "var", "name": "x" * 21},
                {"kind": "any", "conds": [PIX]}, {"kind": "any", "conds": "x"},
                {"kind": "all", "conds": [PIX, {"kind": "any", "conds": [PIX, PIX]}]}):
        with pytest.raises(ValueError):
            vision.validate_condition(bad)


def test_macro_with_set_var_and_while_validates():
    evs = [ev(0, "set_var", name="a", cond=PIX), ev(0, "while_start", cond=VAR("a")), *_kd(0.1, "x"),
           ev(0.2, "while_end")]
    Macro.from_dict({"version": 1, "events": evs})
    with pytest.raises(MacroFormatError):
        Macro.from_dict({"version": 1, "events": [ev(0, "set_var", name="a b", cond=PIX)]})
    with pytest.raises(MacroFormatError):  # 동안 반복과 반복 구간이 엇갈림
        Macro.from_dict({"version": 1, "events": [ev(0, "while_start", cond=PIX), ev(0, "repeat_start", count=2),
                                                  ev(0, "while_end"), ev(0, "repeat_end")]})


def test_blocks_while_and_parent_loop():
    evs = [{"type": "repeat_start"}, {"type": "while_start", "cond": PIX}, {"type": "break_if", "cond": PIX},
           {"type": "while_end"}, {"type": "repeat_end"}]
    b = blocks(evs)
    assert b.while_end == {1: 3} and b.repeat == {0: 4}
    assert b.parent_loop[2] == 1 and b.parent_loop[1] == 0 and b.parent_loop[3] == 0 and 4 not in b.parent_loop
    assert b.loops[2] == (0, 1) and b.loops[0] == ()


# ---- 판정 ----
def test_evaluate_short_circuit_and_negate():
    seen = []

    def leaf(c):
        seen.append(c["x"])
        return vision.Match(c["x"] == 1, 0.5)

    a, b = dict(PIX, x=1), dict(PIX, x=2)
    assert vision.evaluate({"kind": "any", "conds": [a, b]}, leaf, {}).matched and seen == [1]
    seen.clear()
    assert not vision.evaluate({"kind": "all", "conds": [b, a]}, leaf, {}).matched and seen == [2]
    assert vision.evaluate({"kind": "all", "conds": [b, a], "negate": True}, leaf, {}).matched
    assert vision.evaluate(VAR("v"), leaf, {"v": True}).matched
    assert not vision.evaluate(VAR("v"), leaf, {}).matched
    assert vision.evaluate(VAR("v", neg=True), leaf, {}).matched


def test_conditions_and_templates_look_inside_groups():
    img = {"kind": "image", "template": "a.png"}
    evs = [{"type": "if_start", "cond": {"kind": "all", "conds": [img, VAR("v")]}},
           {"type": "set_var", "name": "w", "cond": VAR("v")}]
    assert vision.conditions_in(evs) == [img]
    assert vision.templates_in(evs) == {"a.png"}
    assert vision.variables_used(evs) == {"v"}
    assert vision.describe_condition({"kind": "any", "conds": [PIX, VAR("v")]}).endswith("또는 [변수 'v']")


# ---- 재생 ----
def test_set_var_is_evaluated_each_time_it_is_used():
    # 변수 저장은 판정하지 않고, 변수가 쓰일 때마다 지정된 조건을 판정한다
    evs = [ev(0, "set_var", name="hp", cond=PIX), ev(0, "if_start", cond=VAR("hp")), *_kd(0.1, "a"),
           ev(0.2, "else"), *_kd(0.3, "b"), ev(0.4, "if_end"), ev(0.5, "if_start", cond=VAR("hp", neg=True)),
           *_kd(0.6, "c"), ev(0.7, "if_end")]
    p, be, clock, m = build(evs)
    v = ScriptedVision([True, False])
    p.vision = v
    p.run(m)
    assert keys_pressed(be) == "ac" and len(v.checks) == 2  # 쓰인 두 번 모두 화면을 본다


def test_evaluate_var_definition_and_self_reference():
    leaf = lambda c: vision.Match(c["x"] == 1, 0.5, (3, 4))  # noqa: E731
    defs = {"a": dict(PIX, x=1), "b": VAR("a", neg=True), "loop": VAR("loop")}
    m = vision.evaluate(VAR("a"), leaf, defs)
    assert m.matched and m.pos == (3, 4)
    assert not vision.evaluate(VAR("b"), leaf, defs).matched
    assert not vision.evaluate(VAR("loop"), leaf, defs).matched  # 자기 참조는 거짓


def test_variable_only_macro_runs_without_vision():
    evs = [ev(0, "if_start", cond=VAR("x", neg=True)), *_kd(0.1, "a"), ev(0.2, "if_end")]
    p, be, clock, m = build(evs)
    p.run(m)  # vision 없음 (set_var 는 없지만 재생은 막지 않는다; 저장 검증에서 걸러진다)
    assert keys_pressed(be) == "a"


def test_while_repeats_until_condition_false():
    evs = [ev(0, "while_start", cond=PIX), *_kd(0.1, "a"), ev(0.2, "while_end"), *_kd(0.3, "z")]
    p, be, clock, m = build(evs)
    p.vision = ScriptedVision([True, True, True, False])
    p.run(m)
    assert keys_pressed(be) == "aaaz"


def test_break_if_inside_while_and_if():
    evs = [ev(0, "while_start", cond=PIX), *_kd(0.1, "a"), ev(0.2, "if_start", cond=PIX),
           ev(0.2, "break_if", cond=PIX), ev(0.3, "if_end"), ev(0.4, "while_end"), *_kd(0.5, "z")]
    p, be, clock, m = build(evs)
    # while 참, if 거짓 / while 참, if 참, break 참
    p.vision = ScriptedVision([True, False, True, True, True])
    p.run(m)
    assert keys_pressed(be) == "aaz"


def test_zero_delay_while_still_advances_time():
    evs = [ev(0, "while_start", cond=PIX), ev(0, "while_end"), *_kd(0, "z")]
    p, be, clock, m = build(evs)
    p.vision = ScriptedVision([True] * 50 + [False])
    p.run(m)
    assert keys_pressed(be) == "z" and clock() >= 0.49


def test_variable_not_judged_until_used():
    # 저장 위치가 아니라 쓰이는 위치에서 판정: 반복 회차마다 새로 판정된다
    evs = [ev(0, "set_var", name="go", cond=PIX), ev(0, "repeat_start", count=3),
           ev(0.1, "if_start", cond=VAR("go")), *_kd(0.1, "a"), ev(0.2, "else"), *_kd(0.2, "b"),
           ev(0.3, "if_end"), ev(0.3, "repeat_end")]
    p, be, clock, m = build(evs)
    p.vision = ScriptedVision([True, False, True])
    p.run(m)
    assert keys_pressed(be) == "aba"


def test_variable_used_before_set_is_false_each_playback_round():
    evs = [ev(0, "if_start", cond=VAR("done", neg=True)), *_kd(0.1, "a"), ev(0.2, "if_end"),
           ev(0.3, "set_var", name="done", cond=PIX)]
    p, be, clock, m = build(evs, repeat=2)
    p.vision = ScriptedVision([])
    p.run(m)
    assert keys_pressed(be) == "aa"  # 재생 회차마다 비워진다 (지정 전엔 거짓)


def test_while_variable_rejudged_every_lap():
    # 동안 반복 조건의 변수는 바퀴마다 다시 판정된다 (안에서 다시 저장할 필요 없음)
    evs = [ev(0, "set_var", name="go", cond=PIX), ev(0, "while_start", cond=VAR("go")), *_kd(0.1, "a"),
           ev(0.3, "while_end"), *_kd(0.4, "z")]
    p, be, clock, m = build(evs)
    p.vision = ScriptedVision([True, True, False])
    p.run(m)
    assert keys_pressed(be) == "aaz"


# ---- 편집 모델 ----
def test_editor_model_while_and_set_var():
    items = em.build_items("tap", key="a")
    items, sel = em.wrap_while(items, [0, 1], PIX)
    assert [i["type"] for i in items] == ["while_start", "kdown", "kup", "while_end"] and sel == [0, 3]
    assert em.depths(items) == [0, 1, 1, 0]
    assert em.block_members(items, 3) == {0, 3} and em.block_partner(items, 0) == 3
    sv = em.build_set_var(target="hp", delay_ms="5", kind="pixel", x="1", y="2")
    assert em.build_set_var(target="b", kind="var", name="hp")["cond"] == VAR("hp")
    assert sv["type"] == "set_var" and sv["name"] == "hp" and sv["dt"] == 0.005
    items = [sv] + items
    assert em.describe(sv).startswith("hp = [색 (1, 2)")
    assert em.describe(items[1]).endswith("인 동안")
    assert em.variables_in(items) == ["hp"]
    assert em.variable_defs(items) == {"hp": sv["cond"]}
    with pytest.raises(ValueError):
        em.build_set_var(target="a b", kind="pixel")
    g = em.build_condition(kind="any", conds=[PIX, VAR("hp")], negate=True)
    assert g == {"kind": "any", "conds": [PIX, VAR("hp")], "negate": True}
    with pytest.raises(ValueError):
        em.build_condition(kind="all", conds=[PIX])
    assert not em.has_positional([{"type": "if_start", "cond": VAR("hp"), "dt": 0}])


def test_validate_for_save_reports_undefined_variable():
    items = em.build_items("tap", key="a")
    items, _ = em.wrap_if(items, [0, 1], VAR("ghost"))
    errors = em.validate_for_save("m", None, items, {}, None)
    assert any("ghost" in e for e in errors)
    items = [em.build_set_var(target="ghost", kind="pixel")] + items
    assert em.validate_for_save("m", None, items, {}, None) == []


def test_build_loop_and_replace_loop_start():
    rep = em.build_loop(count="3", delay_ms="10")
    assert rep == {"type": "repeat_start", "count": 3, "dt": 0.01}
    with pytest.raises(ValueError):
        em.build_loop(count="x")
    wh = em.build_loop(loop_kind="cond", kind="var", name="a")
    assert wh["type"] == "while_start" and wh["cond"] == VAR("a")
    items, _ = em.wrap_repeat(em.build_items("tap", key="a"), [0, 1], 2)
    items[-1]["dt"] = 0.5
    out = em.replace_loop_start(items, 0, wh)
    assert [i["type"] for i in out] == ["while_start", "kdown", "kup", "while_end"] and out[-1]["dt"] == 0.5
    assert items[-1]["type"] == "repeat_end"                     # 원본은 그대로
    same = em.replace_loop_start(items, 0, em.build_loop(count="7"))
    assert same[0]["count"] == 7 and same[-1]["type"] == "repeat_end"
    assert em.uses_screen({"kind": "any", "conds": [VAR("a"), PIX]}) and not em.uses_screen(VAR("a"))
