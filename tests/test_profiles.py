import pytest

from profiles import Macro, MacroFormatError, list_macros, load_macro, macro_path, save_macro

EV = [{"t": 0, "type": "move", "x": 1, "y": 2},
      {"t": 0.1, "type": "mdown", "x": 1, "y": 2, "button": "left"},
      {"t": 0.2, "type": "mup", "x": 1, "y": 2, "button": "left"},
      {"t": 0.3, "type": "scroll", "x": 1, "y": 2, "dx": 0, "dy": -1},
      {"t": 0.4, "type": "kdown", "key": "a"}, {"t": 0.5, "type": "kup", "key": "a"}]


def test_roundtrip(tmp_path):
    m = Macro(events=EV, screen={"width": 1920, "height": 1080})
    p = save_macro(m, macro_path(tmp_path, "t1"))
    m2 = load_macro(p)
    assert m2.events == EV and m2.screen["width"] == 1920 and m2.duration == 0.5
    assert list_macros(tmp_path) == ["t1"]


@pytest.mark.parametrize("bad", [
    {"version": 99, "events": []},
    {"version": 1},
    {"version": 1, "events": [{"t": 0, "type": "nope"}]},
    {"version": 1, "events": [{"t": 1, "type": "kdown", "key": "a"}, {"t": 0.5, "type": "kup", "key": "a"}]},
    {"version": 1, "events": [{"t": 0, "type": "kdown", "key": "zzz"}]},
    {"version": 1, "events": [{"t": 0, "type": "mdown", "x": 0, "y": 0, "button": "foot"}]},
    {"version": 1, "events": [], "coord_space": "moon"},
])
def test_invalid(bad):
    with pytest.raises(MacroFormatError):
        Macro.from_dict(bad)


@pytest.mark.parametrize("name", ["../x", "a/b", "", "..", "a\\b"])
def test_bad_names(tmp_path, name):
    with pytest.raises(ValueError):
        macro_path(tmp_path, name)


def test_hotkey_normalized_on_load():
    m = Macro.from_dict({"version": 1, "events": [], "hotkey": "F1+Ctrl"})
    assert m.hotkey == "ctrl+f1"
    with pytest.raises(MacroFormatError):
        Macro.from_dict({"version": 1, "events": [], "hotkey": "ctrl+shift+f1"})
    # 이전 버전 파일의 enabled 키는 무시
    assert Macro.from_dict({"version": 1, "events": [], "enabled": False}).hotkey is None



@pytest.mark.parametrize("events", [
    [{"t": 0, "type": "repeat_start", "count": 2}],
    [{"t": 0, "type": "repeat_end"}],
    [{"t": 0, "type": "repeat_end"}, {"t": 0, "type": "repeat_start", "count": 2}],
    [{"t": 0, "type": "repeat_start", "count": 0}, {"t": 0, "type": "repeat_end"}],
    [{"t": 0, "type": "repeat_start", "count": "3"}, {"t": 0, "type": "repeat_end"}],
])
def test_repeat_blocks_validated(events):
    with pytest.raises(MacroFormatError):
        Macro.from_dict({"version": 1, "events": events})


def test_block_pairs_nested():
    from profiles import block_pairs
    evs = [{"type": "repeat_start"}, {"type": "repeat_start"}, {"type": "repeat_end"}, {"type": "repeat_end"}]
    assert block_pairs(evs) == {1: 2, 0: 3}



def test_wait_until_validation():
    ok = {"t": 0, "type": "wait_until", "cond": {"kind": "pixel", "x": 1, "y": 2, "color": "#ffffff"},
          "timeout": 5, "on_timeout": "continue"}
    Macro.from_dict({"version": 1, "events": [ok]})
    for bad in (dict(ok, cond={"kind": "x"}), dict(ok, timeout=-1), dict(ok, on_timeout="skip"),
                dict(ok, interval=0)):
        with pytest.raises(MacroFormatError):
            Macro.from_dict({"version": 1, "events": [bad]})
