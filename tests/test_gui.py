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


def test_macro_settings_buttons_follow_enable_state(gui):
    assert all(button.instate(["disabled"]) for button in gui.macro_settings_buttons)
    gui.toggle_macros_enabled()
    assert all(button.instate(["!disabled"]) for button in gui.macro_settings_buttons)
    gui.tree.selection_set("alpha")
    gui.macro_settings_buttons[1].invoke()
    assert gui.editor is not None and gui.editor.old_name == "alpha"
    gui.editor.close()
    gui.hotkey_bindings()[gui.toggle_hotkey]()
    pump(gui)
    assert all(button.instate(["disabled"]) for button in gui.macro_settings_buttons)


@pytest.mark.parametrize("action", ["on_add", "on_edit", "on_duplicate", "on_delete"])
def test_macro_settings_blocked_when_enabled(gui, monkeypatch, action):
    gui.tree.selection_set("alpha")
    confirmations = []
    monkeypatch.setattr("gui.messagebox.askyesno", lambda *a, **k: confirmations.append(a) or True)
    for button in gui.macro_settings_buttons:
        button.invoke()
    getattr(gui, action)()  # 더블클릭과 Delete 키도 같은 콜백을 사용한다
    assert gui.editor is None and set(gui.app.library) == {"alpha", "beta"}
    assert confirmations == [] and gui.shown == []


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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    gui.tree.selection_set("beta")
    gui.on_edit()
    ed = gui.editor
    ed.v_name.set("beta2")
    assert ed.on_save()
    assert "beta" not in gui.app.library and not (tmp_path / "beta.json").exists()
    assert "beta2" in gui.app.library and gui.selected() == "beta2"


def test_editor_test_play(gui):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.v_hotkey.set("ctrl+f12")
    ed.insert_items(em.build_items("tap", key="e"))
    assert not ed.on_save() and "전체 실행 전환" in gui.shown[-1][1]


def test_editor_undo_redo(gui):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
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
    assert all(button.instate(["!disabled"]) for button in g.macro_settings_buttons)
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


def test_overlay_xy_eight_directions():
    from gui import overlay_xy
    sw, sh, w, h, m = 1920, 1080, 200, 30, 12
    expect = {"nw": (12, 12), "n": (860, 12), "ne": (1708, 12), "w": (12, 525), "e": (1708, 525),
              "sw": (12, 1038), "s": (860, 1038), "se": (1708, 1038)}
    for pos, xy in expect.items():
        assert overlay_xy(pos, sw, sh, w, h, m) == xy, pos


def test_overlay_shows_flash_and_playback(gui):
    ov = gui.overlay
    pump(gui)
    assert not ov.visible                       # 평소에는 숨김
    gui.toggle_macros_enabled()                 # 전환하면 잠깐 표시
    pump(gui)
    assert ov.visible and ov.label.cget("text") == "○ 매크로 실행 불가"
    gui._flash_until = 0
    pump(gui)
    assert not ov.visible
    gui.toggle_macros_enabled()
    gui._flash_until = 0
    gui.tree.selection_set("beta")
    gui.on_play(immediate=True)
    pump(gui)
    assert ov.visible and ov.label.cget("text") == "▶ beta · 1/1"   # 아이콘·이름·횟수만
    assert ov.top.overrideredirect() and ov.top.attributes("-topmost")
    gui.app.stop_play()
    gui.app._thread.join(1)
    pump(gui)
    assert not ov.visible


def test_overlay_position_change_and_off(gui):
    gui.v_overlay.set("↙ 왼쪽 아래")
    gui.set_overlay_position(gui._overlay_key())
    assert gui.settings["overlay_position"] == "sw"
    pump(gui)
    ov = gui.overlay
    assert ov.visible                           # 위치 확인용으로 잠깐 표시
    gui.root.update()
    assert ov.top.winfo_x() == 12 and ov.top.winfo_y() > gui.root.winfo_screenheight() // 2
    gui.set_overlay_position("off")
    gui._flash_until = 10 ** 9
    pump(gui)
    assert not ov.visible


