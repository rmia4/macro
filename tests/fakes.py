class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.on_wait = None  # 대기 중 호출되는 훅(테스트에서 상태 변경용)

    def __call__(self):
        return self.t

    def wait(self, seconds):
        self.t += seconds
        if self.on_wait:
            self.on_wait()
        return False


class FakeBackend:
    def __init__(self, clock=None, cursor=(0, 0), title="Game"):
        self.calls = []
        self.clock = clock
        self.cursor = cursor
        self.title = title
        self.rect = (100, 50, 1380, 770)
        self.fail_on = None

    def _rec(self, *c):
        if self.fail_on == c[0]:
            raise OSError("boom")
        self.calls.append((self.clock() if self.clock else 0.0, *c))

    def key_down(self, n): self._rec("kdown", n)
    def key_up(self, n): self._rec("kup", n)
    def mouse_down(self, b): self._rec("mdown", b)
    def mouse_up(self, b): self._rec("mup", b)
    def scroll(self, dx, dy): self._rec("scroll", dx, dy)
    def mouse_move_rel(self, dx, dy): self._rec("rel", dx, dy)

    def mouse_move_abs(self, x, y):
        self._rec("abs", x, y)
        self.cursor = (x, y)

    def cursor_pos(self): return self.cursor
    def screen_size(self): return (1920, 1080)
    def foreground_title(self): return self.title
    def find_window_rect(self, title): return self.rect

    def names(self):
        return [c[1:] for c in self.calls]
