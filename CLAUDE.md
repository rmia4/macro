# CLAUDE.md

싱글 플레이 게임용 입력 녹화·재생 매크로 도구 (Windows 전용, Python 3.11+, tkinter GUI).
사용자 문서는 README.md, 이 파일은 작업용 요약이다. **답변·UI 문구·커밋 메시지는 한국어.**

## 구조 (평면 import, 패키지 아님)
| 파일 | 역할 |
|---|---|
| `main.py` | 진입점(기본 GUI, `--cli` REPL, `--selftest`), `App`: 라이브러리·녹화·재생 상태, 저장/이름변경/삭제(+이미지 폴더) |
| `gui.py` | tkinter 화면 전부: `Gui`(메인 목록), `EditorWindow`(기록 화면), `EventDialog`, `ConditionDialog`(wait/if/break/click/set_var/loop(횟수·동안 반복) + 여러 조건의 하위 조건용 cond 모드, 모드별 선택지 `KINDS_BY_MODE`), `RegionSelector`, `HotkeyCaptureDialog`, `Overlay`, `center_on_parent` |
| `editor_model.py` | 기록 화면의 **순수 로직**: 이벤트↔편집 항목(dt 기반) 변환, 이동 경로 묶기(path/relpath), 항목 생성(build_*), 구간 감싸기(wrap_repeat/wrap_if/wrap_while), 반복문(build_loop/replace_loop_start), 변수 범위 표시(scope_text), 저장 검증 |
| `player.py` | 재생기: 시간표(origin+cum), 지터, 보간, 창 포커스 제한, 반복/분기/탈출, 조건 대기, 안전 해제. `PlayOptions`/`set_option`/`options_*_dict` |
| `recorder.py` | `RecorderCore`(순수) + `Recorder`(pynput, relative 시 Raw Input) |
| `profiles.py` | 매크로 JSON 스키마 검증(`Macro.from_dict`), `blocks()` 반복·분기 구조, 저장/목록/경로 |
| `vision.py` | 조건: 검증·설명, `evaluate`(변수·all/any 단축 평가), 이미지(템플릿 매칭)/범위 색 판정, `MssGrabber`, `dominant_color`, PNG 입출력(한글 경로 위해 imdecode/tofile) |
| `input_backend.py` | Windows API만: SendInput(스캔코드), DPI, 창 찾기, 클릭 통과, `RawMouseListener`. 로직 넣지 말 것 |
| `keys.py` | 키 이름↔스캔코드/VK, 핫키 파싱(`parse_hotkey`: 1~2키, `ctrl+f1`), tk 키 이름 변환 |
| `hotkeys.py` | 전역 핫키 디스패처(조합·auto-repeat·suppress), 제어 키 F8/F9/F10 |
| `settings.py` / `paths.py` | settings.json(창 위치, 실행 가능 여부, 전환 키, 오버레이 위치 등) / 데이터 폴더(exe면 exe 옆) |
| `build.py`, `.github/workflows/build-exe.yml` | PyInstaller 단일 exe, CI: Windows 테스트→빌드→`--selftest`→artifact |

## 핵심 설계 규칙
- **OS 의존은 주입**: Player/Recorder/App/GUI는 backend·clock·waiter·grabber·vision·factory를 주입받는다. 테스트는 `tests/fakes.py`의 FakeBackend/FakeClock/FakeRecorder/FakePlayer 사용. Windows API 호출은 input_backend에만.
- **시간**: 저장은 절대 시각 `t`, 편집 화면은 `dt`(직전으로부터 지연). 재생은 `origin + 누적 dt`까지 대기 → 조건 대기 후엔 `origin = now - cum`으로 재정렬.
- **블록**: repeat_start(count, 0=무한)/repeat_end, while_start(cond)/while_end, if_start(cond)/else/if_end는 `profiles.blocks()`로 검증·해석(`parent_loop`=가장 안쪽 반복, `loops`=감싸는 반복 체인; 시작/끝 표시는 자기 반복 바깥). 재생은 인덱스 점프(반복 시작 표시는 재실행 안 함, while_end는 while_start로 돌아가 재판정).
- **조건/변수**: cond = image/pixel/var 잎 또는 all/any(잎만, 중첩 없음). 변수는 boolean, `set_var`로 저장. 초기화는 `profiles.var_scopes()` — 저장·사용 위치를 모두 감싸는 가장 안쪽 반복의 회차마다(없으면 재생 회차마다). `vision.conditions_in`은 화면 잎 조건만 반환(변수만 쓰면 vision 불필요).
- **흐름 제어 조건은 변수로만**(UI 규칙): 반복문·만약·반복 탈출 창은 변수/여러 조건(변수만)만 고른다. 화면 판정(이미지/범위 색)은 변수 저장·조건 대기·이미지 클릭에서. 예전 화면 조건 항목은 수정 시에만 image/pixel 선택지를 덧붙인다(`em.uses_screen`). 데이터·재생은 화면 조건도 계속 지원.
- **이벤트 타입**: move, rmove(dx,dy), mdown/mup/scroll(x,y 생략=현재 커서), kdown/kup, wait, repeat_*, wait_until, if_start/else/if_end, break_if, click_image, set_var(name, cond), while_start/while_end. 새 타입 추가 시: profiles 검증 → player → editor_model(라벨·describe·depths·KIND_FIELDS) → gui(on_edit 라우팅) → 테스트.
- **좌표**: 매크로별 `coord_space` screen/window(창 클라이언트 좌상단 기준). 조건 좌표도 동일.
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
- 클라우드 환경: 기본 python3.11엔 tkinter 없음 → `/usr/bin/python3.12`(필요 시 `pip install --break-system-packages pytest opencv-python-headless mss numpy pynput`, `apt-get install python3-tk`).
- 실제 Windows 동작은 CI(`Build Windows exe`)에서만 검증된다. Windows 전용 차이(창 테두리, 콘솔 인코딩 cp1252 등)에 주의.

## 빌드 / 브랜치
- 기본 브랜치: `claude/fervent-heisenberg-9bybm2` (main 없음). 푸시하면 CI가 exe를 만들어 artifact `MacroTool-windows`로 올림. `v*` 태그면 Release.
- 사용자 데이터(`macros/*.json`, `macros/*/`, `settings.json`, 로그)는 git에 올리지 않는다.
- 큰 기능은 별도 브랜치에서 하고 요청 시 병합(fast-forward).
- **브랜치 이름은 작업 내용이 드러나게** 짓는다: `feature/<기능>`, `fix/<문제>`, `docs/<내용>` (영문 소문자·하이픈, 예: `feature/condition-variables-while`).
  세션이 자동으로 정한 무작위 이름(`claude/<형용사>-<이름>-<난수>`)은 쓰지 말고, 작업 시작 시 위 규칙의 브랜치를 만들어 그곳에 푸시한다.

## 비용 절약 (작업 방식)
- 맥락이 커지면 호출마다 전체를 다시 읽으므로: 스크린샷은 레이아웃이 크게 바뀔 때만, CI 로그는 필요한 줄만, 대기용 예약 확인은 최소화.
- 패치는 `python - <<EOF` 문자열 치환으로 하고 `assert old in s`로 확인하는 방식을 써 왔다(실패 시 아무것도 쓰지 않도록 마지막에 write).
