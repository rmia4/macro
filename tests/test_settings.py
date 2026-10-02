import json

from settings import DEFAULTS, Settings


def test_defaults_without_file(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert s.data == DEFAULTS
    assert Settings(None).save() is False


def test_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    s["macros_enabled"] = False
    s["start_delay"] = 5
    s["toggle_hotkey"] = "ctrl+f11"
    assert s.save()
    s2 = Settings(path)
    assert (s2["macros_enabled"], s2["start_delay"], s2["toggle_hotkey"]) == (False, 5, "ctrl+f11")


def test_broken_or_invalid_values_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert Settings(path).data == DEFAULTS
    path.write_text(json.dumps({"macros_enabled": "yes", "start_delay": 99, "toggle_hotkey": "ctrl+nokey",
                                "main_geometry": 5, "unknown": 1}), encoding="utf-8")
    s = Settings(path)
    assert s["macros_enabled"] is True and s["start_delay"] == 30
    assert s["toggle_hotkey"] == DEFAULTS["toggle_hotkey"] and s["main_geometry"] == ""
    path.write_text(json.dumps({"start_delay": True}), encoding="utf-8")  # bool 은 숫자로 취급 안 함
    assert Settings(path)["start_delay"] == DEFAULTS["start_delay"]


def test_overlay_position_validated(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"overlay_position": "sw"}), encoding="utf-8")
    assert Settings(path)["overlay_position"] == "sw"
    path.write_text(json.dumps({"overlay_position": "middle"}), encoding="utf-8")
    assert Settings(path)["overlay_position"] == DEFAULTS["overlay_position"]