def test_overlay_shows_editor_recording(gui):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    gui.on_add()
    gui.editor.toggle_record(immediate=True)
    pump(gui)
    assert gui.overlay.visible and gui.overlay.label.cget("text").startswith("● 녹화 중")


def test_editor_relative_recording_flag(gui):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    gui.on_add()
    ed = gui.editor
    ed.v_opts["mouse_mode"].set("relative")
    ed.toggle_record(immediate=True)
    pump(gui)
    assert FakeRecorder.last.relative and gui.app.recording_relative
    assert "상대 이동(Raw Input)" in ed.banner.cget("text")
    ed.toggle_record()


def test_event_dialog_rmove_and_relpath(gui):
    import editor_model as em
    from gui import EventDialog
    d = EventDialog(gui.root, em.ADD_KINDS, kind="rmove")
    gui.root.update()
    assert d.rows["delta"][0].winfo_ismapped() and not d.rows["scroll"][0].winfo_ismapped()
    d.v["dx"].set("40"); d.v["dy"].set("0")
    d._on_ok()
    assert d.result == [{"type": "rmove", "dx": 40, "dy": 0, "dt": 0.1}]
    item = {"type": "relpath", "dt": 0.0, "points": [[0.0, 10, 0], [0.01, 10, 0]]}
    d = EventDialog(gui.root, em.RELPATH_KINDS, item=item)
    gui.root.update()
    assert d.rows["scale"][0].winfo_ismapped() and not d.rows["pos"][0].winfo_ismapped()
    d.v["scale_pct"].set("50")
    d._on_ok()
    assert [p[1] for p in d.result[0]["points"]] == [5, 5]


def test_overlay_play_text_infinite(gui):
    gui.tree.selection_set("alpha")   # repeat 0 = 무한
    gui.on_play(immediate=True)
    pump(gui)
    assert gui.overlay.label.cget("text") == "▶ alpha · 1/∞"
    gui.app._player.loop_index = 12
    pump(gui)
    assert gui.overlay.label.cget("text") == "▶ alpha · 12/∞"


def test_small_dialogs_centered_on_parent(gui):
    import editor_model as em
    from gui import EventDialog, HotkeyCaptureDialog
    gui.root.geometry("800x600+100+50")
    gui.root.update()
    for dlg in (EventDialog(gui.root, em.ADD_KINDS, kind="click"), HotkeyCaptureDialog(gui.root)):
        gui.root.update()
        top = dlg.top
        cx = top.winfo_rootx() + top.winfo_width() // 2
        cy = top.winfo_rooty() + top.winfo_height() // 2
        pcx = gui.root.winfo_rootx() + gui.root.winfo_width() // 2
        pcy = gui.root.winfo_rooty() + gui.root.winfo_height() // 2
        assert abs(cx - pcx) <= 30 and abs(cy - pcy) <= 30, (cx, cy, pcx, pcy)
        top.destroy()


def test_editor_first_open_centered(gui):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    gui.root.geometry("1000x700+100+50")
    gui.root.update()
    gui.settings["editor_geometry"] = ""
    gui.on_add()
    gui.root.update()
    top = gui.editor.top
    cx = top.winfo_rootx() + top.winfo_width() // 2
    pcx = gui.root.winfo_rootx() + gui.root.winfo_width() // 2
    assert top.winfo_viewable() and abs(cx - pcx) <= 30


