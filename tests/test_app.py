import threading

import pytest

from fakes import FakeBackend
from hotkeys import HotkeyDispatcher
from main import App
from profiles import Macro


def test_repl_commands(tmp_path):
    out = []
    app = App(FakeBackend(), macros_dir=tmp_path, log=out.append)
    app.macro = Macro(events=[{"t": 0, "type": "move", "x": 1, "y": 1}])
    assert app.handle("save demo")
    assert app.handle("list") and "demo" in out[-1]
    app.macro = None
    app.handle("load demo")
    assert app.macro and len(app.macro.events) == 1
    app.handle("set window_title Elden Ring")
    assert app.options.window_title == "Elden Ring"
    app.handle("set speed -1")
    assert out[-1].startswith("오류")
    app.handle("load ../evil")
    assert out[-1].startswith("오류")
    app.handle("delete demo")
    assert not (tmp_path / "demo.json").exists() and app.macro_name is None
    app.handle("delete demo")
    assert out[-1].startswith("오류")
    app.handle("show")
    assert app.handle("") is True
    assert app.handle("quit") is False


def test_hotkey_dispatch_combos():
    hits = []
    d = HotkeyDispatcher({"f1": lambda: hits.append("f1"), "ctrl+f1": lambda: hits.append("ctrl+f1"),
                          "a+s": lambda: hits.append("a+s")})
    d.press("f1"); d.release("f1")
    d.press("ctrl_r"); d.press("f1"); d.release("f1"); d.release("ctrl_r")   # 큰 조합 우선
    d.press("f1"); d.press("ctrl"); d.release("ctrl"); d.release("f1")       # 순서 반대: f1 만
    d.press("s"); d.press("a"); d.release("a"); d.release("s")               # 아무 순서나
    d.press("w"); d.press("f1"); d.release("f1"); d.release("w")             # 다른 키 누른 채 단일 핫키
    assert hits == ["f1", "ctrl+f1", "f1", "a+s", "f1"]


def test_hotkey_dispatch_ignores_autorepeat():
    hits = []
    d = HotkeyDispatcher({"f8": lambda: hits.append(1)})
    d.press("f8"); d.press("f8"); d.press("f8")
    d.release("f8"); d.press("f8")
    d.press("a")
    assert len(hits) == 2


from fakes import FakePlayer, FakeRecorder  # noqa: E402
from player import PlayOptions  # noqa: E402
from profiles import save_macro, macro_path  # noqa: E402


def make_app(tmp_path):
    out = []
    app = App(FakeBackend(), macros_dir=tmp_path, log=out.append,
              recorder_factory=FakeRecorder, player_factory=FakePlayer)
    return app, out


def test_library_store_rename_and_reload(tmp_path):
    app, out = make_app(tmp_path)
    m = Macro(events=[{"t": 0, "type": "kdown", "key": "a"}], hotkey="f6", options={"repeat": 3})
    assert app.store("one", m) == "one"
    try:
        app.store("one", m)
        assert False, "중복 저장은 거부되어야 함"
    except ValueError:
        pass
    app.store("two", m, old_name="one")
    assert not (tmp_path / "one.json").exists() and (tmp_path / "two.json").exists()
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    save_macro(Macro(options={"speed": -1}), macro_path(tmp_path, "badopt"))
    app.reload_library()
    assert list(app.library) == ["two"] and app.library["two"].hotkey == "f6"
    assert sum("불러오기 실패" in line for line in out) == 2


def test_record_and_play_flow(tmp_path):
    app, out = make_app(tmp_path)
    app.start_record("Game", "window", {"f8", "f6"})
    assert app.recording and FakeRecorder.last.coord_space == "window"
    m = app.stop_record()
    assert len(m.events) == 3 and not app.recording
    app.start_play(m, PlayOptions(), "demo")
    assert app.playing and app.playing_name == "demo" and app.playing_keys == {"a"}
    assert app.progress == (1, 0, PlayOptions().repeat)
    try:
        app.start_record()
        assert False
    except RuntimeError:
        pass
    app.stop_play()
    app._thread.join(1)
    assert not app.playing


