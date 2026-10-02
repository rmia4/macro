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
    assert gui.tree.item("alpha")["values"][:4] == ["alpha", "F6", "∞", 6]


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
    assert gui.status.cget("text") == "5초 후 재생"
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
    assert ed.banner.cget("text").startswith("● 녹화 중")      # 기록 화면에 표시
    assert gui.status.cget("text") == "대기 중"                 # 메인에는 표시 안 함
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
    assert ed.summary.cget("text") == "항목 3개 (이벤트 3개) · 0.55초"


def test_editor_groups_moves_and_edits_path(gui):
    gui.tree.selection_set("beta")
    gui.on_edit()
    ed = gui.editor
    assert ed.v_name.get() == "beta" and not ed.dirty
    # move, mdown, move, mup, kdown, kup -> 이동이 연속되지 않으므로 그대로 6줄
    assert len(ed.tree.get_children()) == 6
    ed.items = [{"type": "path", "dt": 0.0, "points": [[0.0, 0, 0], [0.01, 5, 5], [0.01, 9, 9]]}] + ed.items[1:]
    ed.refresh_tree()
    assert ed.tree.item("0")["values"][2] == "마우스 이동 경로"
    assert ed.summary.cget("text").startswith("항목 6개 (이벤트 8개)")
    ed.tree.selection_set("0")
    ed.on_move(1)
    assert ed.items[1]["type"] == "path" and ed.selected_indices() == [1]


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
    assert gui.editor is None  # 저장하면 창이 닫힌다
    m = gui.app.library["채집"]
    assert m.hotkey == "f7" and m.options["repeat"] == 5 and m.options["window_title"] == "My Game"
    assert (tmp_path / "채집.json").exists()
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
    assert "beta2" in gui.app.library and gui.selected() == "beta2"


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


def test_no_selection_does_nothing(gui):
    gui.tree.selection_remove(gui.tree.selection())
    for fn in (gui.on_edit, gui.on_duplicate, gui.on_delete):
        fn()
    assert gui.shown == [] and gui.editor is None
    assert set(gui.app.library) == {"alpha", "beta"}
    gui.on_add()
    ed = gui.editor
    import editor_model as em
    ed.insert_items(em.build_items("tap", key="q"))
    ed.tree.selection_remove(ed.tree.selection())
    before = list(ed.items)
    for fn in (ed.on_edit, ed.on_delete, lambda: ed.on_move(-1), lambda: ed.on_move(1)):
        fn()
    assert gui.shown == [] and ed.items == before


def test_event_dialog_cursor_option_and_path(gui):
    import editor_model as em
    from gui import EventDialog
    d = EventDialog(gui.root, em.ADD_KINDS, kind="click")
    d.v_cursor.set(True)
    d._on_kind()
    gui.root.update()
    assert not d.rows["pos"][0].winfo_ismapped() and d.rows["cursor"][0].winfo_ismapped()
    d._on_ok()
    assert [("x" in i) for i in d.result] == [False, False]

    item = {"type": "mup", "button": "left", "dt": 0.05}
    d = EventDialog(gui.root, em.EDIT_KINDS, item=item)
    assert d.v_cursor.get() is True

    path = {"type": "path", "dt": 0.0, "points": [[0.0, 0, 0], [0.02, 10, 10]]}
    d = EventDialog(gui.root, em.PATH_KINDS, item=path)
    gui.root.update()
    assert d.v["duration_ms"].get() == "20" and d.rows["pos"][0].cget("text") == "끝 위치 X, Y"
    d.v["x"].set("30")
    d._on_ok()
    assert d.result[0]["points"][-1] == [0.02, 30, 10]


def test_editor_record_button_countdown_shown_in_editor(gui):
    gui.on_add()
    ed = gui.editor
    gui.delay.set("3")
    ed.toggle_record()
    pump(gui)
    assert ed.banner.cget("text").startswith("3초 후 녹화 시작")
    assert gui.status.cget("text") == "대기 중" and "초 후" not in logs(gui)
    ed.toggle_record()  # 카운트다운 중 다시 누르면 취소
    pump(gui)
    assert ed.banner.cget("text") == "대기" and not gui.app.recording


def test_global_enable_toggle(gui):
    gui.tree.selection_set("beta")
    gui.toggle_macros_enabled()
    assert not gui.macros_enabled and "실행 불가" in gui.btn_power.cget("text")
    gui.on_play(immediate=True)
    gui.hotkey_bindings()["f6"]()   # 매크로 핫키도 무시
    pump(gui)
    assert not gui.app.playing and "실행이 꺼져" in logs(gui)
    gui.toggle_macros_enabled()
    gui.on_play(immediate=True)
    pump(gui)
    assert gui.app.playing
    gui.toggle_macros_enabled()     # 끄면 재생 중인 매크로도 멈춤
    gui.app._thread.join(1)
    assert not gui.app.playing


def test_editor_combo_hotkey_save_and_binding(gui):
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.v_name.set("combo")
    ed.v_hotkey.set("F2 + Ctrl")
    ed.insert_items(em.build_items("tap", key="e"))
    assert ed.on_save()
    assert gui.app.library["combo"].hotkey == "ctrl+f2"
    assert "ctrl+f2" in gui.hotkey_bindings()
    assert gui.tree.item("combo")["values"][1] == "CTRL+F2"


