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