def test_editor_repeat_block_ui(gui, monkeypatch):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.insert_items(em.build_items("tap", key="a", delay_ms=100))
    ed.insert_items(em.build_items("tap", key="b", delay_ms=100))
    ed.tree.selection_set(["2", "3"])
    from gui import ConditionDialog
    monkeypatch.setattr(ConditionDialog, "ask", classmethod(lambda cls, *a, **k: em.build_loop(count="5")))
    ed.on_add_loop()
    monkeypatch.undo()
    assert [i["type"] for i in ed.items] == ["kdown", "kup", "repeat_start", "kdown", "kup", "repeat_end"]
    assert ed.tree.item("3")["values"][2] == "│ 키 누름"            # 들여쓰기
    assert "반복 포함" in ed.summary.cget("text")
    # 반복 시작을 아래로 옮겨 끝 뒤로 가는 이동은 무시, 안쪽 이동은 허용
    ed.tree.selection_set("5"); ed.on_move(-1)
    ed.tree.selection_set("4"); ed.on_move(1)
    assert [i["type"] for i in ed.items][2] == "repeat_start" and em.block_error(ed.items) is None
    ed.tree.selection_set("2"); ed.on_move(-1)                      # 바깥으로 이동은 가능
    assert ed.items[1]["type"] == "repeat_start"
    # 시작만 지우면 짝도 지워지고 안의 이벤트는 남는다
    ed.tree.selection_set("1")
    ed.on_delete()
    assert [i["type"] for i in ed.items] == ["kdown", "kup", "kdown", "kup"]
    ed.undo()
    assert sum(i["type"] in em.BLOCK_MARKERS for i in ed.items) == 2
    # 횟수 수정
    i = next(n for n, it in enumerate(ed.items) if it["type"] == "repeat_start")
    d = ConditionDialog(ed, ed.items[i], mode="loop")
    assert d.v_kind.get() == "count" and d.v_count.get() == "5"
    d.v_count.set("9")
    d._on_ok()
    assert d.result["type"] == "repeat_start" and d.result["count"] == 9
    ed.v_name.set("rep")
    assert ed.on_save()
    m = gui.app.library["rep"]
    assert [e["type"] for e in m.events].count("repeat_start") == 1


def test_editor_wrap_overlapping_selection_shows_error(gui):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.insert_items(em.build_items("tap", key="a"))
    ed.wrap_repeat(2)  # 선택 없음 -> 마지막에 추가된 a 누름/뗌이 선택되어 있으므로 감싸짐
    ed.tree.selection_set(["0", "1"])  # 반복 시작 + 안쪽 일부
    ed.wrap_repeat(3)
    assert "겹칩니다" in gui.shown[-1][1]



class ScreenGrabber:
    """GUI 테스트용 가짜 화면: 배경 잡음 + 버튼 하나."""
    def __init__(self):
        import numpy as np
        rng = np.random.default_rng(1)
        self.screen = rng.integers(0, 60, size=(400, 600, 3), dtype=np.uint8)
        self.screen[300:330, 420:480] = (40, 40, 230)
        self.screen[300:303, 420:480] = 255
        for i in range(30):
            self.screen[300 + i, 420 + i] = (0, 255, 0)
        self.screen[10, 20] = (30, 60, 200)   # BGR -> #c83c1e

    def screen_size(self):
        return 600, 400

    def grab(self, x, y, w, h):
        return self.screen[y:y + h, x:x + w].copy()


class Ev:
    def __init__(self, x, y):
        self.x, self.y = x, y


def _open_condition(gui):
    if gui.macros_enabled:
        gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    from gui import ConditionDialog
    gui.grabber = ScreenGrabber()
    gui.delay.set("0")
    gui.on_add()
    return gui.editor, ConditionDialog(gui.editor)


