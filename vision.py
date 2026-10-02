"""(실험적) 화면 인식: 조건(이미지 / 픽셀 색) 검증과 판정.

조건 형식 (매크로 JSON 의 이벤트 안에 들어간다):
  이미지: {"kind": "image", "template": "확인버튼.png", "region": [x, y, w, h] | null,
           "threshold": 0.85, "negate": false}
  픽셀:   {"kind": "pixel", "x": 100, "y": 200, "color": "#ff0000", "tolerance": 20, "negate": false}

좌표는 매크로의 좌표 기준(화면/창)을 따르며, 판정할 때 origin(창 좌상단)을 더해 화면 좌표로 바꾼다.
template 은 매크로별 이미지 폴더(assets_dir) 안의 파일 이름이다. 주 모니터만 대상으로 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

COND_KINDS = ("image", "pixel")
DEFAULT_THRESHOLD = 0.85
DEFAULT_TOLERANCE = 20
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_TEMPLATE_RE = re.compile(r"^[\w\-. ]+\.png$")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_condition(cond) -> None:
    """조건 구조 검증 (파일 존재 여부는 보지 않는다). 잘못되면 ValueError."""
    if not isinstance(cond, dict):
        raise ValueError("조건(cond)은 객체여야 합니다")
    kind = cond.get("kind")
    if kind not in COND_KINDS:
        raise ValueError(f"알 수 없는 조건 종류: {kind!r}")
    if not isinstance(cond.get("negate", False), bool):
        raise ValueError("negate 는 true/false 여야 합니다")
    if kind == "image":
        name = cond.get("template")
        if not isinstance(name, str) or not _TEMPLATE_RE.match(name) or name.strip(".") == "png":
            raise ValueError(f"잘못된 이미지 파일 이름: {name!r} (같은 폴더의 .png 파일 이름)")
        region = cond.get("region")
        if region is not None and (not isinstance(region, list) or len(region) != 4
                                   or not all(isinstance(v, int) and not isinstance(v, bool) for v in region)
                                   or region[2] <= 0 or region[3] <= 0):
            raise ValueError("region 은 [x, y, 너비, 높이] 정수여야 합니다")
        th = cond.get("threshold", DEFAULT_THRESHOLD)
        if not _num(th) or not 0 < th <= 1:
            raise ValueError("threshold 는 0 초과 1 이하여야 합니다")
    else:
        if not (_num(cond.get("x")) and _num(cond.get("y"))):
            raise ValueError("픽셀 조건에는 x, y 가 필요합니다")
        if not isinstance(cond.get("color"), str) or not _COLOR_RE.match(cond["color"]):
            raise ValueError("color 는 '#rrggbb' 형식이어야 합니다")
        tol = cond.get("tolerance", DEFAULT_TOLERANCE)
        if not _num(tol) or not 0 <= tol <= 255:
            raise ValueError("tolerance 는 0~255 여야 합니다")


def parse_color(text: str) -> tuple[int, int, int]:
    """'#rrggbb' -> (r, g, b)."""
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


def describe_condition(cond: dict) -> str:
    neg = " 아님" if cond.get("negate") else ""
    if cond.get("kind") == "image":
        th = round(cond.get("threshold", DEFAULT_THRESHOLD) * 100)
        where = " 영역 [{}, {}, {}, {}]".format(*cond["region"]) if cond.get("region") else " 화면 전체"
        return f"이미지 '{cond.get('template')}' ≥{th}%{where}{neg}"
    return (f"픽셀 ({cond.get('x')}, {cond.get('y')}) = {cond.get('color')} "
            f"±{cond.get('tolerance', DEFAULT_TOLERANCE)}{neg}")


@dataclass
class Match:
    matched: bool                         # negate 까지 반영한 최종 결과
    score: float                          # 이미지: 일치도 0~1, 픽셀: 1 - 차이/255
    pos: tuple[int, int] | None = None    # 이미지: 찾은 위치의 중앙 (화면 좌표)


class MssGrabber:
    """mss 로 화면 영역을 캡처해 BGR numpy 배열로 반환. 스레드마다 mss 인스턴스를 따로 쓴다."""

    def __init__(self) -> None:
        import threading
        self._local = threading.local()

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss
            sct = self._local.sct = mss.mss()
        return sct

    def screen_size(self) -> tuple[int, int]:
        mon = self._sct().monitors[1]  # 주 모니터
        return mon["width"], mon["height"]

    def grab(self, x: int, y: int, w: int, h: int):
        import numpy as np
        mon = self._sct().monitors[1]
        shot = self._sct().grab({"left": mon["left"] + x, "top": mon["top"] + y, "width": w, "height": h})
        return np.asarray(shot)[:, :, :3]  # BGRA -> BGR


class Vision:
    """조건 판정. grabber 를 주입하면 OS 없이 테스트할 수 있다 (grab(x, y, w, h), screen_size())."""

    def __init__(self, assets_dir: str | Path | None, grabber=None) -> None:
        self.assets_dir = Path(assets_dir) if assets_dir else None
        self.grabber = grabber or MssGrabber()
        self._templates: dict[str, object] = {}

    def template(self, name: str):
        """템플릿 이미지(BGR). 한글 경로에서도 읽을 수 있게 imdecode 를 쓴다."""
        if name not in self._templates:
            import cv2
            import numpy as np
            if self.assets_dir is None:
                raise FileNotFoundError(f"이미지 폴더가 없어 '{name}' 을(를) 찾을 수 없습니다 (먼저 매크로를 저장하세요)")
            path = self.assets_dir / name
            if not path.is_file():
                raise FileNotFoundError(f"조건 이미지가 없습니다: {path}")
            img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"이미지를 읽을 수 없습니다: {path}")
            self._templates[name] = img
        return self._templates[name]

    def preload(self, conditions) -> None:
        """재생 시작 전에 이미지가 모두 있는지 확인 (없으면 입력을 보내기 전에 실패)."""
        for cond in conditions:
            if cond.get("kind") == "image":
                self.template(cond["template"])

    def check(self, cond: dict, origin: tuple[int, int] = (0, 0)) -> Match:
        if cond["kind"] == "pixel":
            m = self._check_pixel(cond, origin)
        else:
            m = self._check_image(cond, origin)
        if cond.get("negate"):
            m.matched = not m.matched
        return m

    def _check_pixel(self, cond: dict, origin) -> Match:
        x, y = int(round(cond["x"])) + origin[0], int(round(cond["y"])) + origin[1]
        b, g, r = (int(v) for v in self.grabber.grab(x, y, 1, 1)[0, 0])
        tr, tg, tb = parse_color(cond["color"])
        diff = max(abs(r - tr), abs(g - tg), abs(b - tb))
        return Match(diff <= cond.get("tolerance", DEFAULT_TOLERANCE), round(1 - diff / 255, 4))

    def _check_image(self, cond: dict, origin) -> Match:
        import cv2
        tpl = self.template(cond["template"])
        th, tw = tpl.shape[:2]
        if cond.get("region"):
            rx, ry, rw, rh = cond["region"]
            rx, ry = rx + origin[0], ry + origin[1]
        else:
            rx, ry = 0, 0
            rw, rh = self.grabber.screen_size()
        sw, sh = self.grabber.screen_size()  # 주 모니터 밖으로 나간 부분은 잘라낸다
        x0, y0 = max(0, rx), max(0, ry)
        x1, y1 = min(sw, rx + rw), min(sh, ry + rh)
        if x1 - x0 < tw or y1 - y0 < th:
            return Match(False, 0.0)  # 영역이 이미지보다 작다
        shot = self.grabber.grab(x0, y0, x1 - x0, y1 - y0)
        result = cv2.matchTemplate(shot, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        score = float(max(0.0, min(1.0, score)))
        pos = (x0 + loc[0] + tw // 2, y0 + loc[1] + th // 2)
        return Match(score >= cond.get("threshold", DEFAULT_THRESHOLD), round(score, 4), pos)


def conditions_in(events) -> list[dict]:
    return [ev["cond"] for ev in events if isinstance(ev, dict) and isinstance(ev.get("cond"), dict)]


