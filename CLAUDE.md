# CLAUDE.md

싱글 플레이 게임용 입력 녹화·재생 매크로 도구 (Windows 전용, Python 3.11+, tkinter GUI).
사용 방법은 `docs/user-guide.html`, 프로젝트 소개·빌드·저장 형식은 README.md, 이 파일은 작업용 요약이다. **답변·UI 문구·커밋 메시지는 한국어.**

## 구조 (평면 import, 패키지 아님)
| 파일 | 역할 |
|---|---|
| `main.py` | 진입점(기본 GUI, `--cli` REPL, `--selftest`), `App`: 라이브러리·녹화·재생 상태, 저장/이름변경/삭제(+이미지 폴더) |
| `gui.py` | tkinter 화면 전부: `Gui`(메인 목록), `EditorWindow`(기록 화면), `EventDialog`(키 입력으로 지정, 마우스 이동의 이동 녹화), `ConditionDialog`(wait/if/break/click/set_var/loop(횟수·동안 반복) + 여러 조건의 하위 조건용 cond 모드, 모드별 선택지 `KINDS_BY_MODE`, 이미지 조건의 파일/이미지 변수 선택), `ImageVarDialog`(set_image: 파일 또는 재생 중 캡처 영역), `show_thumb`, `RegionSelector`, `HotkeyCaptureDialog`, `Overlay`, `center_on_parent` |
| `editor_model.py` | 기록 화면의 **순수 로직**: 이벤트↔편집 항목(dt 기반) 변환, 이동 경로 묶기(path/relpath), 항목 생성(build_*), 구간 감싸기(wrap_repeat/wrap_if/wrap_while), 반복문(build_loop/replace_loop_start), 변수 지정 모음(variable_defs), 이미지 변수(build_set_image/image_variables_in/image_defs), 저장 검증 |
| `player.py` | 재생기: 시간표(origin+cum), 지터, 보간, 창 포커스 제한, 반복/분기/탈출, 조건 대기, 안전 해제. `PlayOptions`/`set_option`/`options_*_dict` |
| `recorder.py` | `RecorderCore`(순수) + `Recorder`(pynput, relative 시 Raw Input) |
| `profiles.py` | 매크로 JSON 스키마 검증(`Macro.from_dict`), `blocks()` 반복·분기 구조, 저장/목록/경로 |
| `vision.py` | 조건: 검증·설명, `evaluate`(변수·all/any 단축 평가), 이미지(템플릿 매칭)/범위 색 판정, `MssGrabber`, `dominant_color`. OpenCV 없이 numpy 로 구현: `match_template`(FFT, TM_CCOEFF_NORMED 동일), PNG `encode_png`/`decode_png`(zlib) — exe 용량 때문에 cv2 쓰지 말 것. 판정 최적화: 지난 위치 주변 먼저(`_last_pos`), 화면 crc 같으면 재사용(`_last_full`), 넓은 영역은 축소 후보→원본 확인(`pyramid_factor`/`shrink`), `Vision.frame()` 안에서는 캡처 공유(여러 조건 영역 합쳐 한 번). 캡처는 `default_grabber()`(Windows: `DxgiGrabber`→실패 시 `MssGrabber`, 프로세스에 하나) |
| `input_backend.py` | Windows API만: SendInput(스캔코드), DPI, 창 찾기, 클릭 통과, `RawMouseListener`, `DesktopDuplication`(DXGI COM 을 ctypes 가상 함수 표로 호출). 로직 넣지 말 것 |
| `keys.py` | 키 이름↔스캔코드/VK, 핫키 파싱(`parse_hotkey`: 1~2키, `ctrl+f1`), tk 키 이름 변환 |
| `hotkeys.py` | 전역 핫키 디스패처(조합·auto-repeat·suppress), 제어 키 F8/F9/F10 |
| `settings.py` / `paths.py` | settings.json(창 위치, 실행 가능 여부, 전환 키, 오버레이 위치 등) / 데이터 폴더(exe면 exe 옆) |
| `build.py`, `.github/workflows/build-exe.yml`, `icon.ico` | PyInstaller **폴더 배포**(onedir, 단일 exe는 Defender 오탐 Bearfoos.A!ml) + 버전 정보·아이콘, CI: 부트로더 직접 컴파일→numpy 를 BLAS 없이 소스 빌드(캐시)→Windows 테스트→빌드→`--selftest`→artifact(폴더)/Release(zip). 용량: `EXCLUDES`(표준 라이브러리·numpy 하위 모듈), `--optimize 2`, `PRUNE`(Tcl tzdata 등) |

