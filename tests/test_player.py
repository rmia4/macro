import random

import pytest

from fakes import FakeBackend, FakeClock
from player import PlayOptions, Player, set_option
from profiles import Macro


def build(events, **opt):
    clock = FakeClock()
    be = FakeBackend(clock)
    opt.setdefault("approach", False)
    opt.setdefault("min_key_hold", 0.0)
    p = Player(be, PlayOptions(**opt), clock=clock, waiter=clock.wait, rng=random.Random(1))
    return p, be, clock, Macro(events=events)


def key(t, typ, k="a"):
    return {"t": t, "type": typ, "key": k}


def mv(t, x, y):
    return {"t": t, "type": "move", "x": x, "y": y}


def btn(t, typ, x=10, y=10):
    return {"t": t, "type": typ, "x": x, "y": y, "button": "left"}


def test_timing_and_speed():
    p, be, clock, m = build([key(0.5, "kdown"), key(1.0, "kup")], speed=2.0)
    p.run(m)
    assert [round(c[0], 3) for c in be.calls] == [0.25, 0.5]


def test_repeat_and_loop_delay():
    p, be, clock, m = build([key(0.1, "kdown"), key(0.2, "kup")], repeat=3, loop_delay=1.0)
    p.run(m)
    downs = [c[0] for c in be.calls if c[1] == "kdown"]
    assert len(downs) == 3
    assert round(downs[1] - downs[0], 3) == 1.2  # 0.2 재생 + 1.0 대기


def test_infinite_until_stop():
    p, be, clock, m = build([key(0.1, "kdown"), key(0.2, "kup")], repeat=0)
    clock.on_wait = lambda: p.stop() if clock.t > 5 else None
    p.run(m)
    assert 10 < len([c for c in be.calls if c[1] == "kdown"]) < 40
    assert be.calls[-1][1] == "kup"  # 중단 시에도 키가 해제됨


def test_stop_mid_hold_releases_everything():
    p, be, clock, m = build([key(0.1, "kdown"), btn(0.1, "mdown"), key(5, "kup")])
    clock.on_wait = lambda: p.stop() if clock.t >= 1 else None
    p.run(m)
    ns = be.names()
    assert ("kup", "a") in ns and ("mup", "left") in ns


def test_exception_releases():
    p, be, clock, m = build([key(0.1, "kdown"), btn(0.2, "mdown"), key(0.3, "kup")])
    orig = be.mouse_down

    def boom(b):
        orig(b)
        raise OSError("x")
    be.mouse_down = boom
    with pytest.raises(OSError):
        p.run(m)
    assert ("kup", "a") in be.names() and ("mup", "left") in be.names()


def test_release_failure_does_not_block_others():
    p, be, clock, m = build([key(0.1, "kdown", "a"), key(0.2, "kdown", "b"), btn(0.3, "mdown"),
                             key(5, "kup", "a")])
    clock.on_wait = lambda: p.stop() if clock.t >= 1 else None
    be.fail_on = "kup"
    p.run(m)
    assert ("mup", "left") in be.names()


def test_time_jitter_bounds_and_variation():
    evs = [key(i * 1.0, "kdown", "a") if i % 2 == 1 else key(i * 1.0, "kup", "a") for i in range(1, 21)]
    p, be, clock, m = build(evs, time_jitter=20)
    p.run(m)
    gaps = [b[0] - a[0] for a, b in zip(be.calls, be.calls[1:])]
    assert all(0.8 - 1e-9 <= g <= 1.2 + 1e-9 for g in gaps)
    assert len({round(g, 4) for g in gaps}) > 5


def test_pos_jitter_constant_during_drag():
    evs = [mv(0, 100, 100), btn(0.1, "mdown", 100, 100), mv(0.2, 150, 150), mv(0.3, 200, 200),
           btn(0.4, "mup", 200, 200)]
    p, be, clock, m = build(evs, pos_jitter=10)
    p.run(m)
    pts = [c[2:] for c in be.calls if c[1] == "abs"]
    offs = [(x - ex, y - ey) for (x, y), (ex, ey) in zip(pts, [(100, 100), (150, 150), (200, 200)])]
    assert len(set(offs)) == 1
    assert all(abs(d) <= 10 for o in offs for d in o)


