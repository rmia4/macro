"""프로그램 설정(settings.json) 저장/불러오기. 파일이 없거나 깨져 있으면 기본값을 쓴다."""
from __future__ import annotations

import json
import os
from pathlib import Path

import keys
from paths import app_dir

SETTINGS_PATH = app_dir() / "settings.json"

EDITOR_PANELS = ("settings", "ai")
OVERLAY_POSITIONS = ("off", "nw", "n", "ne", "w", "e", "sw", "s", "se")

DEFAULTS = {
    "macros_enabled": True,        # 전체 매크로 실행 가능 여부
    "start_delay": 3,              # 버튼으로 시작할 때 지연(초)
    "toggle_hotkey": "ctrl+f12",   # 전체 실행 가능/불가 전환 단축키
    "overlay_position": "ne",      # 상태 오버레이 위치 (OVERLAY_POSITIONS) 또는 "off"
    "main_geometry": "",           # 창 크기/위치 ("760x520+100+80")
    "editor_geometry": "",
    "editor_panel": "settings",    # 기록 화면 오른쪽: "settings"(매크로 설정) | "ai"(AI 대화)
    "ai_cli_path": "",             # AI 로 만들기: Claude Code 실행 파일 (비면 PATH 의 claude)
    "ai_model": "",                # AI 로 만들기: 모델 (비면 Claude Code 기본 모델)
}


class Settings:
    def __init__(self, path: str | Path | None = None) -> None:
        """path 가 None 이면 저장하지 않는다 (테스트용)."""
        self.path = Path(path) if path else None
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key, default in DEFAULTS.items():
            value = raw.get(key)
            if isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, int):
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
                value = int(value) if ok else value
            else:
                ok = isinstance(value, type(default))
            if ok:
                self.data[key] = value
        try:
            self.data["toggle_hotkey"] = keys.parse_hotkey(self.data["toggle_hotkey"]) or DEFAULTS["toggle_hotkey"]
        except ValueError:
            self.data["toggle_hotkey"] = DEFAULTS["toggle_hotkey"]
        self.data["start_delay"] = min(max(self.data["start_delay"], 0), 30)
        if self.data["editor_panel"] not in EDITOR_PANELS:
            self.data["editor_panel"] = DEFAULTS["editor_panel"]
        if self.data["overlay_position"] not in OVERLAY_POSITIONS:
            self.data["overlay_position"] = DEFAULTS["overlay_position"]

    def save(self) -> bool:
        if not self.path:
            return False
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def __getitem__(self, key: str):
        return self.data[key]

    def __setitem__(self, key: str, value) -> None:
        self.data[key] = value
