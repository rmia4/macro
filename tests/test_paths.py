import sys
from pathlib import Path

import paths


def test_app_dir_source_and_frozen(monkeypatch, tmp_path):
    assert paths.app_dir() == Path(paths.__file__).resolve().parent
    assert not paths.is_frozen()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "MacroTool.exe"))
    assert paths.is_frozen() and paths.app_dir() == tmp_path.resolve()   # exe 옆에 데이터 저장
