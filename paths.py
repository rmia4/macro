"""데이터 파일 위치. PyInstaller 로 만든 .exe 는 실행 중 임시 폴더에 풀리므로,
매크로·설정은 소스 폴더가 아니라 exe 가 있는 폴더에 저장한다."""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """소스 실행과 PyInstaller 배포에서 공용 리소스 위치."""
    return Path(__file__).resolve().parent / name
