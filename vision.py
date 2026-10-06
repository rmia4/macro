"""화면 인식: 조건(이미지 / 색) 검증과 판정.

조건 형식 (매크로 JSON 의 이벤트 안에 들어간다):
  이미지: {"kind": "image", "template": "확인버튼.png", "region": [x, y, w, h] | null,
           "threshold": 0.85, "negate": false}
  색:     {"kind": "pixel", "x": 100, "y": 200, "w": 30, "h": 10, "color": "#ff0000", "tolerance": 20,
           "ratio": 0.5, "negate": false}
          범위 [x, y, w, h] 안에서 color(채널별 ±tolerance)인 픽셀의 비율이 ratio 이상이면 충족.
          w, h 를 생략하면 (x, y) 한 점만 본다.
  변수:   {"kind": "var", "name": "보스", "negate": false}  '변수 저장'으로 지정한 조건을 쓰일 때 판정. 없으면 거짓.
  복합:   {"kind": "all" | "any", "conds": [잎 조건 2개 이상], "negate": false}
          all = 모두 맞을 때(그리고), any = 하나라도 맞을 때(또는). 안에는 이미지/색/변수만 (중첩 없음).

좌표는 매크로의 좌표 기준(화면/창)을 따르며, 판정할 때 origin(창 좌상단)을 더해 화면 좌표로 바꾼다.
template 은 매크로별 이미지 폴더(assets_dir) 안의 파일 이름이다. 주 모니터만 대상으로 한다.
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

SCREEN_KINDS = ("image", "pixel")
LEAF_KINDS = SCREEN_KINDS + ("var",)
GROUP_KINDS = ("all", "any")
COND_KINDS = LEAF_KINDS + GROUP_KINDS
VAR_RE = re.compile(r"^\w{1,20}$")
DEFAULT_THRESHOLD = 0.85
DEFAULT_TOLERANCE = 20
DEFAULT_RATIO = 0.5
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_TEMPLATE_RE = re.compile(r"^[\w\-. ]+\.png$")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_var_name(name) -> None:
    if not isinstance(name, str) or not VAR_RE.match(name):
        raise ValueError(f"잘못된 변수 이름: {name!r} (글자·숫자·_ 1~20자)")


def validate_condition(cond, _leaf_only: bool = False) -> None:
    """조건 구조 검증 (파일 존재 여부는 보지 않는다). 잘못되면 ValueError."""
    if not isinstance(cond, dict):
        raise ValueError("조건(cond)은 객체여야 합니다")
    kind = cond.get("kind")
    if kind not in COND_KINDS:
        raise ValueError(f"알 수 없는 조건 종류: {kind!r}")
    if _leaf_only and kind in GROUP_KINDS:
        raise ValueError("여러 조건 안에 여러 조건을 넣을 수 없습니다")
    if not isinstance(cond.get("negate", False), bool):
        raise ValueError("negate 는 true/false 여야 합니다")
    if kind == "var":
        validate_var_name(cond.get("name"))
    elif kind in GROUP_KINDS:
        conds = cond.get("conds")
        if not isinstance(conds, list) or len(conds) < 2:
            raise ValueError("여러 조건에는 조건이 2개 이상 필요합니다")
        for c in conds:
            validate_condition(c, _leaf_only=True)
    elif kind == "image":
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
    if cond.get("kind") == "var":
        return f"변수 '{cond.get('name')}'{neg}"
    if cond.get("kind") in GROUP_KINDS:
        joiner = " 그리고 " if cond["kind"] == "all" else " 또는 "
        text = joiner.join(f"[{describe_condition(c)}]" for c in cond.get("conds", []))
        return f"({text}){neg}" if neg else text
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
        self._fft_cache: dict[str, dict] = {}  # 템플릿별 FFT (같은 영역을 반복해서 찾을 때 재사용)

    def template(self, name: str):
        """템플릿 이미지(BGR)."""
        if name not in self._templates:
            if self.assets_dir is None:
                raise FileNotFoundError(f"이미지 폴더가 없어 '{name}' 을(를) 찾을 수 없습니다 (먼저 매크로를 저장하세요)")
            path = self.assets_dir / name
            if not path.is_file():
                raise FileNotFoundError(f"조건 이미지가 없습니다: {path}")
            try:
                img = load_png(path)
            except ValueError as e:
                raise ValueError(f"이미지를 읽을 수 없습니다: {path} ({e})") from None
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
        import numpy as np
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
        result = match_template(shot, tpl, self._fft_cache.setdefault(cond["template"], {}))
        ly, lx = np.unravel_index(int(np.argmax(result)), result.shape)
        score = float(max(0.0, min(1.0, result[ly, lx])))
        pos = (x0 + int(lx) + tw // 2, y0 + int(ly) + th // 2)
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


def _fast_len(n: int) -> int:
    """n 이상인 가장 작은 2·3·5 의 곱 (FFT 가 빠른 길이)."""
    best = 1 << max(0, (n - 1).bit_length())
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            v = p35
            while v < n:
                v *= 2
            best = min(best, v)
            p35 *= 3
        p5 *= 5
    return best


def _box_sum(a, h: int, w: int):
    """2차원 배열에서 모든 (h, w) 창의 합 -> (H-h+1, W-w+1) float64."""
    import numpy as np
    c = np.cumsum(a, axis=0, dtype=np.float64)
    r = c[h - 1:].copy()
    r[1:] -= c[:-h]
    c = np.cumsum(r, axis=1)
    out = c[:, w - 1:].copy()
    out[:, 1:] -= c[:, :-w]
    return out


def match_template(img, tpl, cache: dict | None = None):
    """OpenCV matchTemplate(TM_CCOEFF_NORMED) 와 같은 정규화 상관계수 맵 (numpy FFT 로 계산).

    img (H, W, C), tpl (h, w, C) -> (H-h+1, W-w+1) float64. 평균은 채널별로 빼고 합은 모든 채널에 걸쳐 구한다.
    분모가 0(영역이나 템플릿이 단색)인 위치는 0 이다.
    cache: 같은 템플릿을 반복해서 찾을 때 템플릿 쪽 FFT 를 재사용할 dict (템플릿마다 따로).
    """
    import numpy as np
    img = np.asarray(img)
    tpl = np.asarray(tpl)
    if img.ndim == 2:
        img = img[:, :, None]
    if tpl.ndim == 2:
        tpl = tpl[:, :, None]
    H, W = img.shape[:2]
    h, w = tpl.shape[:2]
    oh, ow = H - h + 1, W - w + 1
    if oh <= 0 or ow <= 0:
        raise ValueError("이미지가 템플릿보다 작습니다")
    # 채널을 앞으로 (FFT·누적합이 연속 메모리에서 빠르다)
    planes = np.ascontiguousarray(np.moveaxis(img, 2, 0), dtype=np.float64)
    fh, fw = _fast_len(H), _fast_len(W)

    key = (fh, fw)
    if cache is not None and key in cache:
        ft, t_norm2 = cache[key]
    else:
        t = np.moveaxis(tpl, 2, 0).astype(np.float64)
        t -= t.mean(axis=(1, 2), keepdims=True)
        t_norm2 = float((t * t).sum())
        # 상관 = 뒤집은 템플릿과의 합성곱
        ft = np.fft.rfft2(t[:, ::-1, ::-1], s=(fh, fw))
        if cache is not None:
            cache[key] = (ft, t_norm2)

    # 분자: sum(T' * I) — T' 의 합이 0 이므로 창 평균을 뺄 필요가 없다
    fi = np.fft.rfft2(planes, s=(fh, fw))
    num = np.fft.irfft2((fi * ft).sum(axis=0), s=(fh, fw))[h - 1:H, w - 1:W]

    # 분모: 창마다 sum((I - 평균)^2) = sum(I^2) - sum(I)^2 / n  (모든 채널 합)
    var = _box_sum((planes * planes).sum(axis=0), h, w)
    for p in planes:
        var -= _box_sum(p, h, w) ** 2 / (h * w)
    denom = np.sqrt(np.maximum(var, 0) * t_norm2)
    out = np.zeros((oh, ow))
    ok = denom > 1e-6 * max(1.0, t_norm2)
    out[ok] = num[ok] / denom[ok]
    return np.clip(out, -1.0, 1.0)


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}  # 색 형식 -> 채널 수


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(img) -> bytes:
    """BGR(또는 회색) uint8 이미지 -> PNG 바이트 (필터 없음)."""
    import numpy as np
    a = np.asarray(img)
    if a.dtype != np.uint8:
        raise ValueError("uint8 이미지만 저장할 수 있습니다")
    if a.ndim == 2:
        color, rows = 0, a
    elif a.ndim == 3 and a.shape[2] == 3:
        color, rows = 2, a[:, :, ::-1]  # BGR -> RGB
    else:
        raise ValueError("BGR 또는 회색 이미지만 저장할 수 있습니다")
    h, w = a.shape[:2]
    raw = np.zeros((h, 1 + rows[0].size), dtype=np.uint8)  # 각 줄 앞에 필터 0
    raw[:, 1:] = np.ascontiguousarray(rows).reshape(h, -1)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0)
    return (_PNG_SIG + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
            + _png_chunk(b"IEND", b""))


def _unfilter(data: bytes, h: int, stride: int, bpp: int):
    import numpy as np
    buf = np.frombuffer(data, dtype=np.uint8)
    if buf.size < h * (stride + 1):
        raise ValueError("PNG 데이터가 잘렸습니다")
    buf = buf[:h * (stride + 1)].reshape(h, stride + 1)
    out = np.zeros((h, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.uint8)
    for y in range(h):
        ft, line = buf[y, 0], buf[y, 1:]
        if ft == 0:
            cur = line.copy()
        elif ft == 1:  # Sub: 왼쪽 픽셀 누적 (바이트 위치 % bpp 별로 독립)
            pad = (-stride) % bpp
            lanes = np.concatenate([line, np.zeros(pad, np.uint8)]).reshape(-1, bpp).astype(np.uint64)
            cur = (lanes.cumsum(axis=0) & 0xFF).astype(np.uint8).reshape(-1)[:stride]
        elif ft == 2:  # Up
            cur = line + prev
        elif ft in (3, 4):  # Average / Paeth: 픽셀 순서대로 계산 (외부에서 만든 PNG 용)
            cur = np.zeros(stride, dtype=np.int32)
            ln, up = line.astype(np.int32), prev.astype(np.int32)
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = up[i]
                if ft == 3:
                    cur[i] = (ln[i] + ((a + b) >> 1)) & 0xFF
                else:
                    c = up[i - bpp] if i >= bpp else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                    cur[i] = (ln[i] + pred) & 0xFF
            cur = cur.astype(np.uint8)
        else:
            raise ValueError(f"알 수 없는 PNG 필터 {ft}")
        out[y] = prev = cur
    return out


def decode_png(data: bytes):
    """PNG 바이트 -> BGR uint8 이미지. 투명도는 버리고 16비트는 8비트로 줄인다 (OpenCV IMREAD_COLOR 와 같음)."""
    import numpy as np
    if not data.startswith(_PNG_SIG):
        raise ValueError("PNG 파일이 아닙니다")
    pos, ihdr, plte, idat = 8, None, None, []
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length:
            raise ValueError("PNG 데이터가 잘렸습니다")
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = np.frombuffer(body, dtype=np.uint8).reshape(-1, 3)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if ihdr is None or not idat:
        raise ValueError("PNG 구조가 올바르지 않습니다")
    w, h, depth, color, _, _, interlace = ihdr
    if color not in _PNG_CHANNELS or depth not in (1, 2, 4, 8, 16) or w == 0 or h == 0:
        raise ValueError("지원하지 않는 PNG 형식입니다")
    if interlace:
        raise ValueError("인터레이스 PNG 는 지원하지 않습니다")
    ch = _PNG_CHANNELS[color]
    if depth < 8 and ch != 1:
        raise ValueError("지원하지 않는 PNG 형식입니다")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise ValueError(f"PNG 압축 해제 실패: {e}") from None
    bits = depth * ch
    stride = (w * bits + 7) // 8
    rows = _unfilter(raw, h, stride, max(1, bits // 8))
    if depth < 8:
        px = np.unpackbits(rows, axis=1).reshape(h, -1, depth)[:, :w]
        px = (px * (1 << np.arange(depth - 1, -1, -1, dtype=np.uint8))).sum(axis=2).astype(np.uint8)
        if color == 0:
            px = (px.astype(np.uint16) * 255 // ((1 << depth) - 1)).astype(np.uint8)
        px = px[:, :, None]
    elif depth == 16:
        px = rows.reshape(h, w, ch, 2)[..., 0]  # 상위 바이트
    else:
        px = rows.reshape(h, w, ch)
    if color == 3:
        if plte is None:
            raise ValueError("팔레트가 없는 PNG 입니다")
        rgb = plte[np.minimum(px[:, :, 0], len(plte) - 1)]
    elif color in (0, 4):
        rgb = np.repeat(px[:, :, :1], 3, axis=2)
    else:
        rgb = px[:, :, :3]
    return np.ascontiguousarray(rgb[:, :, ::-1])  # RGB -> BGR


def load_png(path: str | Path):
    """PNG 파일 -> BGR 이미지 (한글 경로 지원)."""
    return decode_png(Path(path).read_bytes())


def save_png(img, path: str | Path) -> None:
    """BGR 이미지를 PNG 로 저장 (한글 경로 지원)."""
    Path(path).write_bytes(encode_png(img))


def png_base64(img) -> str:
    """tk.PhotoImage(data=...) 용 PNG base64 문자열."""
    import base64
    return base64.b64encode(encode_png(img)).decode("ascii")


def leaf_conditions(cond: dict) -> list[dict]:
    """복합 조건을 펼친 잎 조건들 (이미지/색/변수)."""
    if cond.get("kind") in GROUP_KINDS:
        return [c for c in cond.get("conds", []) if isinstance(c, dict)]
    return [cond]


def conditions_in(events) -> list[dict]:
    """화면을 봐야 하는 잎 조건(이미지/색)들. 변수 조건은 제외."""
    return [c for ev in events if isinstance(ev, dict) and isinstance(ev.get("cond"), dict)
            for c in leaf_conditions(ev["cond"]) if c.get("kind") in SCREEN_KINDS]


def variables_used(events) -> set[str]:
    """조건에서 읽는 변수 이름."""
    return {c["name"] for ev in events if isinstance(ev, dict) and isinstance(ev.get("cond"), dict)
            for c in leaf_conditions(ev["cond"]) if c.get("kind") == "var" and isinstance(c.get("name"), str)}


def evaluate(cond: dict, check_leaf, variables: dict, _seen: frozenset = frozenset()) -> Match:
    """조건 판정. check_leaf(이미지/색 조건) -> Match.
    변수는 variables[이름] 이 조건(dict)이면 쓰이는 지금 판정하고, 참/거짓 값이면 그대로 쓴다 (없으면 거짓).
    자기 자신을 다시 참조하는 변수는 거짓으로 본다.
    여러 조건은 결과가 정해지면 나머지를 보지 않는다. 반환하는 Match 는 위치·점수를 마지막 판정에서 가져온다."""
    kind = cond["kind"]
    if kind == "var":
        name = cond["name"]
        value = variables.get(name, False)
        if isinstance(value, dict) and name not in _seen:
            sub = evaluate(value, check_leaf, variables, _seen | {name})
            m = Match(sub.matched, sub.score, sub.pos)
        else:
            m = Match(value is True, 1.0)
    elif kind in GROUP_KINDS:
        want_all = kind == "all"
        for c in cond["conds"]:  # 마지막으로 본 조건의 결과가 곧 전체 결과
            sub = evaluate(c, check_leaf, variables, _seen)
            m = Match(sub.matched, sub.score, sub.pos)
            if sub.matched != want_all:
                break
    else:
        return check_leaf(cond)  # negate 는 판정기가 반영
    if cond.get("negate"):
        m.matched = not m.matched
    return m


def templates_in(events) -> set[str]:
    """이벤트(또는 편집 항목)가 쓰는 조건 이미지 파일 이름."""
    return {c["template"] for c in conditions_in(events) if c.get("kind") == "image" and c.get("template")}


