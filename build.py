"""Windows 실행 파일(dist/MacroTool.exe) 빌드.

    pip install -r requirements.txt pyinstaller
    python build.py

결과 exe 하나만 있으면 실행된다. 매크로(macros/)와 설정(settings.json)은 exe 와 같은 폴더에 생긴다.
"""
import sys

import PyInstaller.__main__

# Windows 콘솔(cp1252 등)에서도 한글 메시지가 깨지거나 오류 나지 않게
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

NAME = "MacroTool"

if sys.platform != "win32":
    sys.exit("Windows 에서만 빌드할 수 있습니다 (PyInstaller 는 다른 OS 용 실행 파일을 만들지 못함).")

PyInstaller.__main__.run([
    "main.py",
    "--name", NAME,
    "--onefile",
    "--noconsole",
    "--clean",
    "--noconfirm",
    "--specpath", "build",
    # pynput 은 OS 별 백엔드를 실행 중에 고르므로 명시적으로 포함
    "--hidden-import", "pynput.keyboard._win32",
    "--hidden-import", "pynput.mouse._win32",
    # 쓰지 않는 무거운 모듈 제외 (용량 절감)
    "--exclude-module", "matplotlib",
    "--exclude-module", "pytest",
])
print(f"완료: dist/{NAME}.exe")
