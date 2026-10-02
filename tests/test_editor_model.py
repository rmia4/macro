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
    assert em.item_fields(it) == {"kind": "mdown", "delay_ms": 120, "x": 3, "y": 4, "button": "left"}
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
