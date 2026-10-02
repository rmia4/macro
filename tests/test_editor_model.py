import pytest

import editor_model as em
from profiles import Macro


def test_items_events_roundtrip():
    events = [{"t": 0.0, "type": "move", "x": 1, "y": 2}, {"t": 0.25, "type": "kdown", "key": "a"},
              {"t": 0.3, "type": "kup", "key": "a"}]
    items = em.to_items(events)
    assert [i["dt"] for i in items] == [0.0, 0.25, 0.05]
    assert "t" not in items[0]
    assert em.to_events(items) == events
    assert em.total_duration(items) == 0.3


def test_build_tap_and_click():
    tap = em.build_items("tap", delay_ms="200", key="E", hold_ms="")
    assert tap == [{"type": "kdown", "key": "e", "dt": 0.2}, {"type": "kup", "key": "e", "dt": 0.05}]
    click = em.build_items("click", delay_ms=0, button="right", x="-5", y="7", hold_ms=30)
    assert click[0] == {"type": "mdown", "x": -5, "y": 7, "button": "right", "dt": 0.0}
    assert click[1]["type"] == "mup" and click[1]["dt"] == 0.03


def test_build_wait_scroll_move():
    assert em.build_items("wait", delay_ms="1500") == [{"type": "wait", "dt": 1.5}]
    assert em.build_items("scroll", x=1, y=2, dx=0, dy=-3)[0]["dy"] == -3
    assert em.build_items("move", x=3, y=4)[0] == {"type": "move", "x": 3, "y": 4, "dt": 0.0}


@pytest.mark.parametrize("kind,kw", [
    ("tap", {"key": "nokey"}), ("kdown", {"key": ""}), ("wait", {"delay_ms": "-1"}),
    ("wait", {"delay_ms": "abc"}), ("click", {"x": "1.5"}), ("mdown", {"button": "foot"}),
    ("scroll", {"dx": 0, "dy": 0}), ("tap", {"key": "a", "hold_ms": "-3"}), ("nope", {}),
])
def test_build_invalid(kind, kw):
    with pytest.raises(ValueError):
        em.build_items(kind, **kw)


def test_built_items_produce_valid_macro():
    items = (em.build_items("tap", key="space") + em.build_items("click", x=1, y=1)
             + em.build_items("wait", delay_ms=500) + em.build_items("scroll", x=0, y=0, dy=1))
    Macro.from_dict({"version": 1, "events": em.to_events(items)})


def test_item_fields_and_describe():
    it = {"type": "mdown", "x": 3, "y": 4, "button": "left", "dt": 0.12}
    assert em.item_fields(it) == {"kind": "mdown", "delay_ms": 120, "x": 3, "y": 4, "button": "left",
                                  "at_cursor": False}
    assert em.describe(it) == "left (3, 4)"
    assert em.describe({"type": "wait", "dt": 1}) == ""


def test_unique_name():
    assert em.unique_name("새 매크로", []) == "새 매크로"
    assert em.unique_name("a", ["A", "a 2"]) == "a 3"


def test_key_choices_order():
    assert em.KEY_CHOICES[:3] == ["f1", "f2", "f3"]
    assert "f8" not in em.HOTKEY_CHOICES and "f7" in em.HOTKEY_CHOICES


def test_validate_for_save():
    lib = {"other": Macro(hotkey="f6"), "Mine": Macro(hotkey="f5")}
    items = em.build_items("tap", key="a")
    assert em.validate_for_save("Mine", "f5", items, lib, "Mine") == []
    errs = em.validate_for_save("other", "f6", [], lib, "Mine")
    assert any("이름" in e for e in errs) and any("F6" in e for e in errs) and any("이벤트가 없" in e for e in errs)
    assert any("제어 키" in e for e in em.validate_for_save("x", "f9", items, lib, None))
    assert any("핫키/제어 키" in e for e in em.validate_for_save("x", "a", items, lib, None))
    assert any("이름" in e for e in em.validate_for_save("../x", None, items, lib, None))
    assert em.validate_for_save("OTHER2", None, items, lib, None) == []
    # 조합 핫키
    assert em.validate_for_save("x", "Ctrl + F1", items, lib, None) == []
    assert any("제어 키" in e for e in em.validate_for_save("x", "ctrl+f9", items, lib, None))
    assert any("최대 2개" in e for e in em.validate_for_save("x", "ctrl+shift+f1", items, lib, None))
    lib["combo"] = Macro(hotkey="ctrl+f1")
    assert any("CTRL+F1" in e for e in em.validate_for_save("x", "f1+ctrl", items, lib, None))
    both = em.build_items("tap", key="ctrl") + em.build_items("tap", key="q")
    assert em.validate_for_save("y", "ctrl+f2", both, lib, None) == []    # ctrl 만 포함: 충돌 아님
    assert any("핫키/제어 키" in e for e in em.validate_for_save("y", "ctrl+q", both, lib, None))