def test_pos_jitter_rerolled_between_clicks():
    evs = []
    for i in range(10):
        evs += [btn(i + 0.1, "mdown", 100, 100), btn(i + 0.2, "mup", 100, 100)]
    p, be, clock, m = build(evs, pos_jitter=10)
    p.run(m)
    assert len({c[2:] for c in be.calls if c[1] == "abs"}) > 3


def test_min_key_hold():
    p, be, clock, m = build([key(0.1, "kdown"), key(0.11, "kup")], min_key_hold=0.05)
    p.run(m)
    assert round(be.calls[1][0] - be.calls[0][0], 3) == 0.05


def test_focus_gating_blocks_until_focused():
    p, be, clock, m = build([key(0.1, "kdown"), key(0.2, "kup")], window_title="Game")
    be.title = "Browser"
    clock.on_wait = lambda: setattr(be, "title", "My Game") if clock.t > 2 else None
    p.run(m)
    assert be.calls[0][1] == "kdown" and be.calls[0][0] > 2
    assert round(be.calls[1][0] - be.calls[0][0], 3) == 0.1  # 일시정지 시간은 보정됨


def test_focus_never_regained_sends_nothing():
    p, be, clock, m = build([key(0.1, "kdown"), key(0.2, "kup")], window_title="Game")
    be.title = "Browser"
    clock.on_wait = lambda: p.stop() if clock.t > 1 else None
    p.run(m)
    assert be.calls == []


