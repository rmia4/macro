"""Windows 성능 측정. python performance_windows.py (결과: performance-results.json).

화면은 메모리에서만 캡처하고 저장하지 않는다. 실제 입력은 이동량 0의 SendInput만 측정한다.
"""
import ctypes
import json
import platform
import statistics
import sys
import threading
import time
from pathlib import Path

import numpy as np

import editor_model as em
import input_backend as ib
import vision
from player import Player, PlayOptions
from profiles import Macro

RESULTS = {}


def stats(values):
    return {"n": len(values), "median_ms": round(statistics.median(values), 3),
            "p95_ms": round(float(np.percentile(values, 95)), 3),
            "max_ms": round(max(values), 3)}


def measure(name, fn, n=15, warmup=2):
    if name in RESULTS:
        return
    for _ in range(warmup):
        fn()
    samples = []
    cpu = time.process_time()
    for _ in range(n):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    RESULTS[name] = stats(samples)
    RESULTS[name]["cpu_total_ms"] = round((time.process_time() - cpu) * 1000, 3)
    print(name, RESULTS[name], flush=True)


class SyntheticGrabber:
    def __init__(self, image):
        self.image = image

    def screen_size(self):
        return self.image.shape[1], self.image.shape[0]

    def grab(self, x, y, w, h):
        return self.image[y:y + h, x:x + w].copy()


class TimingBackend:
    def __init__(self):
        self.times = []

    def cursor_pos(self):
        return 0, 0

    def mouse_move_rel(self, dx, dy):
        self.times.append(time.perf_counter())


