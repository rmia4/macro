"""Windows 실행 파일 빌드 (폴더 배포).

    pip install -r requirements.txt pyinstaller
    python build.py

결과:
  dist/MacroTool-windows/MacroTool/     MacroTool.exe + _internal/ (폴더째 써야 한다)
  dist/MacroTool-windows.zip            위 폴더를 묶은 배포용 zip
매크로(macros/)와 설정(settings.json)은 MacroTool.exe 와 같은 폴더에 생긴다.

단일 exe(--onefile)는 실행할 때마다 임시 폴더에 풀려서 백신 머신러닝 탐지(예: Bearfoos.A!ml)에
걸리기 쉬워 폴더 배포를 쓴다. CI 는 PyInstaller 부트로더도 직접 컴파일한다 (.github/workflows/build-exe.yml).
"""
import os
import re
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

# Windows 콘솔(cp1252 등)에서도 한글 메시지가 깨지거나 오류 나지 않게
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

NAME = "MacroTool"
VERSION = "1.1.0"  # v1.2.3 태그로 빌드하면 태그 번호를 쓴다
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dist" / f"{NAME}-windows"
WORK = ROOT / "build"


def app_version() -> str:
    m = re.fullmatch(r"v(\d+)(?:\.(\d+))?(?:\.(\d+))?", os.environ.get("GITHUB_REF_NAME", ""))
    if m:
        return ".".join(g or "0" for g in m.groups())
    return VERSION


def version_file(version: str) -> Path:
    """exe 속성(자세히)에 보이는 버전 정보 리소스."""
    nums = tuple(int(p) for p in version.split(".")) + (0,)
    strings = {
        "CompanyName": "rmia4",
        "FileDescription": "MacroTool - input recorder and player for single-player games",
        "FileVersion": version,
        "InternalName": NAME,
        "LegalCopyright": "Copyright (c) rmia4",
        "OriginalFilename": f"{NAME}.exe",
        "ProductName": NAME,
        "ProductVersion": version,
    }
    table = ",\n".join(f"        StringStruct({k!r}, {v!r})" for k, v in strings.items())
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
{table}
    ])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""
    WORK.mkdir(exist_ok=True)
    path = WORK / "version_info.txt"
    path.write_text(text, encoding="utf-8")
    return path


def main() -> None:
    if sys.platform != "win32":
        sys.exit("Windows 에서만 빌드할 수 있습니다 (PyInstaller 는 다른 OS 용 실행 파일을 만들지 못함).")
    version = app_version()
    shutil.rmtree(OUT, ignore_errors=True)
    PyInstaller.__main__.run([
        str(ROOT / "main.py"),
        "--name", NAME,
        "--onedir",
        "--noconsole",
        "--clean",
        "--noconfirm",
        "--noupx",
        "--icon", str(ROOT / "icon.ico"),
        "--version-file", str(version_file(version)),
        "--distpath", str(OUT),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        # pynput 은 OS 별 백엔드를 실행 중에 고르므로 명시적으로 포함
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        # 쓰지 않는 무거운 모듈 제외 (용량 절감)
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        # 테스트 비교용으로만 설치된 OpenCV 가 섞여 들어가지 않게
        "--exclude-module", "cv2",
    ])
    archive = shutil.make_archive(str(OUT), "zip", root_dir=OUT, base_dir=NAME)
    print(f"완료 (v{version}): {OUT / NAME / (NAME + '.exe')}")
    print(f"배포용: {archive}")


if __name__ == "__main__":
    main()