def test_approach_smooth_and_ends_at_target():
    p, be, clock, m = build([mv(0, 1000, 500)], approach=True)
    be.cursor = (0, 0)
    p.run(m)
    pts = [c[2:] for c in be.calls if c[1] == "abs"]
    assert len(pts) > 5 and pts[-1] == (1000, 500)
    xs = [x for x, _ in pts]
    assert xs == sorted(xs)
    steps = [b - a for a, b in zip(xs, xs[1:])]
    assert steps[len(steps) // 2] > steps[0] and steps[len(steps) // 2] > steps[-1]  # 가감속


def test_relative_mode_sends_deltas():
    evs = [mv(0, 100, 100), mv(0.1, 110, 95), btn(0.2, "mdown", 110, 95), mv(0.3, 120, 95),
           btn(0.4, "mup", 120, 95)]
    p, be, clock, m = build(evs, mouse_mode="relative", pos_jitter=50)
    p.run(m)
    assert be.names() == [("rel", 10, -5), ("mdown", "left"), ("rel", 10, 0), ("mup", "left")]


def test_window_space_mapping():
    clock = FakeClock()
    be = FakeBackend(clock)
    m = Macro(events=[mv(0, 10, 20)], coord_space="window",
              window={"title": "Game", "width": 640, "height": 360})
    Player(be, PlayOptions(approach=False, scale_coords=True), clock=clock, waiter=clock.wait).run(m)
    assert be.names() == [("abs", 100 + 20, 50 + 40)]  # 창이 2배 크기


def test_window_missing_aborts():
    clock = FakeClock()
    be = FakeBackend(clock)
    be.find_window_rect = lambda t: None
    m = Macro(events=[key(0, "kdown")], coord_space="window", window={"title": "Game"})
    Player(be, PlayOptions(), clock=clock, waiter=clock.wait).run(m)
    assert be.calls == []


def test_unknown_key_rejected_before_any_input():
    p, be, clock, m = build([key(0, "kdown", "a"), key(0.1, "kdown", "nonexistent")])
    with pytest.raises(ValueError):
        p.run(m)
    assert be.calls == []


def test_scroll_event():
    p, be, clock, m = build([{"t": 0, "type": "scroll", "x": 5, "y": 6, "dx": 1, "dy": -2}])
    p.run(m)
    assert be.names() == [("abs", 5, 6), ("scroll", 1, -2)]


def test_set_option():
    o = PlayOptions()
    set_option(o, "repeat", "0")
    set_option(o, "speed", "1.5")
    set_option(o, "approach", "off")
    set_option(o, "window_title", "Elden Ring")
    set_option(o, "mouse_mode", "relative")
    assert (o.repeat, o.speed, o.approach, o.window_title, o.mouse_mode) == (0, 1.5, False, "Elden Ring", "relative")
    for name, val in [("speed", "0"), ("time_jitter", "101"), ("nope", "1"),
                      ("mouse_mode", "x"), ("approach", "maybe"), ("repeat", "abc")]:
        with pytest.raises(ValueError):
            set_option(o, name, val)


def test_progress_indices():
    p, be, clock, m = build([key(0.1, "kdown"), key(0.2, "kup")], repeat=3)
    seen = []
    clock.on_wait = lambda: seen.append((p.loop_index, p.event_index))
    p.run(m)
    assert (p.loop_index, p.event_index) == (3, 1)
    assert (2, -1) in seen and (1, 0) in seen


def test_buttons_without_coordinates_use_current_cursor():
    evs = [{"t": 0.1, "type": "mdown", "button": "left"}, {"t": 0.2, "type": "mup", "button": "left"},
           {"t": 0.3, "type": "scroll", "dx": 0, "dy": -1}]
    p, be, clock, m = build(evs, approach=True, pos_jitter=5)
    be.cursor = (333, 444)
    p.run(m)
    assert be.names() == [("mdown", "left"), ("mup", "left"), ("scroll", 0, -1)]  # 커서 이동 없음


def test_max_minutes_stops_infinite_loop():
    p, be, clock, m = build([key(0.5, "kdown"), key(1.0, "kup")], repeat=0, max_minutes=0.1)  # 6초
    logs = []
    p._log = logs.append
    p.run(m)
    assert 5.99 <= clock.t <= 6.0 + 1e-9
    assert be.calls[-1][1] == "kup"  # 멈출 때 눌린 키 해제
    assert any("최대 실행 시간" in s for s in logs)


def test_max_minutes_zero_means_unlimited():
    p, be, clock, m = build([key(30, "kdown"), key(60, "kup")], repeat=3, max_minutes=0)
    p.run(m)
    assert len([c for c in be.calls if c[1] == "kdown"]) == 3


def test_rmove_sends_relative_deltas_in_any_mode():
    evs = [mv(0, 100, 100), {"t": 0.1, "type": "rmove", "dx": 5, "dy": -3},
           {"t": 0.2, "type": "rmove", "dx": 0, "dy": 0}, mv(0.3, 100, 100)]
    p, be, clock, m = build(evs)
    be.cursor = (100, 100)
    p.run(m)
    # rmove 뒤에는 커서 위치를 모르므로 같은 좌표라도 절대 이동을 다시 보낸다
    assert be.names() == [("rel", 5, -3), ("abs", 100, 100)]


def test_repeat_block_runs_count_times_with_timing():
    evs = [key(0.1, "kdown", "a"), key(0.2, "kup", "a"),
           {"t": 0.3, "type": "repeat_start", "count": 3},
           key(0.4, "kdown", "b"), key(0.5, "kup", "b"),
           {"t": 0.6, "type": "repeat_end"},
           key(0.7, "kdown", "c"), key(0.8, "kup", "c")]
    p, be, clock, m = build(evs)
    p.run(m)
    assert [c[2] for c in be.calls if c[1] == "kdown"] == ["a", "b", "b", "b", "c"]
    b_times = [round(c[0], 3) for c in be.calls if c[1] == "kdown" and c[2] == "b"]
    # 반복 간격 = 끝 표시까지(0.1) + 첫 이벤트 지연(0.1) + 구간 길이(0.1) = 0.3초
    assert b_times == [0.4, 0.7, 1.0]
    assert round(be.calls[-1][0], 3) == 1.4


def test_nested_repeat_blocks():
    evs = [{"t": 0, "type": "repeat_start", "count": 2},
           key(0.1, "kdown", "x"), key(0.11, "kup", "x"),
           {"t": 0.12, "type": "repeat_start", "count": 3},
           key(0.13, "kdown", "y"), key(0.14, "kup", "y"),
           {"t": 0.15, "type": "repeat_end"},
           {"t": 0.16, "type": "repeat_end"}]
    p, be, clock, m = build(evs, repeat=2)
    p.run(m)
    seq = "".join(c[2] for c in be.calls if c[1] == "kdown")
    assert seq == "xyyyxyyy" * 2  # 전체 반복마다 안쪽 반복 횟수도 처음부터


def test_stop_inside_repeat_block():
    evs = [{"t": 0, "type": "repeat_start", "count": 100000}, key(0.1, "kdown"), key(0.2, "kup"),
           {"t": 0.2, "type": "repeat_end"}]
    p, be, clock, m = build(evs, max_minutes=0.01)  # 0.6초
    p.run(m)
    assert 2 <= len([c for c in be.calls if c[1] == "kdown"]) <= 4


class ScriptedVision:
    """판정 결과를 순서대로 돌려주는 가짜 화면 인식."""
    def __init__(self, results):
        from vision import Match
        self.results = list(results)
        self.checks = []
        self.preloaded = None
        self.Match = Match

    def preload(self, conds):
        self.preloaded = conds

    def check(self, cond, origin=(0, 0)):
        self.checks.append(origin)
        ok = self.results.pop(0) if self.results else False
        return self.Match(ok, 1.0 if ok else 0.0)


PIX = {"kind": "pixel", "x": 1, "y": 1, "color": "#000000"}


def test_wait_until_shifts_following_timeline():
    evs = [key(0.1, "kdown", "a"), {"t": 0.2, "type": "wait_until", "cond": PIX, "timeout": 10},
           key(0.3, "kup", "a")]
    v = ScriptedVision([False] * 5 + [True])   # 6번째 확인에서 조건 충족 (0.1초 간격)
    p, be, clock, m = build(evs)
    p.vision = v
    p.run(m)
    assert v.preloaded == [PIX] and len(v.checks) == 6
    down, up = be.calls[0][0], be.calls[1][0]
    assert round(down, 3) == 0.1
    assert round(up - down, 3) == round(0.1 + 0.5 + 0.1, 3)   # 대기 0.5초만큼 뒤로 밀림, 간격 유지


def test_wait_until_timeout_stop_and_continue():
    evs = [{"t": 0, "type": "wait_until", "cond": PIX, "timeout": 1, "on_timeout": "stop"}, key(0.1, "kdown")]
    p, be, clock, m = build(evs, repeat=3)
    p.vision = ScriptedVision([])
    logs = []
    p._log = logs.append
    p.run(m)
    assert be.calls == [] and round(clock.t, 3) == 1.0 and any("시간 초과" in s for s in logs)
    evs[0] = dict(evs[0], on_timeout="continue")
    p, be, clock, m = build(evs)
    p.vision = ScriptedVision([])
    p.run(m)
    assert [c[1] for c in be.calls] == ["kdown", "kup"]   # 초과 후 계속 (끝에서 키 해제)


def test_wait_until_stop_request_and_window_origin():
    evs = [{"t": 0, "type": "wait_until", "cond": PIX, "timeout": 0}]   # 무제한
    p, be, clock, m = build(evs)
    v = ScriptedVision([])
    p.vision = v
    clock.on_wait = lambda: p.stop() if clock.t > 2 else None
    p.run(m)
    assert 20 <= len(v.checks) <= 22
    clock2 = FakeClock()
    be2 = FakeBackend(clock2)
    v2 = ScriptedVision([True])
    m2 = Macro(events=evs, coord_space="window", window={"title": "Game"})
    Player(be2, PlayOptions(approach=False), clock=clock2, waiter=clock2.wait, vision=v2).run(m2)
    assert v2.checks == [(100, 50)]   # 창 좌상단 기준으로 판정


def test_conditions_require_vision():
    evs = [{"t": 0, "type": "wait_until", "cond": PIX}, key(0.1, "kdown")]
    p, be, clock, m = build(evs)
    with pytest.raises(ValueError):
        p.run(m)
    assert be.calls == []
