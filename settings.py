"""프로그램 설정(settings.json) 저장/불러오기. 파일이 없거나 깨져 있으면 기본값을 쓴다."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import hotkeys
import keys
from paths import app_dir

SETTINGS_PATH = app_dir() / "settings.json"

EDITOR_PANELS = ("settings", "ai")
OVERLAY_POSITIONS = ("off", "nw", "n", "ne", "w", "e", "sw", "s", "se")
AI_PROVIDERS = ("gemini", "anthropic", "claude_cli")

DEFAULTS = {
    "macros_enabled": True,        # True=실행모드, False=편집모드
    "start_delay": 3,              # 버튼으로 시작할 때 지연(초)
    "toggle_hotkey": "ctrl+f12",   # 실행모드/편집모드 전환 단축키
    "capture_hotkey": hotkeys.DEFAULT_CAPTURE,  # AI 화면 캡처 키 (한 키)
    "record_hotkey": hotkeys.DEFAULT_RECORD,    # 녹화 시작/종료 키 (한 키)
    "play_hotkey": hotkeys.DEFAULT_PLAY,        # 선택한 매크로 재생/중지 키 (한 키)
    "quit_hotkey": hotkeys.DEFAULT_QUIT,        # 프로그램 종료 키 (한 키)
    # ---- 고급: 직접 추가할 때 처음 채워지는 값 (녹화·저장된 항목에는 영향 없음) ----
    "event_delay_ms": 100,         # 이벤트 앞 지연
    "key_hold_ms": 50,             # 키 입력 누름 유지
    "click_hold_ms": 60,           # 클릭·이미지 클릭 누름 유지
    "relpath_scale_pct": 100,      # 상대 이동(이동 녹화) 배율
    "default_threshold": 85,       # 이미지 일치도(%)
    "default_tolerance": 20,       # 범위 색 허용 오차
    "cond_timeout_s": 10,          # 조건 최대 대기(초, 0=무제한)
    "cond_interval_ms": 100,       # 조건 확인 간격
    # ---- 고급: 동작 ----
    "move_interval_ms": 10,        # 마우스 이동 녹화 간격 (recorder.MOVE_INTERVAL)
    "undo_limit": 100,             # 기록 화면 되돌리기 최대 개수
    "flash_seconds": 2.0,          # 모드 전환·오버레이 위치 확인 표시 시간(초)
    "ai_max_captures": 8,          # AI 화면 캡처 최대 장수 (ai_gen.MAX_CAPTURES)
    "overlay_position": "ne",      # 상태 오버레이 위치 (OVERLAY_POSITIONS) 또는 "off"
    "main_geometry": "",           # 창 크기/위치 ("760x520+100+80")
    "editor_geometry": "",
    "editor_panel": "settings",    # 기록 화면 오른쪽: "settings"(매크로 설정) | "ai"(AI 대화)
    "ai_provider": "gemini",       # AI 제공자: gemini | anthropic | claude_cli (AI_PROVIDERS)
    "ai_gemini_key": "",           # Gemini API 키 (AI Studio, 무료 등급 가능)
    "ai_gemini_model": "",         # 비면 ai_gen.GeminiProvider.default_model
    "ai_anthropic_key": "",        # Claude API 키 (Anthropic Console)
    "ai_anthropic_model": "",      # 비면 ai_gen.AnthropicProvider.default_model
    "ai_cli_path": "",             # Claude Code 실행 파일 (비면 PATH 의 claude)
    "ai_model": "",                # Claude Code 모델 (비면 Claude Code 기본 모델)
}

# 숫자 설정의 범위 (불러올 때·설정 창에서 이 안으로)
RANGES = {
    "start_delay": (0, 30),
    "event_delay_ms": (0, 60000), "key_hold_ms": (0, 5000), "click_hold_ms": (0, 5000),
    "relpath_scale_pct": (1, 1000), "default_threshold": (50, 100), "default_tolerance": (0, 255),
    "cond_timeout_s": (0, 3600), "cond_interval_ms": (10, 5000),
    "move_interval_ms": (1, 100), "undo_limit": (10, 1000), "flash_seconds": (0, 10), "ai_max_captures": (1, 20),
}
CONTROL_SETTINGS = ("record_hotkey", "play_hotkey", "quit_hotkey", "capture_hotkey")


def clamp(key: str, value):
    """숫자 설정을 범위 안으로, 기본값과 같은 형(int/float)으로."""
    lo, hi = RANGES[key]
    if not math.isfinite(value):  # nan·무한대는 기본값으로
        return DEFAULTS[key]
    return type(DEFAULTS[key])(min(max(value, lo), hi))


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
            elif isinstance(default, (int, float)):
                ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                value = type(default)(value) if ok else value
            else:
                ok = isinstance(value, type(default))
            if ok:
                self.data[key] = value
        try:
            self.data["toggle_hotkey"] = keys.parse_hotkey(self.data["toggle_hotkey"]) or DEFAULTS["toggle_hotkey"]
        except ValueError:
            self.data["toggle_hotkey"] = DEFAULTS["toggle_hotkey"]
        controls = [self.data[k] for k in CONTROL_SETTINGS]
        if hotkeys.control_key_error(*controls):  # 하나라도 문제면 넷 다 기본값
            self.data.update({k: DEFAULTS[k] for k in CONTROL_SETTINGS})
        else:
            self.data.update({k: keys.parse_hotkey(v) for k, v in zip(CONTROL_SETTINGS, controls)})
        for key in RANGES:
            self.data[key] = clamp(key, self.data[key])
        if self.data["editor_panel"] not in EDITOR_PANELS:
            self.data["editor_panel"] = DEFAULTS["editor_panel"]
        if self.data["overlay_position"] not in OVERLAY_POSITIONS:
            self.data["overlay_position"] = DEFAULTS["overlay_position"]
        if self.data["ai_provider"] not in AI_PROVIDERS:
            self.data["ai_provider"] = DEFAULTS["ai_provider"]

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
