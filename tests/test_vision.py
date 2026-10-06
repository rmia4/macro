import zlib

import numpy as np
import pytest

import vision
from vision import Vision, validate_condition


class FakeGrabber:
    """numpy 이미지를 화면으로 쓰는 가짜 캡처. grab 호출을 기록한다."""

    def __init__(self, screen):
        self.screen = screen
        self.calls = []

    def screen_size(self):
        h, w = self.screen.shape[:2]
        return w, h

    def grab(self, x, y, w, h):
        self.calls.append((x, y, w, h))
        return self.screen[y:y + h, x:x + w].copy()


def make_screen():
    rng = np.random.default_rng(0)
    screen = rng.integers(0, 60, size=(300, 400, 3), dtype=np.uint8)  # 어두운 잡음 배경
    # 버튼 모양: 흰 테두리 + 빨간 내부 + 대각선
    btn = np.zeros((30, 50, 3), dtype=np.uint8)
    btn[:] = (0, 0, 220)
    btn[:3, :] = btn[-3:, :] = btn[:, :3] = btn[:, -3:] = 255
    for i in range(30):
        btn[i, i] = (0, 255, 0)
    screen[200:230, 300:350] = btn
    screen[50, 60] = (10, 20, 250)  # BGR -> #fa140a
    return screen, btn


@pytest.fixture
def setup(tmp_path):
    screen, btn = make_screen()
    assets = tmp_path / "한글 매크로"  # 한글 경로에서도 읽혀야 한다
    assets.mkdir()
    vision.save_png(btn, assets / "확인버튼.png")
    grabber = FakeGrabber(screen)
    return Vision(assets, grabber), grabber


def test_pixel_condition(setup):
    v, g = setup
    cond = {"kind": "pixel", "x": 60, "y": 50, "color": "#fa140a", "tolerance": 5}
    m = v.check(cond)
    assert m.matched and m.score == 1.0 and g.calls[-1] == (60, 50, 1, 1)
    assert not v.check(dict(cond, color="#f0140a", tolerance=5)).matched     # 차이 10 > 5
    assert v.check(dict(cond, color="#f0140a", tolerance=10)).matched
    assert not v.check(dict(cond, negate=True)).matched
    assert v.check(dict(cond, x=10, y=0), origin=(50, 50)).matched          # 창 좌표 + origin


def test_image_found_in_region_and_full_screen(setup):
    v, g = setup
    cond = {"kind": "image", "template": "확인버튼.png", "region": [280, 180, 100, 80], "threshold": 0.9}
    m = v.check(cond)
    assert m.matched and m.score > 0.99 and m.pos == (325, 215)
    assert g.calls[-1] == (280, 180, 100, 80)                               # 영역만 캡처
    full = v.check({"kind": "image", "template": "확인버튼.png"})
    assert full.matched and full.pos == (325, 215)
    assert v.check(dict(cond, region=[200, 130, 100, 80]), origin=(80, 50)).pos == (325, 215)


def test_image_not_found_negate_and_clipping(setup):
    v, g = setup
    cond = {"kind": "image", "template": "확인버튼.png", "region": [0, 0, 200, 150]}
    m = v.check(cond)
    assert not m.matched and m.score < 0.85
    assert v.check(dict(cond, negate=True)).matched
    m = v.check(dict(cond, region=[290, 190, 500, 500]))                    # 화면 밖은 잘라냄
    assert m.matched and g.calls[-1] == (290, 190, 110, 110)
    assert v.check(dict(cond, region=[0, 0, 20, 20])).score == 0.0          # 영역 < 이미지


def test_preload_and_missing_template(tmp_path, setup):
    v, _ = setup
    v.preload([{"kind": "image", "template": "확인버튼.png"}, {"kind": "pixel"}])
    with pytest.raises(FileNotFoundError):
        v.preload([{"kind": "image", "template": "없음.png"}])
    with pytest.raises(FileNotFoundError):
        Vision(None, setup[1]).template("확인버튼.png")
    (tmp_path / "깨짐.png").write_bytes(b"not png")
    with pytest.raises(ValueError):
        Vision(tmp_path, setup[1]).template("깨짐.png")