def test_condition_dialog_crop_find_and_save(gui, tmp_path):
    ed, d = _open_condition(gui)
    assert d.top.title() == "조건 대기 추가"                            # 실험 표시 없음
    d._on_ok()
    assert d.result is None and "잘라내기" in d.error.cget("text")      # 이미지 없이 확인 불가
    d.on_crop()                                                          # 지연 0: 바로 캡처 + 선택 화면
    sel = d.selector
    assert sel.top.winfo_exists() and not ed.top.winfo_viewable()       # 편집 창은 숨김
    sel.on_press(Ev(420, 300)); sel.on_drag(Ev(450, 315)); sel.on_release(Ev(480, 330))
    gui.root.update()
    assert d.v_template.get() == "이미지1.png" and (ed.assets_dir / "이미지1.png").is_file()
    assert [v.get() for v in d.v_region] == ["380", "260", "140", "110"]   # 주변 여유 40px
    assert ed.top.winfo_viewable() and not d.v_full.get()
    d.on_test()
    assert d.test_label.cget("text").startswith("✔ 충족 · 일치도 100%")
    assert "찾은 위치 (450, 315)" in d.test_label.cget("text")
    d.v_negate.set(True)
    d.on_test()
    assert d.test_label.cget("text").startswith("✘ 불충족")
    d.v_negate.set(False)
    d.v_timeout.set("5")
    d._on_ok()
    item = d.result
    assert item["cond"]["template"] == "이미지1.png" and item["timeout"] == 5.0
    ed.insert_items([item])
    assert ed.tree.item("0")["values"][2] == "🔍 조건 대기"
    ed.v_name.set("반응형")
    assert ed.on_save()
    assert (tmp_path / "반응형" / "이미지1.png").is_file()
    assert gui.app.library["반응형"].events[0]["type"] == "wait_until"


def test_condition_dialog_select_cancel_and_tiny_drag(gui):
    ed, d = _open_condition(gui)
    d.on_crop()
    d.selector.on_press(Ev(10, 10)); d.selector.on_release(Ev(12, 11))   # 너무 작음: 무시
    assert d.selector.top.winfo_exists()
    d.selector.finish(None)                                               # Esc
    gui.root.update()
    assert d.v_template.get() == "" and ed.top.winfo_viewable()
    d.on_pick_region()                                                    # 영역만 지정 (이미지 저장 없음)
    d.selector.on_press(Ev(100, 50)); d.selector.on_release(Ev(300, 250))
    assert [v.get() for v in d.v_region] == ["100", "50", "200", "200"] and ed.available_templates() == set()


def test_condition_dialog_color_range_pick_and_window_coords(gui):
    ed, d = _open_condition(gui)
    d.v_kind.set("pixel"); d._on_kind()
    d.on_pick_color()                                   # 범위 드래그 -> 가장 많은 색
    d.selector.on_press(Ev(425, 305)); d.selector.on_release(Ev(475, 325))
    gui.root.update()
    assert [v.get() for v in (d.v_px, d.v_py, d.v_pw, d.v_ph)] == ["425", "305", "50", "20"]
    assert d.v_color.get() == "#e62828" and "범위의 9" in d.color_info.cget("text")
    d.on_test()
    assert d.test_label.cget("text").startswith("✔ 충족 · 색 비율 9")
    d.v_color.set("#00ff00")
    d.on_test()
    assert d.test_label.cget("text").startswith("✘ 불충족")
    ed.v_title.set("Game")                              # 창 기준 (창 좌상단 = (100, 50))
    d.on_pick_color()
    d.selector.on_press(Ev(425, 305)); d.selector.on_release(Ev(475, 325))
    assert (d.v_px.get(), d.v_py.get()) == ("325", "255")
    d.v_ratio.set("70")
    d._on_ok()
    assert d.result["cond"] == {"kind": "pixel", "x": 325, "y": 255, "w": 50, "h": 20, "color": "#e62828",
                                "tolerance": 20, "ratio": 0.7}
    d2 = __import__("gui").ConditionDialog(ed, {"type": "wait_until", "dt": 0,
                                                "cond": {"kind": "pixel", "x": 1, "y": 2, "color": "#123456"}})
    assert (d2.v_pw.get(), d2.v_ph.get(), d2.v_ratio.get()) == ("1", "1", "50")   # 예전 한 점 조건