def test_cli_play_without_macro(tmp_path):
    app, out = make_app(tmp_path)
    app.handle("play")
    assert out[-1].startswith("오류: 재생할 매크로가 없습니다")



def test_start_play_creates_vision_for_condition_macros(tmp_path):
    made = []
    app = App(FakeBackend(), macros_dir=tmp_path, log=lambda m: None, recorder_factory=FakeRecorder,
              player_factory=FakePlayer, vision_factory=lambda d: made.append(d) or ("vision", d))
    plain = Macro(events=[{"t": 0, "type": "kdown", "key": "a"}])
    cond = Macro(events=[{"t": 0, "type": "wait_until", "cond": {"kind": "pixel", "x": 1, "y": 1, "color": "#000000"}}])
    app.store("plain", plain)
    app.store("cond", cond)
    app.start_play(plain, PlayOptions(), "plain")
    assert app._player.vision is None and made == []
    app.stop_play(); app._thread.join(1)
    app.start_play(cond, PlayOptions(), "cond")
    assert made == [tmp_path / "cond"] and app._player.vision == ("vision", tmp_path / "cond")
    app.stop_play(); app._thread.join(1)



def test_store_assets_replace_rename_delete(tmp_path):
    app, out = make_app(tmp_path)
    src = tmp_path / "staging"
    src.mkdir()
    (src / "a.png").write_bytes(b"A")
    (src / "unused.png").write_bytes(b"U")
    m = Macro(events=[{"t": 0, "type": "kdown", "key": "a"}])
    app.store("m1", m, assets={"a.png": src / "a.png"})
    assert sorted(p.name for p in (tmp_path / "m1").iterdir()) == ["a.png"]
    assert app.asset_files("m1") == {"a.png": tmp_path / "m1" / "a.png"}
    app.store("m2", m, old_name="m1")                       # assets=None: 폴더만 옮김
    assert not (tmp_path / "m1").exists() and (tmp_path / "m2" / "a.png").read_bytes() == b"A"
    (src / "b.png").write_bytes(b"B")
    app.store("m2", m, old_name="m2", assets={"b.png": src / "b.png"})   # 교체
    assert sorted(p.name for p in (tmp_path / "m2").iterdir()) == ["b.png"]
    app.store("m2", m, old_name="m2", assets={})            # 이미지 없음 -> 폴더 삭제
    assert not (tmp_path / "m2").exists()
    app.store("m3", m, assets={"b.png": src / "b.png"})
    app.delete("m3")
    assert not (tmp_path / "m3").exists() and not (tmp_path / "m3.json").exists()
    app.reload_library()
    assert "m3" not in app.library and set(app.library) == {"m2"}   # 이미지 폴더는 목록에 안 나옴


def test_store_asset_failure_keeps_old_images(tmp_path, monkeypatch):
    from pathlib import Path
    app, out = make_app(tmp_path)
    src = tmp_path / "staging"
    src.mkdir()
    (src / "a.png").write_bytes(b"A")
    m = Macro(events=[{"t": 0, "type": "kdown", "key": "a"}])
    app.store("m1", m, assets={"a.png": src / "a.png"})
    (src / "new.png").write_bytes(b"N")
    (src / "b.png").write_bytes(b"B")
    write = Path.write_bytes

    def failing(self, data):
        if self.name == "b.png":
            raise OSError("disk full")
        return write(self, data)
    monkeypatch.setattr(Path, "write_bytes", failing)
    try:
        app.store("m1", m, old_name="m1", assets={"a.png": src / "new.png", "b.png": src / "b.png"})
    except OSError:
        pass
    else:
        raise AssertionError("쓰기 실패가 알려져야 함")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m1", "m1.json", "staging"]
    assert (tmp_path / "m1" / "a.png").read_bytes() == b"A"


