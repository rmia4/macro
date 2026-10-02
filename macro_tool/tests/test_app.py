import threading

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
    app.handle("show")
    assert app.handle("") is True
    assert app.handle("quit") is False


def test_hotkey_dispatch_ignores_autorepeat():
    hits = []
    d = HotkeyDispatcher({"f8": lambda: hits.append(1)})
    d.press("f8"); d.press("f8"); d.press("f8")
    d.release("f8"); d.press("f8")
    d.press("a")
    assert len(hits) == 2