def test_editor_condition_edit_and_assets_lifecycle(gui, tmp_path):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    import shutil
    ed, d = _open_condition(gui)
    d.on_crop()
    d.selector.on_press(Ev(420, 300)); d.selector.on_release(Ev(480, 330))
    d._on_ok()
    ed.insert_items([d.result])
    ed.v_name.set("원본")
    assert ed.on_save()
    staging = ed.assets_dir
    assert not staging.exists()                                           # 닫으면 작업 폴더 삭제
    gui.tree.selection_set("원본")
    gui.on_duplicate()
    assert (tmp_path / "원본 복사" / "이미지1.png").is_file()                # 복제 시 이미지도 복사
    gui.tree.selection_set("원본")
    gui.on_edit()
    ed = gui.editor
    assert (ed.assets_dir / "이미지1.png").is_file()                        # 기존 이미지를 작업 폴더로
    from gui import ConditionDialog
    d = ConditionDialog(ed, ed.items[0])
    assert d.v_template.get() == "이미지1.png" and d.v_timeout.get() == "10" and d._thumb is not None
    d.top.destroy()
    ed.v_name.set("이름변경")
    assert ed.on_save()
    assert not (tmp_path / "원본").exists() and (tmp_path / "이름변경" / "이미지1.png").is_file()
    # 이미지 파일이 없으면 저장 거부
    gui.tree.selection_set("이름변경")
    gui.on_edit()
    ed = gui.editor
    shutil.rmtree(ed.assets_dir); ed.assets_dir.mkdir()
    assert not ed.on_save() and "조건 이미지가 없습니다" in gui.shown[-1][1]


def test_editor_test_play_uses_staging_assets(gui, monkeypatch):
    ed, d = _open_condition(gui)
    d.on_crop()
    d.selector.on_press(Ev(420, 300)); d.selector.on_release(Ev(480, 330))
    d._on_ok()
    ed.insert_items([d.result])
    calls = []
    monkeypatch.setattr(gui.app, "start_play", lambda *a: calls.append(a))
    ed.test_play()
    assert calls and calls[0][3] == ed.assets_dir


def test_run_screen_action_countdown_hides_and_restores(gui):
    ed, _ = _open_condition(gui)
    gui.delay.set("1")
    ran = []
    gui.run_screen_action([ed.top], "화면 캡처", lambda restore: (ran.append(1), restore()))
    gui.root.update()
    assert not ran and not ed.top.winfo_viewable()
    assert gui.overlay_state()[0] == "1초 후 화면 캡처"
    for _ in range(14):
        time.sleep(0.1); gui.root.update()
    assert ran == [1] and ed.top.winfo_viewable()



def test_branch_dialog_wraps_selection_with_else(gui):
    import editor_model as em
    from gui import ConditionDialog
    ed, _ = _open_condition(gui)
    ed.insert_items(em.build_items("tap", key="a", delay_ms=100))
    ed.tree.selection_set(["0", "1"])
    d = ConditionDialog(ed, mode="if")
    assert d.kinds == ("var", "group") and d.v_kind.get() == "var"   # 흐름 제어는 변수로만
    d.v_var.set("hp")
    d.v_with_else.set(True)
    d._on_ok()
    ed.wrap_if(d.result["cond"], d.result["with_else"])
    assert [i["type"] for i in ed.items] == ["if_start", "kdown", "kup", "else", "if_end"]
    assert "with_else" not in ed.items[0]
    assert ed.tree.item("1")["values"][2] == "│ 키 누름" and ed.tree.item("3")["values"][2] == "↪ 아니면"
    ed.tree.selection_set("4")                    # 분기 끝을 지우면 만약/아니면도 함께
    ed.on_delete()
    assert [i["type"] for i in ed.items] == ["kdown", "kup"]
    ed.undo()
    ed.tree.selection_set("3")                    # '아니면'만 지우기
    ed.on_delete()
    assert [i["type"] for i in ed.items] == ["if_start", "kdown", "kup", "if_end"]
    ed.items.insert(0, em.build_set_var(target="hp", kind="pixel"))
    ed.v_name.set("분기")
    assert ed.on_save()