def test_consecutive_moves_grouped_into_path():
    events = [{"t": 0.0, "type": "move", "x": 0, "y": 0}, {"t": 0.01, "type": "move", "x": 5, "y": 1},
              {"t": 0.03, "type": "move", "x": 9, "y": 2}, {"t": 0.1, "type": "mdown", "x": 9, "y": 2, "button": "left"},
              {"t": 0.2, "type": "move", "x": 20, "y": 2}, {"t": 0.3, "type": "kdown", "key": "a"}]
    items = em.to_items(events)
    assert [i["type"] for i in items] == ["path", "mdown", "move", "kdown"]  # 단독 이동은 그대로
    assert items[0]["points"] == [[0.0, 0, 0], [0.01, 5, 1], [0.02, 9, 2]]
    assert em.to_events(items) == events  # 재생용으로 원래 궤적 복원
    assert em.event_count(items) == 6 and em.total_duration(items) == 0.3
    assert em.describe(items[0]) == "(0, 0) → (9, 2) · 3개 지점 · 30ms"


def test_path_edit_reshapes_end_and_duration():
    item = {"type": "path", "dt": 0.1, "points": [[0.0, 0, 0], [0.01, 10, 0], [0.03, 20, 0]]}
    f = em.item_fields(item)
    assert (f["x"], f["y"], f["duration_ms"], f["delay_ms"]) == (20, 0, 40, 100)
    new = em.build_items("path", delay_ms=50, x=40, y=10, duration_ms=80, points=item["points"])[0]
    assert new["dt"] == 0.05
    assert new["points"] == [[0.0, 0, 0], [0.02, 20, 5], [0.06, 40, 10]]  # 시작점 고정, 비례 분배


def test_button_and_scroll_at_cursor():
    click = em.build_items("click", x=5, y=5, at_cursor=True)
    assert all("x" not in i for i in click) and em.describe(click[0]) == "left (현재 커서 위치)"
    assert em.item_fields(click[0])["at_cursor"] is True
    sc = em.build_items("scroll", dy=-1, at_cursor=True)[0]
    assert "x" not in sc
    assert not em.has_positional(click + [sc])
    Macro.from_dict({"version": 1, "events": em.to_events(click + [sc])})
    with pytest.raises(Exception):
        Macro.from_dict({"version": 1, "events": [{"t": 0, "type": "mdown", "x": 1, "button": "left"}]})
    with pytest.raises(Exception):
        Macro.from_dict({"version": 1, "events": [{"t": 0, "type": "move"}]})


def test_validate_reserved_toggle_hotkey():
    items = em.build_items("tap", key="a")
    assert any("전체 실행 전환" in e for e in em.validate_for_save("x", "Ctrl+F12", items, {}, None,
                                                                  reserved=("ctrl+f12",)))
    both = em.build_items("tap", key="ctrl") + em.build_items("tap", key="f12")
    assert any("ctrl+f12" in e for e in em.validate_for_save("x", None, both, {}, None, reserved=("ctrl+f12",)))