@pytest.mark.parametrize("cond", [
    None, {"kind": "sound"},
    {"kind": "image", "template": "../x.png"}, {"kind": "image", "template": "a.jpg"},
    {"kind": "image", "template": "a.png", "region": [0, 0, 0, 5]},
    {"kind": "image", "template": "a.png", "threshold": 1.5},
    {"kind": "pixel", "x": 1, "y": 1, "color": "red"},
    {"kind": "pixel", "x": 1, "color": "#000000"},
    {"kind": "pixel", "x": 1, "y": 1, "color": "#000000", "tolerance": 300},
    {"kind": "pixel", "x": 1, "y": 1, "color": "#000000", "negate": "yes"},
])
def test_validate_condition_errors(cond):
    with pytest.raises(ValueError):
        validate_condition(cond)


def test_describe_condition():
    assert vision.describe_condition({"kind": "image", "template": "a.png", "region": [1, 2, 3, 4]}) == \
        "이미지 'a.png' ≥85% 영역 [1, 2, 3, 4]"
    assert vision.describe_condition({"kind": "pixel", "x": 1, "y": 2, "color": "#00ff00", "negate": True}) == \
        "색 (1, 2) = #00ff00 ±20 아님"



def test_color_range_condition(setup):
    v, g = setup
    # 버튼(30x50): 내부 빨강(0,0,220) 대부분 + 흰 테두리 + 초록 대각선
    cond = {"kind": "pixel", "x": 300, "y": 200, "w": 50, "h": 30, "color": "#dc0000", "tolerance": 10}
    m = v.check(cond)
    assert m.matched and 0.6 < m.score < 0.9 and g.calls[-1] == (300, 200, 50, 30)
    assert not v.check(dict(cond, ratio=0.95)).matched
    assert not v.check(dict(cond, color="#00ff00")).matched
    assert v.check(dict(cond, x=390, w=50)).score < 1          # 화면 밖은 잘라냄
    assert g.calls[-1] == (390, 200, 10, 30)
    assert not v.check(dict(cond, x=500)).matched              # 완전히 화면 밖


def test_dominant_color():
    _, btn = make_screen()
    color, share = vision.dominant_color(btn)
    assert color == "#dc0000" and 0.6 < share < 0.9
    noisy = np.full((10, 10, 3), (30, 30, 200), dtype=np.uint8)
    noisy[::3, ::3] = (31, 29, 203)                               # 잡음은 같은 색으로 묶임
    assert vision.dominant_color(noisy, 5) == ("#c81e1e", 1.0)


@pytest.mark.parametrize("cond", [
    {"kind": "pixel", "x": 1, "y": 1, "color": "#000000", "w": 5},
    {"kind": "pixel", "x": 1, "y": 1, "color": "#000000", "w": 0, "h": 5},
    {"kind": "pixel", "x": 1, "y": 1, "color": "#000000", "w": 2, "h": 2, "ratio": 0},
])
def test_color_range_validation(cond):
    with pytest.raises(ValueError):
        validate_condition(cond)


def test_describe_color_range():
    assert vision.describe_condition({"kind": "pixel", "x": 1, "y": 2, "w": 3, "h": 4, "color": "#00ff00",
                                      "ratio": 0.7}) == "색 범위 [1, 2, 3, 4] #00ff00 ±20 ≥70%"


# --- OpenCV 없이 구현한 PNG 입출력 · 템플릿 매칭 ---

def _png(w, h, color, depth, rows, filters, plte=None):
    """테스트용 PNG 바이트 (줄마다 지정한 필터 번호를 그대로 붙인다: 이미 필터링된 rows 를 넘긴다)."""
    raw = b"".join(bytes([f]) + bytes(r) for f, r in zip(filters, rows))
    ihdr = vision.struct.pack(">IIBBBBB", w, h, depth, color, 0, 0, 0)
    out = vision._PNG_SIG + vision._png_chunk(b"IHDR", ihdr)
    if plte is not None:
        out += vision._png_chunk(b"PLTE", bytes(plte))
    return out + vision._png_chunk(b"IDAT", zlib.compress(raw)) + vision._png_chunk(b"IEND", b"")


