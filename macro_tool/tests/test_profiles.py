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
