import time

import pytest

tk = pytest.importorskip("tkinter")

from fakes import FakeBackend  # noqa: E402
from main import App  # noqa: E402
from profiles import Macro  # noqa: E402


@pytest.fixture
def gui(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("디스플레이 없음 (xvfb-run 으로 실행)")
    from gui import Gui
    app = App(FakeBackend(), macros_dir=tmp_path)
    g = Gui(root, app)
    yield g
    root.after_cancel(g._after_id)
    root.destroy()


def pump(g, n=2):
    for _ in range(n):
        time.sleep(0.06)  # _poll 주기(50ms)보다 길게
        g.root.update()


def test_options_roundtrip_and_validation(gui, monkeypatch):
    errors = []
    monkeypatch.setattr("gui.messagebox.showerror", lambda t, m: errors.append(m))
    gui._vars["repeat"].set("5")
    gui._vars["speed"].set("2.5")
    gui._vars["approach"].set(False)
    gui._vars["mouse_mode"].set("relative")
    gui._vars["window_title"].set("Elden Ring")
    assert gui.apply_options()
    o = gui.app.options
    assert (o.repeat, o.speed, o.approach, o.mouse_mode, o.window_title) == (5, 2.5, False, "relative", "Elden Ring")
    gui._vars["speed"].set("0")
    gui._vars["window_title"].set("")
    assert not gui.apply_options() and "speed" in errors[0]


def test_save_load_list(gui):
    gui.app.macro = Macro(events=[{"t": 0, "type": "move", "x": 1, "y": 1}])
    gui.name.set("demo")
    gui.on_save()
    assert gui.listbox.get(0) == "demo"
    gui.app.macro = None
    gui.listbox.selection_set(0)
    gui.on_load()
    assert gui.app.macro is not None
    pump(gui)
    assert "이벤트 1개" in gui.info.cget("text")


def test_status_and_log(gui):
    pump(gui)
    assert gui.status.cget("text") == "대기 중"
    gui.log("hello")
    pump(gui)
    assert "hello" in gui.log_box.get("1.0", "end")


def test_play_without_macro_logs(gui):
    gui.on_play(immediate=True)
    pump(gui)
    assert "재생할 매크로가 없습니다" in gui.log_box.get("1.0", "end")


def test_countdown_cancel(gui):
    gui.delay.set("5")
    gui.on_play()
    pump(gui)
    assert gui.status.cget("text") == "시작 대기 중…"
    gui.on_stop()
    pump(gui)
    assert gui.status.cget("text") == "대기 중"


def test_hotkey_calls_run_on_tk_thread(gui):
    hits = []
    gui.on_record = lambda immediate=False: hits.append(immediate)
    gui.hotkey_bindings()["f8"]()
    pump(gui, 2)
    assert hits == [True]
