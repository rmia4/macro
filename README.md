# macro_tool — 싱글 플레이 게임용 입력 녹화/재생 (Windows)

Steam 싱글 플레이 게임의 반복 작업(클릭·드래그·키·스크롤)을 녹화하고 재생한다. 현재 버전 **1.1.0**.

## 한눈에 보기
- **녹화·재생**: 마우스(절대/상대 이동)·키보드·스크롤을 녹화하고, 지터·보간·반복 횟수를 정해 재생한다.
- **편집**: 기록 화면에서 이벤트를 고치고, 구간을 반복·만약·동안 반복으로 감싼다.
- **화면 조건**: 이미지(템플릿 매칭)나 범위 색이 나타날 때까지 대기, 이미지 클릭, 변수에 조건을 지정해 흐름 제어에 사용.
- **AI로 만들기**: 자연어 설명(+ 게임 화면 캡처)으로 매크로 초안을 만들고, 기록 화면의 AI 대화로 고친다.
  Gemini API(무료 등급)·Claude API·Claude Code(구독) 중 선택.
- **안전**: 재생 중 오류·중단·창 포커스 이탈 시 눌린 키/버튼을 전부 해제한다.
- **배포**: Python 없이 실행되는 Windows exe(폴더 배포)를 CI가 테스트 후 자동 빌드한다.

## 기술적 특징
- **OpenCV 없이 화면 인식**: 템플릿 매칭(TM_CCOEFF_NORMED와 같은 결과)을 numpy FFT로, PNG 읽기/쓰기를 zlib으로 직접 구현해
  exe 용량을 줄였다. 지난 위치 주변 우선 탐색, 같은 화면 재사용, 축소 후보 탐색으로 판정을 빠르게 한다.
- **화면 캡처**: DXGI Desktop Duplication(COM을 ctypes 가상 함수 표로 호출)을 쓰고, 실패하면 mss로 대체한다.
- **OS 의존 분리**: Windows API는 `input_backend.py`에만 두고, 재생기·녹화기·GUI는 백엔드·시계·캡처를 주입받는다.
  덕분에 Linux에서도 가짜 객체로 단위·GUI 테스트를 돌린다.
- **AI 연동**: SDK 없이 urllib로 API를 직접 호출한다(또는 설치된 `claude -p`). AI는 지연(ms) 단위 단계와
  캡처 이미지 픽셀 좌표로 답하고, 이를 이벤트(`t`)·매크로 좌표·이미지 조건 자산으로 바꾼 뒤 기존 스키마 검증을 그대로 거친다.
  형식 오류면 한 번 다시 요청한다.
- **CI**: Windows에서 테스트 → PyInstaller 빌드 → exe 자가진단(`--selftest`) → artifact/Release 업로드.

