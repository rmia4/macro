from recorder import RecorderCore


def make(**kw):
    c = RecorderCore(**kw)
    c.start(10.0, (500, 400))
    return c


def test_initial_move_and_lead_normalization():
    c = make(ignore_keys={"f8"})
    c.on_key("a", True, 15.0)   # 5초 뒤 첫 입력
    c.on_key("a", False, 15.1)
    ev = c.finish(16.0)
    assert ev[0] == {"t": 0.0, "type": "move", "x": 500, "y": 400}
    assert ev[1]["t"] == 0.2 and ev[1]["type"] == "kdown"
    assert abs(ev[2]["t"] - 0.3) < 1e-9


def test_autorepeat_filtered_and_ignore_keys():
    c = make(ignore_keys={"f8"})
    for t in (10.1, 10.2, 10.3):
        c.on_key("w", True, t)
    c.on_key("w", False, 10.4)
    c.on_key("f8", True, 10.5)
    c.on_key("f8", False, 10.6)
    types = [(e["type"], e.get("key")) for e in c.finish(11.0)[1:]]
    assert types == [("kdown", "w"), ("kup", "w")]


def test_move_sampling_throttle_and_pending_flush():
    c = make()
    c.on_move(501, 400, 10.100)   # emit
    c.on_move(502, 400, 10.103)   # pending
    c.on_move(503, 400, 10.105)   # pending 갱신
    c.on_click(503, 400, "left", True, 10.106)  # pending 먼저 flush
    ev = c.finish(10.2)
    moves = [e for e in ev if e["type"] == "move"]
    assert [m["x"] for m in moves] == [500, 501, 503]
    assert [e["type"] for e in ev][-1] == "mdown" or ev[-1]["type"] == "mup"


def test_pending_move_flushed_on_finish():
    c = make()
    c.on_move(501, 400, 10.100)
    c.on_move(509, 400, 10.102)
    assert c.finish(10.2)[-1]["x"] == 509


def test_duplicate_position_skipped():
    c = make()
    c.on_move(500, 400, 10.1)
    assert len(c.finish(10.2)) == 1


def test_held_inputs_released_at_finish():
    c = make()
    c.on_click(500, 400, "left", True, 10.1)
    c.on_key("shift", True, 10.1)
    ev = c.finish(11.0)
    assert {e["type"] for e in ev[-2:]} == {"mup", "kup"}
    assert ev[-1]["t"] > ev[-3]["t"]


def test_up_without_down_ignored():
    c = make()
    c.on_click(0, 0, "left", False, 10.1)
    c.on_key("a", False, 10.1)
    assert len(c.finish(10.2)) == 1


def test_scroll_and_window_origin():
    c = make(origin=(100, 50))
    c.on_scroll(600, 450, 0, -1, 10.1)
    ev = c.finish(10.2)
    assert ev[0]["x"] == 400 and ev[0]["y"] == 350
    assert ev[1] == {"t": 0.1, "type": "scroll", "x": 500, "y": 400, "dx": 0, "dy": -1}


def test_relative_mode_records_raw_deltas_and_buttons_without_coords():
    c = RecorderCore(relative=True)
    c.start(10.0, (500, 400))
    c.on_move(600, 400, 10.05)              # 커서 좌표 이동은 무시
    c.on_raw_move(5, -2, 10.100)            # 첫 이동은 즉시 기록
    c.on_raw_move(3, 1, 10.103)             # 10ms 안: 합산 대기
    c.on_raw_move(2, 1, 10.105)
    c.on_click(0, 0, "left", True, 10.106)  # 대기 중 이동량 먼저 기록
    c.on_raw_move(0, 0, 10.107)             # 0 이동은 무시
    c.on_scroll(0, 0, 0, -1, 10.2)
    c.on_raw_move(7, 7, 10.3)
    ev = c.finish(10.4)                     # 눌린 버튼은 좌표 없이 해제
    types = [(e["type"], e.get("dx"), e.get("dy")) for e in ev]
    assert types == [("rmove", 5, -2), ("rmove", 5, 2), ("mdown", None, None), ("scroll", 0, -1),
                     ("rmove", 7, 7), ("mup", None, None)]
    assert all("x" not in e for e in ev)
    assert ev[0]["t"] == 0.1  # 첫 입력이 정규화 기준(0.2초)보다 빠르면 그대로


def test_recorder_serializes_listener_threads(monkeypatch):
    """마우스·Raw Input 리스너가 동시에 불러도 상대 이동량이 사라지거나 두 번 기록되지 않는다."""
    import sys
    import threading
    import time
    import types

    import input_backend
    from fakes import FakeBackend
    from recorder import Recorder

    class Listener:
        def __init__(self, **kw):
            self.kw = kw
            made.append(self)

        def start(self): pass
        def stop(self): pass

    class Raw:
        def __init__(self, cb):
            made.append(cb)

        def start(self): pass
        def stop(self): pass

    made = []
    pynput = types.ModuleType("pynput")
    pynput.mouse = types.SimpleNamespace(Listener=Listener)
    pynput.keyboard = types.SimpleNamespace(Listener=Listener)
    monkeypatch.setitem(sys.modules, "pynput", pynput)
    monkeypatch.setattr(input_backend, "RawMouseListener", Raw)
    rec = Recorder(FakeBackend(), relative=True, clock=time.perf_counter)
    rec.start()
    raw, mouse = made[0], made[1].kw
    left = types.SimpleNamespace(name="left")

    def move():
        for _ in range(20000):
            raw(1, 0)

    def click():
        for i in range(2000):
            mouse["on_click"](0, 0, left, i % 2 == 0)

    threads = [threading.Thread(target=move), threading.Thread(target=click)]
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # 스레드를 자주 바꿔 경쟁이 드러나게
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)
    ev = rec.stop().events
    assert sum(e["dx"] for e in ev if e["type"] == "rmove") == 20000
    assert [e["t"] for e in ev] == sorted(e["t"] for e in ev)


def test_recorder_start_failure_cleans_up(monkeypatch):
    """키보드 리스너 시작이 실패하면 이미 시작한 리스너를 멈추고 녹화 상태를 되돌린다."""
    import sys
    import types

    import pytest
    from fakes import FakeBackend
    from recorder import Recorder

    stopped = []

    class Mouse:
        def __init__(self, **kw): pass
        def start(self): pass
        def stop(self): stopped.append("mouse")

    class Keyboard(Mouse):
        def start(self): raise RuntimeError("hook failed")
        def stop(self): stopped.append("keyboard")

    pynput = types.ModuleType("pynput")
    pynput.mouse = types.SimpleNamespace(Listener=Mouse)
    pynput.keyboard = types.SimpleNamespace(Listener=Keyboard)
    monkeypatch.setitem(sys.modules, "pynput", pynput)
    rec = Recorder(FakeBackend())
    with pytest.raises(RuntimeError):
        rec.start()
    assert not rec.recording and stopped == ["mouse"]  # 시작하지 못한 키보드 리스너는 멈추지 않는다
    assert rec._listeners == []