## 핵심 설계 규칙
- **OS 의존은 주입**: Player/Recorder/App/GUI는 backend·clock·waiter·grabber·vision·factory를 주입받는다. 테스트는 `tests/fakes.py`의 FakeBackend/FakeClock/FakeRecorder/FakePlayer 사용. Windows API 호출은 input_backend에만.
- **시간**: 저장은 절대 시각 `t`, 편집 화면은 `dt`(직전으로부터 지연). 재생은 `origin + 누적 dt`까지 대기 → 조건 대기 후엔 `origin = now - cum`으로 재정렬.
- **블록**: repeat_start(count, 0=무한)/repeat_end, while_start(cond)/while_end, if_start(cond)/else/if_end는 `profiles.blocks()`로 검증·해석(`parent_loop`=가장 안쪽 반복, `loops`=감싸는 반복 체인; 시작/끝 표시는 자기 반복 바깥). 재생은 인덱스 점프(반복 시작 표시는 재실행 안 함, while_end는 while_start로 돌아가 재판정).
- **조건/변수**: cond = image/pixel/var 잎 또는 all/any(잎만, 중첩 없음). `set_var`는 판정하지 않고 변수에 조건을 **지정**만 한다(`Player.variables[이름] = cond`). 변수 조건은 **쓰일 때마다** `vision.evaluate`가 지정된 조건을 판정(미지정·자기 참조 = 거짓, bool 값도 허용). 지정은 재생 회차마다 비운다. `vision.conditions_in`은 화면 잎 조건만 반환(변수만 쓰면 vision 불필요).
- **조건 선택지**(`KINDS_BY_MODE`): 반복문·만약·반복 탈출·조건 대기·변수 저장 모두 이미지/범위 색/변수/여러 조건을 바로 고른다(반복문은 + 횟수). 여러 조건의 하위(cond 모드)는 이미지/범위 색/변수. 이미지 클릭은 이미지만.
- **이미지 변수**: `set_image(name, template | capture=[x,y,w,h])`. 이미지 조건은 `template` 대신 `image_var`로 참조. `Vision.images`(이름 -> (캐시 키, 이미지))에 담기며 `set_image`/`capture_image`/`clear_images`(Player 가 회차마다). 캐시 키는 `@이름:모양:crc` 라 이미지가 바뀌면 `_forget`으로 옛 캐시 정리. 미지정 = 거짓. `vision.needs_vision`(화면 조건 또는 set_image)으로 재생 시 vision 생성, `templates_in`은 set_image 파일 포함.
- **UI 이름**: 흐름·조건 항목은 while/await/if(else, if 끝)/break/const 로 표시(이벤트 타입 이름은 그대로).
- **이벤트 타입**: move, rmove(dx,dy), mdown/mup/scroll(x,y 생략=현재 커서), kdown/kup, wait, repeat_*, wait_until, if_start/else/if_end, break_if, click_image, set_var(name, cond), set_image(name, template|capture), while_start/while_end. 새 타입 추가 시: profiles 검증 → player → editor_model(라벨·describe·depths·KIND_FIELDS) → gui(on_edit 라우팅) → 테스트.
- **좌표**: 매크로별 `coord_space` screen/window(창 클라이언트 좌상단 기준). 조건 좌표도 동일. 절대/상대 이동은 **이벤트 종류만** 정한다(move=절대, rmove=상대). 재생 옵션 `mouse_mode`는 없앴고(예전 파일의 값은 무시), Raw Input 녹화 여부는 녹화 방식(기록 화면 체크·상대 이동의 이동 녹화·`record relative`)일 뿐 매크로 설정이 아니다.
- **이미지 자산**: `macros/<이름>/*.png`. 기록 화면은 임시 폴더에서 작업하고 저장 시 `App.store(..., assets=)`로 반영. 이름변경/복제/삭제 시 폴더도 처리.
- **안전**: 재생 중 예외·중단 시 `finally`에서 눌린 키/버튼 전부 해제. 조건 이미지 누락은 입력 보내기 전에 실패.

