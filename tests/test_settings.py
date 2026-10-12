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


def test_ai_provider_validated(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ai_provider": "anthropic", "ai_anthropic_key": "k"}), encoding="utf-8")
    s = Settings(path)
    assert (s["ai_provider"], s["ai_anthropic_key"]) == ("anthropic", "k")
    path.write_text(json.dumps({"ai_provider": "openai"}), encoding="utf-8")
    assert Settings(path)["ai_provider"] == DEFAULTS["ai_provider"] == "gemini"


def test_overlay_position_validated(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"overlay_position": "sw"}), encoding="utf-8")
    assert Settings(path)["overlay_position"] == "sw"
    path.write_text(json.dumps({"overlay_position": "middle"}), encoding="utf-8")
    assert Settings(path)["overlay_position"] == DEFAULTS["overlay_position"]


def test_control_hotkeys_validated(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"record_hotkey": "F2", "play_hotkey": "f11"}), encoding="utf-8")
    s = Settings(path)
    assert (s["record_hotkey"], s["play_hotkey"]) == ("f2", "f11")
    for bad in ({"record_hotkey": "f2", "play_hotkey": "f2"}, {"record_hotkey": "ctrl+f2"},
                {"play_hotkey": "f10"}, {"play_hotkey": "shift"}, {"record_hotkey": "nokey"}):
        path.write_text(json.dumps(bad), encoding="utf-8")
        s = Settings(path)
        assert (s["record_hotkey"], s["play_hotkey"]) == ("f8", "f9"), bad


def test_numbers_clamped_and_control_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"undo_limit": 5, "flash_seconds": 3, "key_hold_ms": 9999.7, "ai_max_captures": 50,
                                "quit_hotkey": "F4", "capture_hotkey": "f3"}), encoding="utf-8")
    s = Settings(path)
    assert s["undo_limit"] == 10 and s["flash_seconds"] == 3.0 and isinstance(s["flash_seconds"], float)
    assert s["key_hold_ms"] == 5000 and s["ai_max_captures"] == 20
    assert (s["quit_hotkey"], s["capture_hotkey"]) == ("f4", "f3")
    path.write_text(json.dumps({"capture_hotkey": "f8", "play_hotkey": "f2"}), encoding="utf-8")  # 녹화 키와 같음
    s = Settings(path)
    assert (s["capture_hotkey"], s["record_hotkey"], s["play_hotkey"]) == ("f7", "f8", "f9")
