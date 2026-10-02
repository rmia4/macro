"""화면 인식: 조건(이미지 / 색) 검증과 판정.

조건 형식 (매크로 JSON 의 이벤트 안에 들어간다):
  이미지: {"kind": "image", "template": "확인버튼.png", "region": [x, y, w, h] | null,
           "threshold": 0.85, "negate": false}
  색:     {"kind": "pixel", "x": 100, "y": 200, "w": 30, "h": 10, "color": "#ff0000", "tolerance": 20,
           "ratio": 0.5, "negate": false}
          범위 [x, y, w, h] 안에서 color(채널별 ±tolerance)인 픽셀의 비율이 ratio 이상이면 충족.
          w, h 를 생략하면 (x, y) 한 점만 본다.

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
DEFAULT_RATIO = 0.5
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
            raise ValueError("색 조건에는 x, y 가 필요합니다")
        if ("w" in cond) != ("h" in cond):
            raise ValueError("색 범위는 너비(w)와 높이(h)를 함께 지정해야 합니다")
        if "w" in cond and not all(isinstance(cond[k], int) and not isinstance(cond[k], bool) and cond[k] >= 1
                                   for k in ("w", "h")):
            raise ValueError("색 범위의 너비·높이는 1 이상의 정수여야 합니다")
        ratio = cond.get("ratio", DEFAULT_RATIO)
        if not _num(ratio) or not 0 < ratio <= 1:
            raise ValueError("ratio(색 비율)는 0 초과 1 이하여야 합니다")
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
    color = f"{cond.get('color')} ±{cond.get('tolerance', DEFAULT_TOLERANCE)}"
    if "w" in cond:
        ratio = round(cond.get("ratio", DEFAULT_RATIO) * 100)
        return f"색 범위 [{cond.get('x')}, {cond.get('y')}, {cond['w']}, {cond['h']}] {color} ≥{ratio}%{neg}"
    return f"색 ({cond.get('x')}, {cond.get('y')}) = {color}{neg}"


@dataclass
class Match:
    matched: bool                         # negate 까지 반영한 최종 결과
    score: float                          # 이미지: 일치도 0~1, 색: 범위 안 목표 색 비율 (한 점이면 1 - 차이/255)
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
        import numpy as np
        x, y = int(round(cond["x"])) + origin[0], int(round(cond["y"])) + origin[1]
        tr, tg, tb = parse_color(cond["color"])
        tol = cond.get("tolerance", DEFAULT_TOLERANCE)
        if "w" not in cond:  # 한 점
            b, g, r = (int(v) for v in self.grabber.grab(x, y, 1, 1)[0, 0])
            diff = max(abs(r - tr), abs(g - tg), abs(b - tb))
            return Match(diff <= tol, round(1 - diff / 255, 4))
        sw, sh = self.grabber.screen_size()
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(sw, x + cond["w"]), min(sh, y + cond["h"])
        if x1 <= x0 or y1 <= y0:
            return Match(False, 0.0)
        area = self.grabber.grab(x0, y0, x1 - x0, y1 - y0).astype(np.int16)
        diff = np.abs(area - np.array([tb, tg, tr], dtype=np.int16)).max(axis=2)
        share = float((diff <= tol).mean())
        return Match(share >= cond.get("ratio", DEFAULT_RATIO), round(share, 4))

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


def dominant_color(img, tolerance: int = DEFAULT_TOLERANCE) -> tuple[str, float]:
    """범위에서 가장 많이 차지하는 색 ('#rrggbb', 그 색이 허용 오차 안에서 차지하는 비율).

    색을 채널당 16단계로 묶어 가장 많은 묶음을 고르고, 그 묶음 픽셀들의 중앙값을 대표 색으로 쓴다.
    """
    import numpy as np
    px = img.reshape(-1, 3).astype(np.int16)
    q = px >> 4
    keys = (q[:, 0] << 8) | (q[:, 1] << 4) | q[:, 2]
    top = np.bincount(keys).argmax()
    b, g, r = (int(v) for v in np.median(px[keys == top], axis=0))
    share = float((np.abs(px - np.array([b, g, r])).max(axis=1) <= tolerance).mean())
    return f"#{r:02x}{g:02x}{b:02x}", round(share, 4)


def save_png(img, path: str | Path) -> None:
    """BGR 이미지를 PNG 로 저장 (한글 경로 지원)."""
    import cv2
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("PNG 인코딩 실패")
    buf.tofile(str(path))


def png_base64(img) -> str:
    """tk.PhotoImage(data=...) 용 PNG base64 문자열."""
    import base64
    import cv2
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("PNG 인코딩 실패")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def conditions_in(events) -> list[dict]:
    return [ev["cond"] for ev in events if isinstance(ev, dict) and isinstance(ev.get("cond"), dict)]


def templates_in(events) -> set[str]:
    """이벤트(또는 편집 항목)가 쓰는 조건 이미지 파일 이름."""
    return {c["template"] for c in conditions_in(events) if c.get("kind") == "image" and c.get("template")}