def test_click_and_break_dialogs(gui):
    from gui import ConditionDialog
    ed, _ = _open_condition(gui)
    d = ConditionDialog(ed, mode="click")
    assert d.v_kind.get() == "image"
    d.on_crop()
    d.selector.on_press(Ev(420, 300)); d.selector.on_release(Ev(480, 330))
    d.v_button.set("right"); d.v_off_x.set("3"); d.v_hold.set("80")
    d._on_ok()
    ck = d.result
    assert ck["type"] == "click_image" and ck["button"] == "right" and ck["offset"] == [3, 0] and ck["hold"] == 0.08
    d = ConditionDialog(ed, mode="break")
    gui.root.update()
    assert not d.f_click.winfo_ismapped() and not d.f_test.winfo_ismapped()
    d.v_var.set("끝")
    d.v_negate.set(True)
    d._on_ok()
    assert d.result["type"] == "break_if" and d.result["cond"]["negate"] is True
    assert "timeout" not in d.result
    import editor_model as em
    ed.insert_items([ck, em.build_set_var(target="끝", kind="pixel"), d.result])
    ed.v_name.set("클릭탈출")
    assert ed.on_save()
    assert gui.app.library["클릭탈출"].events[0]["type"] == "click_image"


def test_edit_routes_condition_modes(gui, monkeypatch):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    import editor_model as em
    seen = []
    import gui as gui_mod
    monkeypatch.setattr(gui_mod.ConditionDialog, "ask",
                        classmethod(lambda cls, ed, item=None, mode="wait", parent=None: seen.append(mode)))
    gui.on_add()
    ed = gui.editor
    px = {"kind": "pixel", "x": 1, "y": 1, "color": "#000000"}
    ed.items = [em.build_wait_until(kind="pixel", x=1, y=1), {"type": "if_start", "cond": px, "dt": 0},
                {"type": "break_if", "cond": px, "dt": 0}, {"type": "if_end", "dt": 0},
                em.build_click_image(kind="image", template="a.png")]
    ed.refresh_tree()
    for i in (0, 1, 2, 4):
        ed.tree.selection_set(str(i))
        ed.on_edit()
    assert seen == ["wait", "if", "break", "click"]
    ed.items = [em.build_set_var(target="v", kind="pixel"), {"type": "while_start", "cond": px, "dt": 0},
                {"type": "while_end", "dt": 0}, {"type": "repeat_start", "count": 2, "dt": 0},
                {"type": "repeat_end", "dt": 0}]
    ed.refresh_tree()
    for i in (0, 1, 3):
        ed.tree.selection_set(str(i))
        ed.on_edit()
    assert seen[4:] == ["set_var", "loop", "loop"]