def test_play_end_reason_from_error_and_player(tmp_path):
    class Broken(FakePlayer):
        def run(self, macro):
            raise OSError("SendInput 실패")

    class Timeout(FakePlayer):
        stop_reason = "조건 대기 시간 초과로 재생을 멈춥니다 (10초)"

        def run(self, macro):
            pass

    m = Macro(events=[{"t": 0, "type": "kdown", "key": "a"}])
    for factory, reason in ((Broken, "재생 오류: SendInput 실패"), (Timeout, Timeout.stop_reason)):
        app = App(FakeBackend(), macros_dir=tmp_path, log=lambda s: None, player_factory=factory)
        app.play_end_reason = "이전 것"
        app.start_play(m, PlayOptions())
        app._thread.join(1)
        assert app.play_end_reason == reason


def _store_with_images(tmp_path):
    app, _ = make_app(tmp_path)
    src = tmp_path / "staging"
    src.mkdir()
    (src / "a.png").write_bytes(b"A")
    (src / "new.png").write_bytes(b"N")
    m = Macro(events=[{"t": 0, "type": "kdown", "key": "a"}])
    app.store("m1", m, assets={"a.png": src / "a.png"})
    return app, src, (tmp_path / "m1.json").read_text(encoding="utf-8")


def test_store_folder_swap_failure_restores_old_images(tmp_path, monkeypatch):
    """새 폴더를 바꿔 넣다 실패해도 기존 이미지와 JSON 이 그대로 남는다."""
    from pathlib import Path
    app, src, before = _store_with_images(tmp_path)
    rename = Path.rename

    def failing(self, target):
        if self.name.endswith(".saving"):
            raise OSError("locked")
        return rename(self, target)
    monkeypatch.setattr(Path, "rename", failing)
    m2 = Macro(events=[{"t": 0, "type": "kdown", "key": "b"}])
    with pytest.raises(OSError):
        app.store("m1", m2, old_name="m1", assets={"a.png": src / "new.png"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m1", "m1.json", "staging"]
    assert (tmp_path / "m1" / "a.png").read_bytes() == b"A"
    assert (tmp_path / "m1.json").read_text(encoding="utf-8") == before


def test_store_json_failure_restores_old_images(tmp_path, monkeypatch):
    import main
    app, src, before = _store_with_images(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(main, "save_macro", boom)
    with pytest.raises(OSError):
        app.store("m1", Macro(events=[]), old_name="m1", assets={"a.png": src / "new.png"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m1", "m1.json", "staging"]
    assert (tmp_path / "m1" / "a.png").read_bytes() == b"A"


def test_stop_all_releases_keys_when_player_hangs(tmp_path):
    import time
    from player import PlayOptions
    from vision import Match
    gate = threading.Event()

    class HangVision:  # 판정이 멈춰 중지 요청을 못 보는 상태
        def preload(self, conds): pass

        def check(self, cond, origin=(0, 0)):
            gate.wait(5)
            return Match(False, 0.0)
    be = FakeBackend()
    app = App(be, macros_dir=tmp_path, log=lambda m: None, vision_factory=lambda d: HangVision())
    app.STOP_TIMEOUT = 0.2
    pix = {"kind": "pixel", "x": 0, "y": 0, "color": "#000000"}
    app.start_play(Macro(events=[{"t": 0, "type": "kdown", "key": "a"},
                                 {"t": 0, "type": "wait_until", "cond": pix},
                                 {"t": 0.1, "type": "kup", "key": "a"}]), PlayOptions(approach=False))
    deadline = time.monotonic() + 2
    while ("kdown", "a") not in be.names() and time.monotonic() < deadline:
        time.sleep(0.01)
    app.stop_all()
    assert app.playing and be.names()[-1] == ("kup", "a")  # 스레드는 아직 멈춰 있지만 키는 뗐다
    gate.set()
    app._thread.join(2)