def _filter_row(ft, cur, prev, bpp):
    """PNG 필터 적용 (명세 그대로, 디코더 검증용)."""
    out = []
    for i, x in enumerate(cur):
        a = cur[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ft == 0:
            pred = 0
        elif ft == 1:
            pred = a
        elif ft == 2:
            pred = b
        elif ft == 3:
            pred = (a + b) // 2
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
        out.append((x - pred) & 0xFF)
    return out


def test_png_roundtrip_and_korean_path(tmp_path):
    rng = np.random.default_rng(1)
    img = rng.integers(0, 256, size=(17, 23, 3), dtype=np.uint8)
    path = tmp_path / "한글 폴더" / "그림.png"
    path.parent.mkdir()
    vision.save_png(img, path)
    assert np.array_equal(vision.load_png(path), img)
    gray = rng.integers(0, 256, size=(5, 7), dtype=np.uint8)
    assert np.array_equal(vision.decode_png(vision.encode_png(gray)), np.repeat(gray[:, :, None], 3, axis=2))
    import base64
    assert base64.b64decode(vision.png_base64(img)).startswith(vision._PNG_SIG)


@pytest.mark.parametrize("ch,color", [(3, 2), (4, 6), (1, 0), (2, 4)])
def test_png_decode_all_filters(ch, color):
    rng = np.random.default_rng(ch)
    h, w = 10, 9
    px = rng.integers(0, 256, size=(h, w, ch), dtype=np.uint8)
    flat = px.reshape(h, -1).tolist()
    filters = [y % 5 for y in range(h)]
    prev, rows = [0] * len(flat[0]), []
    for ft, cur in zip(filters, flat):
        rows.append(_filter_row(ft, cur, prev, ch))
        prev = cur
    img = vision.decode_png(_png(w, h, color, 8, rows, filters))
    rgb = px[:, :, :3] if ch >= 3 else np.repeat(px[:, :, :1], 3, axis=2)
    assert np.array_equal(img, rgb[:, :, ::-1])                            # 투명도 버리고 BGR


def test_png_decode_palette_16bit_and_errors():
    plte = [10, 20, 30, 200, 100, 0, 1, 2, 3]
    rows = [[0b00011000]]                                                  # 2비트 팔레트: 0,1,2,0
    img = vision.decode_png(_png(4, 1, 3, 2, rows, [0], plte))
    assert img[0].tolist() == [[30, 20, 10], [0, 100, 200], [3, 2, 1], [30, 20, 10]]
    rows16 = [[0x12, 0x34, 0xAB, 0xCD, 0x00, 0xFF]]                         # 16비트 RGB -> 상위 바이트
    assert vision.decode_png(_png(1, 1, 2, 16, rows16, [0]))[0, 0].tolist() == [0x00, 0xAB, 0x12]
    for bad in [b"not png", vision._PNG_SIG, _png(4, 1, 3, 2, rows, [0])[:-20]]:
        with pytest.raises(ValueError):
            vision.decode_png(bad)


def _cv_reference(img, tpl):
    """OpenCV TM_CCOEFF_NORMED 정의를 그대로 계산 (느린 기준값)."""
    img, tpl = img.astype(np.float64), tpl.astype(np.float64)
    h, w = tpl.shape[:2]
    t = tpl - tpl.mean(axis=(0, 1))
    out = np.zeros((img.shape[0] - h + 1, img.shape[1] - w + 1))
    for y in range(out.shape[0]):
        for x in range(out.shape[1]):
            win = img[y:y + h, x:x + w]
            i = win - win.mean(axis=(0, 1))
            d = np.sqrt((t * t).sum() * (i * i).sum())
            out[y, x] = (t * i).sum() / d if d else 0
    return out


def test_match_template_equals_definition():
    rng = np.random.default_rng(2)
    img = rng.integers(0, 256, size=(23, 31, 3), dtype=np.uint8)
    tpl = img[5:12, 9:20].copy()
    tpl[0, 0] = (0, 0, 0)
    res = vision.match_template(img, tpl)
    assert res.shape == (17, 21)
    assert np.allclose(res, _cv_reference(img, tpl), atol=1e-9)
    assert np.unravel_index(np.argmax(res), res.shape) == (5, 9)
    flat = np.full((10, 10, 3), 7, dtype=np.uint8)                         # 단색 영역: 분모 0 -> 0
    assert not vision.match_template(flat, tpl[:3, :3]).any()


def _scenes():
    """(화면, 템플릿) 쌍: 잡음, 실제 UI 같은 단순 도형, 밝기 변화, 단색 구간 포함."""
    rng = np.random.default_rng(3)
    screen, btn = make_screen()
    yield screen, btn
    yield screen, btn[1:28, 2:47]
    yield screen, btn[5:25, 5:45]                                          # 대각선 방향으로 똑같은 위치가 여럿
    dim = screen.copy()
    dim[200:230, 300:350] = (btn * 0.8).astype(np.uint8)                   # 어두워진 버튼
    yield dim, btn
    ui = np.full((240, 320, 3), 240, dtype=np.uint8)                       # 단색 배경 위 아이콘들
    for k, (y, x) in enumerate([(20, 30), (100, 200), (180, 60)]):
        ui[y:y + 24, x:x + 24] = (40 * k, 120, 255 - 60 * k)
        ui[y + 8:y + 16, x + 4:x + 20] = 255
    yield ui, ui[100:124, 200:224].copy()
    big = rng.integers(0, 256, size=(360, 640, 3), dtype=np.uint8)
    yield big, big[123:163, 456:520].copy()
    yield big, rng.integers(0, 256, size=(40, 40, 3), dtype=np.uint8)     # 없는 이미지


def test_match_template_score_same_as_opencv():
    """OpenCV 결과(점수·위치)와 비교 — opencv 가 설치된 개발 환경에서만 실행."""
    cv2 = pytest.importorskip("cv2")
    for screen, tpl in _scenes():
        ours = vision.match_template(screen, tpl)
        ref = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
        assert ours.shape == ref.shape
        assert np.abs(ours - ref).max() < 1e-4                              # 맵 전체가 같다
        _, score, _, loc = cv2.minMaxLoc(ref)
        oy, ox = np.unravel_index(np.argmax(ours), ours.shape)
        assert abs(ours[oy, ox] - score) < 1e-4
        assert abs(ours[loc[1], loc[0]] - score) < 1e-4                     # 같은 점수 위치가 여럿이면 어느 쪽이든
        if score > 0.5 and (ref >= score - 1e-4).sum() == 1:
            assert (ox, oy) == loc


def test_png_same_as_opencv(tmp_path):
    cv2 = pytest.importorskip("cv2")
    screen, _ = make_screen()
    ours = vision.encode_png(screen)
    assert np.array_equal(cv2.imdecode(np.frombuffer(ours, np.uint8), cv2.IMREAD_COLOR), screen)
    for img in [screen, cv2.cvtColor(screen, cv2.COLOR_BGR2BGRA), cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)]:
        ok, buf = cv2.imencode(".png", img)                                # OpenCV 가 만든 PNG (필터 섞임)
        assert np.array_equal(vision.decode_png(buf.tobytes()), cv2.imdecode(buf, cv2.IMREAD_COLOR))


def test_vision_does_not_import_opencv():
    """exe 용량 때문에 OpenCV 를 쓰지 않는다 (개발 환경에 설치돼 있어도 불러오지 않아야 한다)."""
    import subprocess
    import sys
    code = ("import sys, numpy as np, vision\n"
            "img = np.zeros((20, 20, 3), np.uint8); img[5:9, 5:9] = 255\n"
            "vision.decode_png(vision.encode_png(img)); vision.match_template(img, img[3:11, 3:11])\n"
            "assert 'cv2' not in sys.modules\n")
    root = str(__import__("pathlib").Path(vision.__file__).parent)
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)