def test_while_and_set_var_dialogs(gui):
    import editor_model as em
    from gui import ConditionDialog
    ed, _ = _open_condition(gui)
    ed.insert_items(em.build_items("tap", key="a", delay_ms=100))
    # 변수 저장: 범위 색 판정 결과를 '적' 에 저장
    ed.tree.selection_set([])
    d = ConditionDialog(ed, mode="set_var")
    d.v_name.set("적")
    d.v_kind.set("pixel"); d._on_kind()
    d.v_px.set("5"); d.v_py.set("6")
    d._on_ok()
    assert d.result["type"] == "set_var" and d.result["name"] == "적" and d.result["cond"]["kind"] == "pixel"
    ed.items.insert(0, d.result)
    ed.refresh_tree()
    assert "쓰일 때 판정" in ed.tree.item("0")["values"][3]
    # 반복문 추가: 기본은 횟수, 변수를 고르면 동안 반복
    ed.tree.selection_set(["1", "2"])
    d = ConditionDialog(ed, mode="loop")
    gui.root.update()
    assert d.kinds == ("count", "var", "group") and d.v_kind.get() == "count"
    assert d.f_count.winfo_ismapped() and not d.f_neg.winfo_ismapped() and not d.f_test.winfo_ismapped()
    d.v_count.set("-1")
    d._on_ok()
    assert "반복 횟수" in d.error.cget("text")
    assert "적" in d.var_box.cget("values")
    d.v_kind.set("var"); d._on_kind()
    gui.root.update()
    assert d.f_var.winfo_ismapped() and d.f_neg.winfo_ismapped() and not d.f_test.winfo_ismapped()
    assert "동안" in d.intro.cget("text")
    d._on_ok()
    assert d.error.cget("text")                     # 변수 이름이 비어 있음
    d.v_var.set("적")
    d._on_ok()
    ed.wrap_while(d.result["cond"])
    assert [i["type"] for i in ed.items] == ["set_var", "while_start", "kdown", "kup", "while_end"]
    assert ed.tree.item("2")["values"][2] == "│ 키 누름"
    # 수정에서 횟수로 바꾸면 짝 끝 표시도 '반복 끝'으로 바뀐다
    ed.tree.selection_set("1")
    d = ConditionDialog(ed, ed.items[1], mode="loop")
    assert d.v_kind.get() == "var" and d.v_var.get() == "적"
    d.v_kind.set("count"); d.v_count.set("3"); d._on_kind()
    d._on_ok()
    ed.items = em.replace_loop_start(ed.items, 1, d.result)
    assert [i["type"] for i in ed.items] == ["set_var", "repeat_start", "kdown", "kup", "repeat_end"]
    ed.items = em.replace_loop_start(ed.items, 1, em.build_loop(loop_kind="cond", kind="var", name="적"))
    ed.refresh_tree()
    ed.v_name.set("동안")
    assert ed.on_save()
    assert gui.app.library["동안"].events[1]["cond"] == {"kind": "var", "name": "적"}


def test_flow_dialog_keeps_legacy_screen_condition(gui):
    from gui import ConditionDialog
    ed, _ = _open_condition(gui)
    px = {"kind": "pixel", "x": 3, "y": 4, "color": "#102030"}
    d = ConditionDialog(ed, {"type": "if_start", "dt": 0, "cond": px}, mode="if")
    assert d.kinds == ("var", "group", "image", "pixel") and d.v_kind.get() == "pixel"
    d._on_ok()
    assert {k: d.result["cond"][k] for k in px} == px    # 한 점은 1x1 범위로 정규화
    d = ConditionDialog(ed, mode="cond")                 # 여러 조건의 하위 조건은 변수만
    assert d.kinds == ("var",)


def test_group_condition_dialog(gui, monkeypatch):
    import editor_model as em
    from gui import ConditionDialog
    ed, _ = _open_condition(gui)
    d = ConditionDialog(ed, mode="if")
    d.v_kind.set("group"); d._on_kind()
    gui.root.update()
    assert d.f_group.winfo_ismapped()
    subs = iter([{"cond": {"kind": "var", "name": "a"}},
                 {"cond": {"kind": "var", "name": "v"}}, None, None])
    opened = []

    def fake_ask(cls, editor, item=None, mode="wait", parent=None):
        opened.append((mode, parent, item))
        return next(subs)
    monkeypatch.setattr(ConditionDialog, "ask", classmethod(fake_ask))
    d.on_sub_add()
    d._on_ok()
    assert "2개 이상" in d.error.cget("text")
    d.on_sub_add()
    d.on_sub_add()                                   # 취소
    assert opened[0][:2] == ("cond", d.top) and d.sub_list.size() == 2
    d.sub_list.selection_set(1)
    d.on_sub_edit()                                  # 취소 -> 그대로
    assert opened[-1][2] == {"cond": {"kind": "var", "name": "v"}}
    d.v_group_op.set("any")
    d._on_ok()
    assert d.result["cond"]["kind"] == "any" and len(d.result["cond"]["conds"]) == 2
    # 수정으로 다시 열면 여러 조건 상태가 복원된다
    d2 = ConditionDialog(ed, {"type": "if_start", "dt": 0, "cond": d.result["cond"]}, mode="if")
    assert d2.v_kind.get() == "group" and d2.v_group_op.get() == "any" and d2.sub_list.size() == 2
    d2.sub_list.selection_set(0)
    d2.on_sub_delete()
    assert d2.sub_list.size() == 1
    # 조건 하나 고르기(mode=cond)에는 '여러 조건' 선택지가 없고 지연 칸도 없다
    d3 = ConditionDialog(ed, mode="cond")
    gui.root.update()
    assert not d3._common_has_rows
    d3.v_kind.set("var"); d3.v_var.set("v")
    d3._on_ok()
    assert d3.result == {"cond": {"kind": "var", "name": "v"}}
    assert em.describe({"type": "if_start", "cond": d.result["cond"]}).startswith("[변수 'a']")


