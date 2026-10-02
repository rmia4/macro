# macro_tool — 싱글 플레이 게임용 입력 녹화/재생 (Windows)

Steam 싱글 플레이 게임의 반복 작업(클릭·드래그·키·스크롤)을 녹화하고 재생한다. 현재 **1단계(MVP)**.

## 설치 / 실행
```
pip install -r requirements.txt
python main.py          # GUI (기본)
python main.py --cli    # 콘솔 REPL
```
Windows, Python 3.11+ (tkinter 포함 설치 필요).
게임이 관리자 권한이면 이 도구도 관리자 권한으로 실행해야 한다 (시작 시 경고 표시).

## GUI 사용법
1. **대상 창 제목**에 게임 창 제목의 일부를 입력하고 `옵션 적용`을 누른다 (비우면 포커스 제한 없음).
2. `● 녹화`(또는 F8) → 게임에서 동작 → F8로 종료. 버튼으로 시작하면 **시작 지연(기본 3초)** 동안 게임 창으로 전환할 수 있다.
   녹화 종료는 버튼 클릭이 기록되므로 **F8 키**를 권장한다.
3. 이름을 입력하고 `저장`. 목록에서 더블클릭(또는 `불러오기`)으로 로드.
4. `▶ 재생`(또는 F9). 재생 중에는 F9 또는 `■ 중지`로 즉시 멈춘다.

상태 표시줄(대기/녹화/재생/시작 대기)과 로그 창, 모든 재생 옵션 입력란이 있다. 재생·녹화 시작 시 옵션이 자동 적용되며, 잘못된 값은 오류 창으로 알려준다.

## 핫키 (`hotkeys.py` 상수로 변경)
| 키 | 동작 |
|----|------|
| F8 | 녹화 시작/종료 |
| F9 | 재생/중지 |
| F10 | 종료 (눌린 입력 모두 해제 후) |

제어 키는 매크로에 기록되지 않는다.

## REPL 명령 (`--cli`)
`record` · `play` · `stop` · `save <이름>` · `load <이름>` · `list` · `set <옵션> <값>` · `show` · `quit`

| 옵션 | 기본 | 설명 |
|------|------|------|
| `repeat` | 1 | 반복 횟수 (0 = 무한) |
| `speed` | 1.0 | 속도 배율 |
| `loop_delay` | 0 | 반복 사이 대기(초) |
| `time_jitter` | 0 | 대기 시간 ±% 편차 |
| `pos_jitter` | 0 | 클릭 좌표 ±px 편차 (한 번의 클릭/드래그 동안 동일 오프셋) |
| `approach` / `approach_duration` | on / 0.4 | 루프 시작 시 첫 위치까지 가감속 이동 |
| `min_key_hold` | 0.04 | 키 최소 유지 시간(초) |
| `window_title` | (없음) | 이 문자열이 제목에 포함된 창이 포커스일 때만 입력 전송. 녹화 시에는 좌표를 창 기준으로 저장 |
| `mouse_mode` | absolute | `absolute`(절대 좌표) / `relative`(델타 이동, 3D 시점용) |
| `scale_coords` | off | 창 크기가 녹화 때와 다르면 좌표를 비례 조정 |

권장 순서: `set window_title <게임 창 제목>` → F8 녹화 → F8 → `save name` → F9 재생.

## 저장 형식 (JSON)
```json
{"version": 1, "screen": {"width": 1920, "height": 1080},
 "coord_space": "window", "window": {"title": "...", "width": 1280, "height": 720},
 "events": [{"t": 0.0, "type": "move", "x": 10, "y": 20},
            {"t": 0.2, "type": "mdown", "x": 10, "y": 20, "button": "left"},
            {"t": 0.3, "type": "scroll", "x": 10, "y": 20, "dx": 0, "dy": -1},
            {"t": 0.4, "type": "kdown", "key": "w"}]}
```
이벤트 종류: `move`, `mdown`, `mup`, `scroll`, `kdown`, `kup`. 드래그는 별도 이벤트 없이 `mdown → move… → mup` 이다.

## 구조
`input_backend.py`만 Windows API(`SendInput`, 스캔코드/확장키)를 호출하고, 나머지(`recorder`/`player`/`hotkeys`/`profiles`)는
백엔드·시계·대기 함수를 주입받아 Linux에서도 단위 테스트된다. GUI(`gui.py`, tkinter)는 `main.App` 로직에 위임하며,
GUI 테스트는 tkinter+디스플레이가 있을 때만 실행된다(Linux: `xvfb-run python -m pytest tests`).
```
pip install -r requirements-dev.txt && python -m pytest tests
```

## 알려진 한계
- 독점 전체 화면 모드보다 **창 모드/테두리 없는 창 모드**가 안정적이다.
- **상대 이동 모드**는 게임의 마우스 감도·가속 설정에 따라 재현 결과가 달라질 수 있다. 녹화는 절대 좌표(`pynput`)로 하고
  재생 시 연속 좌표의 차이를 델타로 변환하므로, 커서를 화면 중앙에 고정하는 3D 게임의 시점 회전은 정확히 녹화되지 않을 수 있다
  (raw input 델타 녹화는 이후 과제).
- **안티치트**가 있는 게임은 입력 자동화를 차단하거나 제재할 수 있으므로 사용 전 확인이 필요하다.
- 창 포커스를 잃으면 눌린 입력을 해제하고 일시정지하며, 복귀 후에는 끊긴 지점부터 이어가지만 도중에 풀린 드래그는 복원되지 않는다.
- 텐키 Enter는 일반 Enter로 기록된다. 한/영·IME 전환 키는 지원하지 않는다.
- 실제 Windows 환경에서의 `SendInput` 동작은 이 저장소의 CI/Linux 테스트로 검증되지 않았다(백엔드는 의도적으로 얇게 유지).
