import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def _reset_runtime_settings():
    """설정 창이 바꾸는 모듈 값(단축키·이동 녹화 간격·AI 캡처 장수)을 테스트마다 기본값으로 되돌린다."""
    yield
    import ai_gen
    import hotkeys as hk
    import recorder
    hk.set_control_keys(hk.DEFAULT_RECORD, hk.DEFAULT_PLAY, hk.DEFAULT_QUIT, hk.DEFAULT_CAPTURE)
    recorder.MOVE_INTERVAL = 0.010
    ai_gen.MAX_CAPTURES = 8