def test_infinite_repeat_summary(gui, monkeypatch):
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    import editor_model as em
    gui.on_add()
    ed = gui.editor
    ed.insert_items(em.build_items("tap", key="a"))
    monkeypatch.setattr("gui.ConditionDialog.ask", classmethod(lambda cls, *a, **k: em.build_loop(count="0")))
    ed.on_add_loop()
    assert ed.items[0]["count"] == 0 and "무한 반복 포함" in ed.summary.cget("text")


def test_event_dialog_capture_key(gui):
    import editor_model as em
    from types import SimpleNamespace
    from gui import EventDialog
    d = EventDialog(gui.root, em.ADD_KINDS, kind="tap")
    d._capture_key()
    assert d._capturing and d.capture_btn.cget("text") == "키를 누르세요…"
    assert d._on_capture(SimpleNamespace(keysym="F5", keycode=0)) == "break"
    assert d.v["key"].get() == "f5" and not d._capturing
    d._capture_key()
    d._on_capture(SimpleNamespace(keysym="Multi_key", keycode=0))
    assert d.v["key"].get() == "f5" and "지원하지 않는 키" in d.error.cget("text")
    d._on_ok()
    assert d.result[0]["key"] == "f5"


def test_move_record_inserts_only_moves_after_selection(gui):
    import editor_model as em
    from gui import EventDialog
    gui.toggle_macros_enabled()  # 설정 작업은 실행 불가 상태에서 수행
    gui.on_add()
    ed = gui.editor
    # 이동 녹화 버튼은 마우스 이동 종류에서만 보인다
    d = EventDialog(ed.top, em.ADD_KINDS, kind="tap", record=ed.start_move_record)
    assert not d.rows["record"][0].winfo_manager()
    d.v_kind.set(dict(em.ADD_KINDS)["move"])
    assert d.rows["record"][0].winfo_manager()
    ed.insert_items(em.build_items("tap", key="b") + em.build_items("tap", key="c"))
    ed.tree.selection_set(["1"])
    gui.delay.set("0")  # 카운트다운 없이 바로 시작
    d._on_record()
    assert not d.top.winfo_exists() and gui.app.recording
    pump(gui)
    assert "이동 녹화 중" in ed.banner.cget("text")
    FakeRecorder.events = [{"t": 0.1, "type": "move", "x": 1, "y": 2}, {"t": 0.2, "type": "kdown", "key": "a"},
                           {"t": 0.3, "type": "move", "x": 3, "y": 4}, {"t": 0.4, "type": "kup", "key": "a"}]
    try:
        ed.toggle_record()
    finally:
        del FakeRecorder.events
    assert [i["type"] for i in ed.items] == ["kdown", "kup", "path", "kdown", "kup"]
    assert ed.items[2]["points"][-1][1:] == [3, 4] and not ed._moves_only