## 사용자 선호 (UI)
- 대상 미선택 상태의 편집/복제/삭제/수정/위로/아래로 → **알림 없이 무동작**.
- 작은 대화상자는 **부모 창 가운데**(Windows 제목 표시줄 보정 포함, `center_on_parent`).
- 녹화 상태 피드백은 기록 화면에, 저장하면 기록 화면 닫힘, 전체 실행 가능/불가는 전역 토글(+단축키 기본 Ctrl+F12).
- 오버레이 재생 표시는 `▶ 이름 · 현재/전체`만. "실험" 표기 쓰지 않음.

## 테스트
```
python -m pytest -q tests                        # tkinter 없으면 GUI 테스트 skip
xvfb-run -a python3.12 -m pytest -q tests        # (Linux 클라우드) tkinter 있는 3.12 + 가상 디스플레이로 GUI 포함 전체
```
- **작업 후 테스트는 영향 범위만**: 바꾼 모듈과 그 모듈을 쓰는 곳의 테스트 파일(필요하면 `-k`로 해당 테스트)만 돌린다. 예) `player.py`만 고쳤으면 `tests/test_player.py tests/test_flow.py`. 전체 실행은 여러 모듈에 걸친 큰 변경이나 병합·릴리스 전에만.
- 클라우드 환경: 기본 python3.11엔 tkinter 없음 → `/usr/bin/python3.12`(필요 시 `pip install --break-system-packages pytest opencv-python-headless mss numpy pynput`, `apt-get install python3-tk`).
- 실제 Windows 동작은 CI(`Build Windows exe`)에서만 검증된다. Windows 전용 차이(창 테두리, 콘솔 인코딩 cp1252 등)에 주의.

## 문서
- **README.md 에는 사용 방법을 쓰지 않는다**: 소개·기술적 특징·exe 받기/빌드·소스 실행·저장 형식(JSON)·구조·알려진 한계만. 사용 방법은 가이드 링크로 대신한다.
- **사용 방법은 `docs/user-guide.html`** (처음 쓰는 사람용 가이드, 화면 버튼 이름·문구를 실제 UI 와 같게).
- **가이드 HTML 은 기능 브랜치에서 바로바로 고치지 않는다.** 기능 작업 중에는 손대지 말고, 그 브랜치를 **main 에 병합할 때** 병합되는
  변경(버튼 이름, 새 기능, 없어진 설정 등)을 가이드에 반영하는 커밋을 함께 넣는다.

## 빌드 / 브랜치
- 기본 브랜치: `main` (릴리스용). 푸시하면 CI가 빌드해 artifact `MacroTool-windows`(MacroTool 폴더)로 올림. `v*` 태그면 Release에 zip.
- 사용자 데이터(`macros/*.json`, `macros/*/`, `settings.json`, 로그)는 git에 올리지 않는다.
- 큰 기능은 별도 브랜치에서 하고 요청 시 병합(fast-forward).
- **브랜치 이름은 작업 내용이 드러나게** 짓는다: `feature/<기능>`, `fix/<문제>`, `docs/<내용>` (영문 소문자·하이픈, 예: `feature/condition-variables-while`).
  세션이 자동으로 정한 무작위 이름(`claude/<형용사>-<이름>-<난수>`)은 쓰지 말고, 작업 시작 시 위 규칙의 브랜치를 만들어 그곳에 푸시한다.

## 비용 절약 (작업 방식)
- 맥락이 커지면 호출마다 전체를 다시 읽으므로: 스크린샷은 레이아웃이 크게 바뀔 때만, CI 로그는 필요한 줄만, 대기용 예약 확인은 최소화.
- 버그 재현은 테스트 픽스처 → 차이점 하나씩 바꿔 확인 → `mainloop`+`after()` 최소 스크립트(tkinter 는 `update()` 몇 번으로는 늦게 오는 창 메시지를 못 잡음) → 사용자에게 증상 질문 → 실제 앱 화면 조작은 마지막 순서. 화면 조작 전에 이유를 밝힌다.
- 패치는 `python - <<EOF` 문자열 치환으로 하고 `assert old in s`로 확인하는 방식을 써 왔다(실패 시 아무것도 쓰지 않도록 마지막에 write).
