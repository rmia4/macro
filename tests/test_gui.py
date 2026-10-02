import time

import pytest

tk = pytest.importorskip("tkinter")

from fakes import FakeBackend, FakePlayer, FakeRecorder  # noqa: E402
from main import App  # noqa: E402
from profiles import Macro  # noqa: E402

EVENTS = [{"t": 0, "type": "move", "x": 1, "y": 1},
          {"t": 0.1, "type": "mdown", "x": 1, "y": 1, "button": "left"},
          {"t": 0.2, "type": "move", "x": 5, "y": 5},
          {"t": 0.3, "type": "mup", "x": 5, "y": 5, "button": "left"},
          {"t": 0.4, "type": "kdown", "key": "a"},
          {"t": 0.5, "type": "kup", "key": "a"}]


@pytest.fixture
def gui(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("디스플레이 없음 (xvfb-run 으로 실행)")
    import gui as gui_mod
    # 대화상자는 테스트에서 막히지 않게 기록만 한다
    shown = []
    monkeypatch.setattr(gui_mod.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(gui_mod.messagebox, "showerror", lambda *a, **k: shown.append(a))
    app = App(FakeBackend(), macros_dir=tmp_path,
              recorder_factory=FakeRecorder, player_factory=FakePlayer)
    app.store("alpha", Macro(events=list(EVENTS), hotkey="f6", options={"repeat": 0}))
    app.store("beta", Macro(events=list(EVENTS)))
    g = gui_mod.Gui(root, app)
    g.delay.set("0")
    g.shown = shown
    yield g
    app.stop_all()
    if g.editor is not None:
        g.editor.dirty = False
        g.editor.close()
    root.after_cancel(g._after_id)
    root.destroy()


def pump(g, n=2):
    for _ in range(n):
        time.sleep(0.06)  # _poll 주기(50ms)보다 길게
        g.root.update()


def logs(g):
    return g.log_box.get("1.0", "end")


# ---- 메인 화면 ----
def test_list_shows_library(gui):
    assert gui.tree.get_children() == ("alpha", "beta")
    assert gui.tree.item("alpha")["values"][1:4] == ["F6", "∞", 6]


def test_play_selected_and_stop(gui):
    gui.on_play()
    assert gui.shown  # 선택 없음 안내
    gui.tree.selection_set("beta")
    gui.on_play()
    pump(gui)
    assert gui.app.playing and gui.app.playing_name == "beta"
    assert gui.status.cget("text") == "▶ 재생 중"
    assert gui.progress_label.cget("text").startswith("beta · 루프 1/1 · 이벤트 1/6")
    gui.on_stop()
    gui.app._thread.join(1)
    pump(gui)
    assert not gui.app.playing and gui.status.cget("text") == "대기 중"


def test_macro_hotkey_toggles_that_macro(gui):
    b = gui.hotkey_bindings()
    assert {"f6", "f8", "f9", "f10"} <= set(b)
    b["f6"]()
    pump(gui)
    assert gui.app.playing_name == "alpha"
    assert gui.hotkey_suppressed("a") and not gui.hotkey_suppressed("f6")
    b["f6"]()
    pump(gui)
    gui.app._thread.join(1)
    assert not gui.app.playing


def test_countdown_cancel(gui):
    gui.delay.set("5")
    gui.tree.selection_set("beta")
    gui.on_play()
    pump(gui)
    assert gui.status.cget("text") == "시작 대기 중…"
    gui.on_stop()
    pump(gui)
    assert gui.status.cget("text") == "대기 중" and not gui.app.playing


def test_duplicate_and_delete(gui, monkeypatch, tmp_path):
    gui.tree.selection_set("alpha")
    gui.on_duplicate()
    assert "alpha 복사" in gui.app.library and gui.app.library["alpha 복사"].hotkey is None
    assert gui.selected() == "alpha 복사"
    monkeypatch.setattr("gui.messagebox.askyesno", lambda *a, **k: False)
    gui.on_delete()
    assert (tmp_path / "alpha 복사.json").exists()
    monkeypatch.setattr("gui.messagebox.askyesno", lambda *a, **k: True)
    gui.on_delete()
    assert not (tmp_path / "alpha 복사.json").exists()
    assert gui.tree.get_children() == ("alpha", "beta")


def test_hotkey_calls_run_on_tk_thread(gui):
    hits = []
    gui.toggle_macro = lambda n: hits.append(n)
    gui.hotkey_bindings()["f6"]()
    assert hits == []  # 아직 큐에만 있음
    pump(gui)
    assert hits == ["alpha"]


# ---- 기록 화면 ----
def test_add_opens_editor_and_records(gui):
    gui.on_add()
    ed = gui.editor
    assert ed is not None and ed.v_name.get() == "새 매크로"
    gui.on_add()  # 두 번째는 열리지 않음
    assert gui.editor is ed
    gui.hotkey_bindings()["f8"]()  # F8 = 녹화 시작
    pump(gui)
    assert gui.app.recording
    assert "f6" in FakeRecorder.last.ignore_keys and "f8" in FakeRecorder.last.ignore_keys
    gui.hotkey_bindings()["f8"]()  # 녹화 종료 -> 목록 끝에 추가
    pump(gui)
    assert not gui.app.recording and len(ed.items) == 3 and ed.dirty
    gui.hotkey_bindings()["f8"]()
    pump(gui)
    gui.hotkey_bindings()["f8"]()
    pump(gui)
    assert len(ed.items) == 6  # 두 번째 녹화도 이어 붙음
    assert str(ed.coord_box.cget("state")) == "disabled"  # 좌표 이벤트가 있으면 기준 변경 불가


def test_editor_insert_edit_delete_move(gui):
    gui.on_add()
    ed = gui.editor
    import editor_model as em
    ed.insert_items(em.build_items("tap", key="q"))
    ed.insert_items(em.build_items("wait", delay_ms=500))
    assert [i["type"] for i in ed.items] == ["kdown", "kup", "wait"]
    ed.tree.selection_set("0")  # 선택한 이벤트 뒤에 삽입
    ed.insert_items(em.build_items("click", x=3, y=4))
    assert [i["type"] for i in ed.items] == ["kdown", "mdown", "mup", "kup", "wait"]
    ed.tree.selection_set("4")
    ed.on_move(-1)
    assert [i["type"] for i in ed.items] == ["kdown", "mdown", "mup", "wait", "kup"]
    assert ed.selected_indices() == [3]
    ed.tree.selection_set(["1", "2"])
    ed.on_delete()
    assert [i["type"] for i in ed.items] == ["kdown", "wait", "kup"]
    assert ed.summary.cget("text") == "이벤트 3개 · 0.55초"


def test_editor_hide_moves_and_move_skips_hidden(gui):
    gui.tree.selection_set("beta")
    gui.on_edit()
    ed = gui.editor
    assert ed.v_name.get() == "beta" and not ed.dirty
    assert len(ed.tree.get_children()) == 4  # 이동 2개 숨김
    ed.tree.selection_set("3")  # mup -> 위로: 숨겨진 move 를 건너뛰고 mdown 앞으로
    ed.on_move(-1)
    assert [i["type"] for i in ed.items][:4] == ["move", "mup", "mdown", "move"]
    ed.hide_moves.set(False)
    ed.refresh_tree()
    assert len(ed.tree.get_children()) == 6


def test_editor_save_new_with_hotkey_and_options(gui, tmp_path):
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.v_name.set("채집")
    ed.v_hotkey.set("f7")
    ed.v_opts["repeat"].set("5")
    ed.v_title.set("My Game")
    assert ed.coord_space == "window"  # 창 제목을 넣으면 창 기준
    ed.insert_items(em.build_items("tap", key="e"))
    assert ed.on_save()
    m = gui.app.library["채집"]
    assert m.hotkey == "f7" and m.options["repeat"] == 5 and m.options["window_title"] == "My Game"
    assert (tmp_path / "채집.json").exists() and not ed.dirty
    assert "f7" in gui.hotkey_bindings()
    assert gui.tree.item("채집")["values"][1] == "F7"


def test_editor_save_validation_errors(gui):
    gui.on_add()
    ed = gui.editor
    ed.v_name.set("beta")       # 이미 있는 이름
    ed.v_hotkey.set("f6")       # alpha 가 사용 중
    assert not ed.on_save()     # 이벤트도 없음
    msg = gui.shown[-1][1]
    assert "같은 이름" in msg and "F6" in msg and "이벤트가 없" in msg
    import editor_model as em
    ed.v_name.set("gamma")
    ed.v_hotkey.set("(없음)")
    ed.v_opts["speed"].set("0")
    ed.insert_items(em.build_items("tap", key="e"))
    assert not ed.on_save() and "speed" in gui.shown[-1][1]


def test_editor_rename_existing(gui, tmp_path):
    gui.tree.selection_set("beta")
    gui.on_edit()
    ed = gui.editor
    ed.v_name.set("beta2")
    assert ed.on_save()
    assert "beta" not in gui.app.library and not (tmp_path / "beta.json").exists()
    assert ed.old_name == "beta2"


def test_editor_test_play(gui):
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.test_play()
    assert gui.shown and not gui.app.playing  # 이벤트 없음
    ed.insert_items(em.build_items("tap", key="e"))
    ed.test_play()
    pump(gui)
    assert gui.app.playing and gui.app.playing_name == "(편집 중) 새 매크로"
    assert ed.btn_test.cget("text") == "■ 테스트 중지"
    ed.test_play()
    gui.app._thread.join(1)
    assert not gui.app.playing


def test_editor_close_asks_when_dirty(gui, monkeypatch):
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.insert_items(em.build_items("tap", key="e"))
    monkeypatch.setattr("gui.messagebox.askyesnocancel", lambda *a, **k: None)
    assert not ed.close() and gui.editor is ed       # 취소
    monkeypatch.setattr("gui.messagebox.askyesnocancel", lambda *a, **k: False)
    assert ed.close() and gui.editor is None          # 저장 안 하고 닫기
    assert "새 매크로" not in gui.app.library


def test_editor_pick_position_window_space(gui):
    gui.on_add()
    ed = gui.editor
    gui.app.backend.cursor = (150, 90)
    assert ed.pick_position() == (150, 90)  # 화면 기준
    ed.v_title.set("Game")                   # rect = (100, 50, ...)
    assert ed.pick_position() == (50, 40)


# ---- 이벤트 대화상자 ----
def test_event_dialog_add_and_edit(gui):
    import editor_model as em
    from gui import EventDialog
    gui.app.backend.cursor = (7, 8)
    d = EventDialog(gui.root, em.ADD_KINDS, kind="click", pick=lambda: gui.app.backend.cursor)
    assert (d.v["x"].get(), d.v["y"].get()) == ("7", "8")  # 현재 커서 위치로 시작
    assert d.v["hold_ms"].get() == "60"
    d.v["button"].set("right")
    d._on_ok()
    assert [i["type"] for i in d.result] == ["mdown", "mup"] and d.result[0]["button"] == "right"

    d = EventDialog(gui.root, em.ADD_KINDS, kind="tap")
    d._on_ok()  # 키 미입력
    assert d.result is None and "알 수 없는 키" in d.error.cget("text")
    d.v["key"].set("space")
    d._on_ok()
    assert d.result[0] == {"type": "kdown", "key": "space", "dt": 0.1}

    item = {"type": "kdown", "key": "w", "dt": 0.25}
    d = EventDialog(gui.root, em.EDIT_KINDS, item=item)
    assert d.v["delay_ms"].get() == "250" and d.kind == "kdown"
    d.v["key"].set("s")
    d._on_ok()
    assert d.result == [{"type": "kdown", "key": "s", "dt": 0.25}]


def test_event_dialog_fields_follow_kind(gui):
    import editor_model as em
    from gui import EventDialog
    d = EventDialog(gui.root, em.ADD_KINDS, kind="wait")
    gui.root.update()
    assert not d.rows["key"][0].winfo_ismapped() and d.delay_label.cget("text") == "지연(ms)"
    d.v_kind.set("키 입력 (누르고 떼기)")
    gui.root.update()
    assert d.rows["key"][0].winfo_ismapped() and not d.rows["pos"][0].winfo_ismapped()
    d.top.destroy()