def main():
    if not ib.IS_WINDOWS:
        raise SystemExit("Windows에서 실행하세요")
    if "--resume" in sys.argv:
        RESULTS.update(json.loads(Path("performance-results.json").read_text(encoding="utf-8")))
    ib.init_process()
    dxgi = None
    try:
        cpu = ctypes.create_unicode_buffer(256)
        size = ctypes.c_ulong(ctypes.sizeof(cpu))
        ctypes.windll.advapi32.RegGetValueW(0x80000002,
            "HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0", "ProcessorNameString",
            2, None, cpu, ctypes.byref(size))
        RESULTS["environment"] = {"os": platform.platform(), "python": sys.version,
            "numpy": np.__version__, "cpu": cpu.value.strip(),
            "timer_resolution_requested_ms": 1, "local_time": time.strftime("%Y-%m-%d %H:%M:%S")}
        # 실제 Windows 캡처 경로. 정지 화면에서는 DXGI가 프레임을 재사용할 수 있다.
        dxgi = vision.DxgiGrabber()
        mss = vision.MssGrabber()
        sw, sh = mss.screen_size()
        RESULTS["environment"]["screen"] = [sw, sh]
        for label, grabber in (("dxgi", dxgi), ("mss", mss)):
            for w, h in ((1, 1), (320, 180), (sw, sh)):
                measure(f"capture_{label}_{w}x{h}", lambda g=grabber, w=w, h=h:
                        g.grab(0, 0, w, h), n=30)
        if "--resume" not in sys.argv:
            RESULTS["capture_status"] = {"dxgi_active": dxgi.active, "error": dxgi.error,
                                         "scene": "현재 데스크톱; 정지/갱신 프레임 혼합"}
        backend = ib.WindowsBackend()
        measure("sendinput_zero_move", lambda: backend.mouse_move_rel(0, 0), n=1000)

        # 알고리즘은 고정 시드 합성 화면으로 정답도 함께 검증.
        rng = np.random.default_rng(20261006)
        image = rng.integers(0, 256, (1080, 1920, 3), dtype=np.uint8)
        grabber = SyntheticGrabber(image)
        tpl = image[400:432, 700:748].copy()
        cond = {"kind": "image", "template": "synthetic.png", "threshold": .85}
        v = vision.Vision(None, grabber)
        v._templates[cond["template"]] = tpl

        def first_search():
            v._last_pos.clear()
            v._last_full.clear()
            m = v.check(cond)
            assert m.matched and m.pos == (724, 416), m

        measure("image_full_1080p_48x32_first_search", first_search, n=8)
        measure("image_same_position_48x32", lambda: v.check(cond), n=30)
        region = dict(cond, region=[600, 300, 320, 240])

        def region_search():
            v._last_pos.clear()
            v._last_full.clear()
            m = v.check(region)
            assert m.matched and m.pos == (724, 416), m

        measure("image_region_320x240_first_search", region_search)
        # 작은 템플릿은 피라미드 축소를 못 쓰는 경로.
        v._templates["small.png"] = image[400:412, 700:712].copy()
        small = dict(cond, template="small.png")

        def small_search():
            v._last_pos.clear()
            v._last_full.clear()
            m = v.check(small)
            assert m.matched and m.pos == (706, 406), m

        measure("image_full_1080p_12x12_first_search", small_search, n=5, warmup=1)
        absent = rng.integers(0, 256, tpl.shape, dtype=np.uint8)
        v._templates["absent.png"] = absent
        no_cond = dict(cond, template="absent.png")

        def absent_search():
            v._last_pos.clear()
            v._last_full.clear()
            assert not v.check(no_cond).matched

        measure("image_absent_full_first_search", absent_search, n=8)
        measure("image_absent_unchanged_cache", lambda: v.check(no_cond), n=30)
        pixel = {"kind": "pixel", "x": 0, "y": 0, "w": 320, "h": 180,
                 "color": "#ffffff", "tolerance": 20, "ratio": .5}
        live = vision.Vision(None, dxgi)
        measure("pixel_live_320x180", lambda: live.check(pixel), n=30)

        for n in (1000, 10000, 100000):
            events = [{"t": round(i * .01, 4), "type": "move", "x": i % 1920, "y": i % 1080}
                      for i in range(n)]
            data = Macro(events=events).to_dict()
            measure(f"validate_{n}_events", lambda: Macro.from_dict(data), n=5)

            def roundtrip():
                restored = em.to_events(em.to_items(events))
                assert restored == events

            measure(f"editor_roundtrip_{n}_events", roundtrip, n=5)
            zero = Macro(events=[{"t": 0., "type": "rmove", "dx": 1, "dy": 0}
                                 for _ in range(n)])

            def throughput():
                b = TimingBackend()
                Player(b, PlayOptions(approach=False)).run(zero)
                assert len(b.times) == n

            measure(f"player_no_input_{n}_events", throughput, n=5)

        # 실제 시계/Windows 대기, 입력은 보내지 않고 발행 시각만 기록.
        for interval in (.001, .01):
            lag, drift = [], []
            for _ in range(3):
                b = TimingBackend()
                p = Player(b, PlayOptions(approach=False))
                macro = Macro(events=[{"t": (i + 1) * interval, "type": "rmove", "dx": 1, "dy": 0}
                                      for i in range(300)])
                p.run(macro)
                assert len(b.times) == 300
                lag.extend((actual - (p._origin + ev["t"])) * 1000
                           for actual, ev in zip(b.times, macro.events))
                drift.append((b.times[-1] - p._origin - macro.duration) * 1000)
            RESULTS[f"player_timing_{interval * 1000:g}ms"] = stats(lag)
            RESULTS[f"player_timing_{interval * 1000:g}ms"]["end_lag_ms"] = drift
            print(f"player_timing_{interval * 1000:g}ms", RESULTS[f"player_timing_{interval * 1000:g}ms"], flush=True)

        stop_lag = []
        for _ in range(10):
            p = Player(TimingBackend(), PlayOptions(approach=False))
            thread = threading.Thread(target=p.run, args=(Macro(events=[{"t": 10., "type": "wait"}]),))
            thread.start()
            time.sleep(.02)
            start = time.perf_counter()
            p.stop()
            thread.join(1.)
            assert not thread.is_alive()
            stop_lag.append((time.perf_counter() - start) * 1000)
        RESULTS["player_stop_during_wait"] = stats(stop_lag)
    finally:
        if dxgi is not None and dxgi._dup is not None:
            dxgi._dup.close()
        ib.shutdown_process()
        Path("performance-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Results: performance-results.json", flush=True)


if __name__ == "__main__":
    main()