## 실행 파일 (.exe)
Python 없이 실행할 수 있다. 배포는 **폴더째**(zip)이며, 압축을 풀면 다음처럼 된다.
```
MacroTool/
  MacroTool.exe   ← 이것을 실행
  _internal/      ← 실행에 필요한 파일 (지우거나 옮기지 말 것)
```
`MacroTool.exe`만 따로 꺼내면 실행되지 않으니 폴더째 쓰기 가능한 곳(예: `문서\MacroTool\`)에 둔다.
매크로(`macros/`)와 설정(`settings.json`)은 **exe와 같은 폴더**에 만들어진다. 게임이 관리자 권한이면 exe도
"관리자 권한으로 실행"한다. 오류로 종료되면 같은 폴더에 `crash.log`가 남는다.

- **GitHub에서 받기**: 저장소 Actions 탭 → `Build Windows exe` → 최근 실행 → Artifacts의 `MacroTool-windows`
  (받은 zip 안의 `MacroTool` 폴더를 사용). `v1.2.0` 같은 태그를 푸시하면 Releases에 `MacroTool-windows.zip`이 올라간다.
  (Windows에서 테스트 → 빌드 → exe 자가진단까지 자동 실행)
- **직접 빌드** (Windows):
  ```
  pip install -r requirements.txt pyinstaller
  python build.py          # -> dist\MacroTool-windows\MacroTool\MacroTool.exe, dist\MacroTool-windows.zip
  dist\MacroTool-windows\MacroTool\MacroTool.exe --selftest   # 점검 결과를 같은 폴더의 selftest.log 에 기록
  ```
- 처음 실행 시 Windows SmartScreen이 "알 수 없는 게시자" 경고를 띄울 수 있다(서명되지 않은 exe). `추가 정보 → 실행`.
- 백신 오탐: 키 입력 후킹·입력 자동화·화면 캡처를 하는 프로그램이라 백신이 의심 파일로 분류할 수 있다
  (예: Defender `Trojan:Win32/Bearfoos.A!ml`, 끝의 `!ml`은 머신러닝 추정 탐지). 이를 줄이려고 단일 exe 대신 폴더 배포,
  직접 컴파일한 PyInstaller 부트로더(CI), exe 버전 정보·아이콘을 쓴다. 그래도 걸리면 폴더를 Defender 제외 목록에 넣거나
  https://www.microsoft.com/wdsi/filesubmission 에 오탐으로 제출한다.

## 설치 / 실행 (소스)
```
pip install -r requirements.txt
python main.py          # GUI (기본)
python main.py --cli    # 콘솔 REPL
```
Windows, Python 3.11+ (tkinter 포함 설치 필요).
게임이 관리자 권한이면 이 도구도 관리자 권한으로 실행해야 한다 (시작 시 경고 표시).

## 사용 방법
사용 방법(화면 설명, 녹화·편집·재생, 화면 조건, AI로 만들기, 설정 항목, 단축키, 문제 해결, 콘솔 모드)은
**[사용자 가이드 `docs/user-guide.html`](docs/user-guide.html)** 에 있다. 브라우저로 열어 본다.

## 저장 형식 (JSON)
```json
{"version": 1, "screen": {"width": 1920, "height": 1080},
 "coord_space": "window", "window": {"title": "...", "width": 1280, "height": 720},
 "events": [{"t": 0.0, "type": "move", "x": 10, "y": 20},
            {"t": 0.2, "type": "mdown", "x": 10, "y": 20, "button": "left"},
            {"t": 0.3, "type": "scroll", "x": 10, "y": 20, "dx": 0, "dy": -1},
            {"t": 0.4, "type": "kdown", "key": "w"}]}
```
이벤트 종류: `move`, `rmove`(상대 이동 `dx`, `dy`), `mdown`, `mup`, `scroll`, `kdown`, `kup`, `wait`(지연만),
`repeat_start`(`count`: 0=무한, 1~100000) / `repeat_end`(반복 구간, 짝이 맞아야 하며 중첩 가능),
`wait_until`, `if_start`/`else`/`if_end`, `break_if`, `click_image`, `set_var`, `set_image`, `while_start`/`while_end`
(아래 "조건 이벤트" 참고).
`mdown`/`mup`/`scroll`은 `x`, `y`를 생략하면 현재 커서 위치에서 입력한다. `hotkey`는 `"f6"`, `"ctrl+f1"` 형식.
GUI로 저장하면 `"hotkey": "f6"`과 `"options": {"repeat": 0, "speed": 1.0, ...}`(재생 옵션)도 함께 저장된다. 드래그는 별도 이벤트 없이 `mdown → move… → mup` 이다.

### 조건 이벤트
조건(`cond`)은 `image`(템플릿 `template` 또는 이미지 변수 `image_var`, `region`, `threshold`) / `pixel`(범위 `x,y,w,h`, `color`,
`tolerance`, `ratio`) / `var`(변수 `name`) 잎, 또는 잎을 묶는 `all`/`any`(중첩 없음). 모두 `negate`로 반대 판정.
조건 이미지는 `macros/<매크로 이름>/`에 PNG로 저장된다.
```json
{"t": 1.2, "type": "wait_until", "timeout": 10, "on_timeout": "stop", "interval": 0.1,
 "cond": {"kind": "image", "template": "이미지1.png", "region": [600, 400, 300, 150], "threshold": 0.85}}
{"t": 2.0, "type": "wait_until", "timeout": 0, "on_timeout": "continue",
 "cond": {"kind": "pixel", "x": 120, "y": 40, "w": 200, "h": 12, "color": "#d03030", "tolerance": 20,
          "ratio": 0.5, "negate": true}}
{"t": 2.5, "type": "if_start", "cond": {...}}  ...  {"t": 3, "type": "else"}  ...  {"t": 3.5, "type": "if_end"}
{"t": 4.0, "type": "break_if", "cond": {...}}
{"t": 4.5, "type": "click_image", "cond": {"kind": "image", "template": "확인.png"}, "button": "left",
 "offset": [0, 5], "hold": 0.06, "timeout": 10, "on_timeout": "stop"}
```
```json
{"t": 0.5, "type": "set_var", "name": "보스", "cond": {"kind": "image", "template": "boss.png"}}
{"t": 0.6, "type": "while_start", "cond": {"kind": "all", "conds": [
   {"kind": "var", "name": "보스"}, {"kind": "pixel", "x": 50, "y": 20, "color": "#d03030"}]}}
  ...
{"t": 1.0, "type": "while_end"}
```
```json
{"t": 0.0, "type": "set_image", "name": "확인", "template": "이미지1.png"}
{"t": 0.0, "type": "set_image", "name": "처음", "capture": [600, 400, 120, 40]}
{"t": 0.5, "type": "break_if", "cond": {"kind": "image", "image_var": "처음", "region": [600, 400, 120, 40],
                                       "negate": true}}
{"t": 0.8, "type": "click_image", "cond": {"kind": "image", "image_var": "확인"}, "button": "left"}
```

## 구조
`input_backend.py`만 Windows API(`SendInput`, 스캔코드/확장키)를 호출하고, 나머지(`recorder`/`player`/`hotkeys`/`profiles`)는
백엔드·시계·대기 함수를 주입받아 Linux에서도 단위 테스트된다. GUI(`gui.py`, tkinter)는 `main.App` 로직에 위임하며,
GUI 테스트는 tkinter+디스플레이가 있을 때만 실행된다(Linux: `xvfb-run python -m pytest tests`).
```
pip install -r requirements-dev.txt && python -m pytest tests
```

## 알려진 한계
- 독점 전체 화면 모드보다 **창 모드/테두리 없는 창 모드**가 안정적이다.
- **상대 이동 모드**는 게임의 마우스 감도·가속 설정과 Windows "포인터 정확도 향상" 설정에 따라 재현 결과가 달라질 수 있다
  (이동량 배율로 보정).
- 상태 오버레이는 독점 전체 화면 게임 위에는 표시되지 않을 수 있다 (창 모드/테두리 없는 창 모드 권장).
- **안티치트**가 있는 게임은 입력 자동화를 차단하거나 제재할 수 있으므로 사용 전 확인이 필요하다.
- 창 포커스를 잃으면 눌린 입력을 해제하고 일시정지하며, 복귀 후에는 끊긴 지점부터 이어가지만 도중에 풀린 드래그는 복원되지 않는다.
- 텐키 Enter는 일반 Enter로 기록된다. 한/영·IME 전환 키는 지원하지 않는다.
- 실제 Windows 환경에서의 `SendInput` 동작은 이 저장소의 CI/Linux 테스트로 검증되지 않았다(백엔드는 의도적으로 얇게 유지).

## 개발 방식
이 프로젝트는 AI 코딩 도구(Claude Code)와 협업해 만들었다. 코드 작성은 대부분 AI가 맡았고,
기능 요구사항·화면 동작 규칙 정의, 실제 Windows 환경에서의 사용·검증, 작업 규칙 문서화(`CLAUDE.md`)는 직접 했다.
커밋 기록의 `Co-Authored-By: Claude` 표기가 이를 나타낸다.
