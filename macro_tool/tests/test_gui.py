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


EVENTS = [{"t": 0, "type": "move", "x": 1, "y": 1},
          {"t": 0.1, "type": "mdown", "x": 1, "y": 1, "button": "left"},
          {"t": 0.2, "type": "move", "x": 5, "y": 5},
          {"t": 0.3, "type": "mup", "x": 5, "y": 5, "button": "left"},
          {"t": 0.4, "type": "kdown", "key": "a"}]


def test_save_and_open_playback_window(gui):
    gui.app.macro = Macro(events=list(EVENTS))
    gui.name.set("demo")
    gui.on_save()
    assert gui.listbox.get(0) == "demo" and gui.app.macro_name == "demo"
    gui.app.macro = None
    gui.listbox.selection_set(0)
    gui.open_playback()
    assert gui.app.macro is not None and gui.playback is not None
    pb = gui.playback
    assert pb.top.title() == "재생 - demo"
    assert len(pb.tree.get_children()) == 3  # 이동 숨김
    pb.hide_moves.set(False)
    pb.load_macro()
    assert len(pb.tree.get_children()) == 5
    pump(gui)
    assert "demo" in gui.info.cget("text")
    pb.close()
    assert gui.playback is None


def test_open_playback_without_macro(gui, monkeypatch):
    shown = []
    monkeypatch.setattr("gui.messagebox.showinfo", lambda t, m: shown.append(m))
    gui.open_playback()
    assert gui.playback is None and shown


def test_delete_macro(gui, monkeypatch, tmp_path):
    gui.app.macro = Macro(events=list(EVENTS))
    gui.name.set("gone")
    gui.on_save()
    gui.listbox.selection_set(0)
    monkeypatch.setattr("gui.messagebox.askyesno", lambda t, m: False)
    gui.on_delete()
    assert (tmp_path / "gone.json").exists()
    monkeypatch.setattr("gui.messagebox.askyesno", lambda t, m: True)
    gui.on_delete()
    assert not (tmp_path / "gone.json").exists()
    assert gui.listbox.size() == 0 and gui.app.macro_name is None
    assert gui.app.macro is not None  # 메모리의 매크로는 유지


def test_playback_progress_display(gui, monkeypatch):
    gui.app.macro = Macro(events=list(EVENTS))
    gui.open_playback()
    monkeypatch.setattr(App, "progress", property(lambda s: (2, 3, 5)))
    monkeypatch.setattr(App, "playing", property(lambda s: True))
    pump(gui)
    pb = gui.playback
    assert pb.state_label.cget("text").startswith("루프 2/5 · 이벤트 4/5")
    assert pb.progress["value"] == 80
    assert pb.tree.selection() == ("3",)
    assert gui.status.cget("text") == "▶ 재생 중"


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


def test_describe():
    from gui import describe
    assert describe({"type": "kdown", "key": "w"}) == "w"
    assert describe({"type": "mdown", "x": 1, "y": 2, "button": "left"}) == "left (1, 2)"
    assert describe({"type": "scroll", "x": 1, "y": 2, "dx": 0, "dy": -1}) == "dx=0 dy=-1 (1, 2)"
