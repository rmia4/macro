"""Windows 실행 파일 빌드 (폴더 배포).

    pip install -r requirements.txt pyinstaller
    python build.py

결과:
  dist/MacroTool-windows/MacroTool/     MacroTool.exe + _internal/ (폴더째 써야 한다)
  dist/MacroTool-windows.zip            위 폴더를 묶은 배포용 zip
매크로(macros/)와 설정(settings.json)은 MacroTool.exe 와 같은 폴더에 생긴다.

단일 exe(--onefile)는 실행할 때마다 임시 폴더에 풀려서 백신 머신러닝 탐지(예: Bearfoos.A!ml)에
걸리기 쉬워 폴더 배포를 쓴다. CI 는 PyInstaller 부트로더도 직접 컴파일한다 (.github/workflows/build-exe.yml).

용량 줄이기:
  - CI 는 numpy 를 BLAS/LAPACK 없이 소스에서 빌드해 쓴다 (OpenBLAS DLL 수십 MB 제외, 행렬곱은 안 씀).
    pip 의 일반 numpy 로 빌드해도 동작은 같고 용량만 크다.
  - 쓰지 않는 표준 라이브러리·numpy 하위 모듈 제외, 바이트코드 최적화(-OO: docstring·assert 제거).
  - Tcl 의 시간대 자료 등 tkinter 가 쓰지 않는 파일은 빌드 뒤 지운다.
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

# 쓰지 않는 모듈 (용량 절감). 실제로 필요한지는 CI 의 --selftest 가 확인한다.
EXCLUDES = [
    "matplotlib", "pytest",
    "cv2",  # 테스트 비교용으로만 설치된 OpenCV 가 섞여 들어가지 않게
    # 표준 라이브러리: 테스트·문서·네트워크·압축·DB·비동기
    "unittest", "doctest", "pydoc", "pydoc_data", "xmlrpc", "http", "xml", "ftplib", "smtplib", "imaplib",
    "poplib", "mailbox", "ssl", "_ssl", "_hashlib", "sqlite3", "_sqlite3", "lzma", "_lzma", "bz2", "_bz2",
    "asyncio", "multiprocessing", "concurrent", "decimal", "_decimal", "turtle", "turtledemo", "idlelib",
    "lib2to3", "tkinter.tix",
    # numpy: FFT·기본 연산만 쓴다
    "numpy.random", "numpy.polynomial", "numpy.ma", "numpy.testing", "numpy.f2py", "numpy.distutils",
]
# 빌드 결과에서 지울 것 (_internal 기준). tkinter 는 Tcl 의 clock 시간대 자료·Tk 예제를 쓰지 않는다.
PRUNE = ["_tcl_data/tzdata", "_tcl_data/msgs", "_tk_data/demos", "_tk_data/images"]


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
        "--optimize", "2",
        "--icon", str(ROOT / "icon.ico"),
        "--version-file", str(version_file(version)),
        "--distpath", str(OUT),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        # pynput 은 OS 별 백엔드를 실행 중에 고르므로 명시적으로 포함
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        *[arg for name in EXCLUDES for arg in ("--exclude-module", name)],
    ])
    internal = OUT / NAME / "_internal"
    for rel in PRUNE:
        shutil.rmtree(internal / rel, ignore_errors=True)
    report_size(OUT / NAME)
    archive = shutil.make_archive(str(OUT), "zip", root_dir=OUT, base_dir=NAME)
    print(f"완료 (v{version}): {OUT / NAME / (NAME + '.exe')}")
    print(f"배포용: {archive}")


def report_size(folder: Path) -> None:
    """배포 폴더 크기와 큰 파일들. BLAS 가 들어갔으면 알린다."""
    files = [p for p in folder.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"배포 폴더: {total / 2**20:.1f} MB, 파일 {len(files)}개")
    for p in sorted(files, key=lambda p: p.stat().st_size, reverse=True)[:12]:
        print(f"  {p.stat().st_size / 2**20:7.2f} MB  {p.relative_to(folder)}")
    if any("openblas" in p.name.lower() for p in files):
        print("참고: OpenBLAS 가 포함됐습니다 (BLAS 없는 numpy 로 빌드하면 수십 MB 줄어듭니다)")


if __name__ == "__main__":
    main()