def test_hotkey_capture_dialog(gui):
    from gui import HotkeyCaptureDialog

    class Ev:
        def __init__(self, keysym):
            self.keysym, self.keycode = keysym, 0
    d = HotkeyCaptureDialog(gui.root)
    d.on_press(Ev("F1")); d.on_press(Ev("Control_L")); d.on_press(Ev("Shift_L"))  # 3번째 키는 무시
    assert d.shown.cget("text") == "CTRL + F1"
    d.on_release(Ev("Control_L"))
    assert d.result is None
    d.on_release(Ev("F1")); d.on_release(Ev("Shift_L"))
    assert d.result == "ctrl+f1" and d.btn_ok.instate(["!disabled"])
    d.on_press(Ev("a")); d.on_release(Ev("a"))   # 다시 누르면 새로 지정
    assert d.result == "a"
    d._ok()
    assert d.confirmed


def test_toggle_hotkey_binding_and_change(gui):
    b = gui.hotkey_bindings()
    assert "ctrl+f12" in b and "CTRL + F12" in gui.btn_power.cget("text")
    b["ctrl+f12"]()
    pump(gui)
    assert not gui.macros_enabled
    assert not gui.hotkey_suppressed("f12")      # 재생 중에도 전환 키는 막지 않음
    assert gui.set_toggle_hotkey("f6") is not None          # alpha 의 핫키
    assert gui.set_toggle_hotkey("ctrl+f9") is not None     # 제어 키
    assert gui.set_toggle_hotkey("Shift + F11") is None
    assert "shift+f11" in gui.hotkey_bindings() and "ctrl+f12" not in gui.hotkey_bindings()
    assert "SHIFT + F11" in gui.btn_power.cget("text")
    assert "f11" in gui.recording_ignore_keys() and "shift" not in gui.recording_ignore_keys()


def test_editor_rejects_toggle_hotkey(gui):
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.v_hotkey.set("ctrl+f12")
    ed.insert_items(em.build_items("tap", key="e"))
    assert not ed.on_save() and "전체 실행 전환" in gui.shown[-1][1]


def test_editor_undo_redo(gui):
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    assert ed.btn_undo.instate(["disabled"])
    ed.insert_items(em.build_items("tap", key="q"))
    ed.insert_items(em.build_items("wait", delay_ms=100))
    ed.tree.selection_set("0")
    ed.on_delete()
    assert [i["type"] for i in ed.items] == ["kup", "wait"]
    pump(gui)
    assert ed.btn_undo.instate(["!disabled"])
    ed.undo()
    assert [i["type"] for i in ed.items] == ["kdown", "kup", "wait"]
    ed.undo()
    assert [i["type"] for i in ed.items] == ["kdown", "kup"]
    ed.redo()
    assert len(ed.items) == 3
    ed.undo(); ed.undo(); ed.undo()   # 더 되돌릴 게 없으면 무시
    assert ed.items == []
    ed.redo()
    ed.insert_items(em.build_items("wait", delay_ms=5))   # 새 변경은 redo 기록을 지운다
    assert ed._redo == []
    # 녹화 추가도 되돌리기 가능
    gui.app.start_record()
    ed.toggle_record()
    n = len(ed.items)
    ed.undo()
    assert len(ed.items) == n - 3


def test_editor_undo_shortcut_ignored_in_entry(gui):
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.insert_items(em.build_items("tap", key="q"))
    entry = [w for w in ed.top.winfo_children()[0].winfo_children() if isinstance(w, tk.ttk.Entry)][0]
    entry.focus_force()
    gui.root.update()
    assert ed._shortcut(ed.undo) is None and len(ed.items) == 2
    ed.tree.focus_force()
    gui.root.update()
    assert ed._shortcut(ed.undo) == "break" and ed.items == []


def test_max_minutes_field_saved(gui):
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.v_name.set("timed")
    ed.v_opts["max_minutes"].set("30")
    ed.insert_items(em.build_items("tap", key="e"))
    assert ed.on_save()
    assert gui.app.library["timed"].options["max_minutes"] == 30.0


def test_settings_persist_across_restart(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("디스플레이 없음")
    import gui as gui_mod
    from settings import Settings
    path = tmp_path / "settings.json"
    app = App(FakeBackend(), macros_dir=tmp_path / "m", recorder_factory=FakeRecorder, player_factory=FakePlayer)
    g = gui_mod.Gui(root, app, Settings(path))
    g.toggle_macros_enabled()
    g.delay.set("7")
    root.geometry("800x600+20+30")
    root.update()
    g.on_add()
    g.editor.top.geometry("950x620+40+50")
    root.update()
    g.editor.close()
    g.close()  # 창 닫기 -> 설정 저장
    s = Settings(path)
    assert s["macros_enabled"] is False and s["start_delay"] == 7
    assert s["main_geometry"].startswith("800x600") and s["editor_geometry"].startswith("950x620")

    root = tk.Tk()
    app = App(FakeBackend(), macros_dir=tmp_path / "m", recorder_factory=FakeRecorder, player_factory=FakePlayer)
    g = gui_mod.Gui(root, app, Settings(path))
    root.update()
    assert not g.macros_enabled and g.delay.get() == "7" and "실행 불가" in g.btn_power.cget("text")
    assert root.geometry().startswith("800x600")
    g.close()


def test_restore_geometry_offscreen(gui):
    from gui import restore_geometry
    top = tk.Toplevel(gui.root)
    restore_geometry(top, "500x400+99999+99999")   # 화면 밖 -> 크기만
    top.update()
    assert top.geometry().startswith("500x400") and not top.geometry().endswith("+99999+99999")
    restore_geometry(top, "garbage")                 # 무시
    top.destroy()
