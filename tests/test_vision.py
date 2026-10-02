import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import vision  # noqa: E402
from vision import Vision, validate_condition  # noqa: E402


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
    ok, buf = cv2.imencode(".png", btn)
    buf.tofile(str(assets / "확인버튼.png"))
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
        "픽셀 (1, 2) = #00ff00 ±20 아님"