def test_relative_moves_grouped_and_rescaled():
    events = [{"t": 0.0, "type": "rmove", "dx": 3, "dy": 1}, {"t": 0.01, "type": "rmove", "dx": 3, "dy": 1},
              {"t": 0.03, "type": "rmove", "dx": 4, "dy": -1}, {"t": 0.05, "type": "mdown", "button": "left"}]
    items = em.to_items(events)
    assert [i["type"] for i in items] == ["relpath", "mdown"]
    assert em.describe(items[0]) == "총 이동 (10, 1) · 3회 · 30ms"
    assert em.to_events(items) == events
    assert not em.has_positional(items)
    f = em.item_fields(items[0])
    assert (f["duration_ms"], f["scale_pct"]) == (30, 100)
    new = em.build_items("relpath", delay_ms=0, duration_ms=60, scale_pct=150, points=items[0]["points"])[0]
    assert [p[0] for p in new["points"]] == [0.0, 0.02, 0.04]
    assert sum(p[1] for p in new["points"]) == 15 and sum(p[2] for p in new["points"]) == 2  # 총량 보존 반올림
    with pytest.raises(ValueError):
        em.build_items("relpath", scale_pct=0, duration_ms=10, points=items[0]["points"])


def test_build_rmove():
    assert em.build_items("rmove", delay_ms=10, dx="-30", dy="5") == [{"type": "rmove", "dx": -30, "dy": 5, "dt": 0.01}]
    with pytest.raises(ValueError):
        em.build_items("rmove", dx=0, dy=0)
    Macro.from_dict({"version": 1, "events": em.to_events(em.build_items("rmove", dx=1, dy=0))})
    with pytest.raises(Exception):
        Macro.from_dict({"version": 1, "events": [{"t": 0, "type": "rmove", "dx": 1}]})


def _keys(*names):
    out = []
    for n in names:
        out += em.build_items("tap", key=n, delay_ms=100)
    return out


def test_wrap_repeat_selection_and_empty():
    items = _keys("a", "b", "c")                    # 6 항목
    new, sel = em.wrap_repeat(items, [2, 3], 3)     # b 누름/뗌 감싸기
    assert [i["type"] for i in new] == ["kdown", "kup", "repeat_start", "kdown", "kup", "repeat_end", "kdown", "kup"]
    assert new[2] == {"type": "repeat_start", "count": 3, "dt": 0.1} and new[3]["dt"] == 0.0  # 앞 지연은 구간 앞으로
    assert sel == [2, 5]
    assert em.depths(new) == [0, 0, 0, 1, 1, 0, 0, 0]
    assert em.describe(new[2]) == "×3회 반복"
    empty, sel = em.wrap_repeat(items, [], 2)
    assert [i["type"] for i in empty[-2:]] == ["repeat_start", "repeat_end"] and sel == [6, 7]
    with pytest.raises(ValueError):
        em.wrap_repeat(new, [2, 3], 2)               # 시작만 포함하는 선택은 거부
    with pytest.raises(ValueError):
        em.wrap_repeat(items, [0], 0)


def test_expanded_duration_and_partner():
    items = _keys("a")                              # 0.1 + 0.05
    items, _ = em.wrap_repeat(items, [0, 1], 4)     # 구간: (0 + 0.05) + 끝 0 -> 0.05 x4, 앞 0.1
    assert em.total_duration(items) == 0.15
    assert em.expanded_duration(items) == 0.3
    outer, _ = em.wrap_repeat(items, [0, 3], 2)
    assert em.expanded_duration(outer) == 0.5  # 앞 지연 0.1 은 한 번만 + (0.2 x 2)
    assert em.block_partner(outer, 0) == 5 and em.block_partner(outer, 4) == 1 and em.block_partner(outer, 2) is None


def test_build_repeat_start_and_validation():
    assert em.build_items("repeat_start", delay_ms=0, count="7") == [{"type": "repeat_start", "count": 7, "dt": 0.0}]
    with pytest.raises(ValueError):
        em.build_items("repeat_start", count="0")
    assert em.item_fields({"type": "repeat_start", "count": 5, "dt": 0.2})["count"] == 5
    items = _keys("a") + [{"type": "repeat_end", "dt": 0}]
    assert any("반복 구간" in e for e in em.validate_for_save("x", None, items, {}, None))
    good, _ = em.wrap_repeat(_keys("a"), [0, 1], 2)
    assert em.validate_for_save("x", None, good, {}, None) == []
    Macro.from_dict({"version": 1, "events": em.to_events(good)})
