"""read_window_text: what a window says, read through UI Automation rather
than a screenshot — exact text, and it works while El Fager covers the window.
Read-only: it never clicks, types or focuses anything."""
import os

import pytest

from core import focus_context
from tools import window_tool

TEXT, BUTTON, PANE, EDIT = 50020, 50000, 50033, 50004


class El:
    def __init__(self, name="", ctype=TEXT, offscreen=False, children=(), value=None):
        self.CurrentName = name
        self.CurrentControlType = ctype
        self.CurrentIsOffscreen = offscreen
        self.children = list(children)
        self.value = value


class Walker:
    """The two TreeWalker calls the reader makes."""

    def __init__(self, root):
        self.parent = {}
        stack = [root]
        while stack:
            node = stack.pop()
            for child in node.children:
                self.parent[id(child)] = node
                stack.append(child)

    def GetFirstChildElement(self, el):
        return el.children[0] if el.children else None

    def GetNextSiblingElement(self, el):
        parent = self.parent.get(id(el))
        if parent is None:
            return None
        i = parent.children.index(el)
        return parent.children[i + 1] if i + 1 < len(parent.children) else None


def _collect(root, **kw):
    return window_tool._collect_text(root, Walker(root), value_of=lambda el: el.value, **kw)


class TestCollect:
    def test_it_reads_visible_text_in_order(self):
        root = El(ctype=PANE, children=[
            El("Inbox"),
            El(ctype=PANE, children=[El("Rodri signs for Barça"), El("Reply", BUTTON)]),
            El("Archived", offscreen=True),        # scrolled out of view
        ])
        assert _collect(root) == ["Inbox", "Rodri signs for Barça", "Reply"]

    def test_containers_and_blank_names_are_skipped(self):
        root = El("Chrome Legacy Window", PANE, children=[El(""), El("  "), El("Hello")])
        assert _collect(root) == ["Hello"]

    def test_repeats_in_a_row_are_read_once(self):
        root = El(ctype=PANE, children=[El("Send"), El("Send"), El("Draft"), El("Send")])
        assert _collect(root) == ["Send", "Draft", "Send"]

    def test_an_edit_box_gives_its_contents(self):
        root = El(ctype=PANE, children=[El("Search", EDIT, value="padel rackets")])
        assert _collect(root) == ["Search: padel rackets"]

    def test_it_stops_at_the_element_cap(self):
        root = El(ctype=PANE, children=[El(f"line {i}") for i in range(50)])
        assert len(_collect(root, max_nodes=10)) < 10

    def test_it_stops_when_time_runs_out(self):
        root = El(ctype=PANE, children=[El(f"line {i}") for i in range(50)])
        assert _collect(root, seconds=0) == []


class TestReadWindowText:
    @pytest.fixture(autouse=True)
    def _fake_uia(self, monkeypatch):
        focus_context.reset()
        read = []
        monkeypatch.setattr(window_tool, "_read_hwnd",
                            lambda hwnd: read.append(hwnd) or ["Rodri signs for Barça"])
        self.read = read
        yield
        focus_context.reset()

    def test_no_title_reads_the_window_mo_was_in(self, monkeypatch):
        focus_context.observe(os.getpid() + 1, "BBC Sport - Comet", "Chrome_WidgetWin_1",
                              "comet.exe", 4242)
        monkeypatch.setattr(focus_context, "el_fager_in_front", lambda: True)
        out = window_tool.read_window_text()
        assert self.read == [4242]
        assert out.startswith('Text in "BBC Sport - Comet"')
        assert "Rodri signs for Barça" in out

    def test_a_title_reads_that_window(self, monkeypatch):
        class Win:
            title, _hWnd = "Spotify Premium", 7

        monkeypatch.setattr(window_tool, "_find_window", lambda t: (Win(), None))
        window_tool.read_window_text("spotify")
        assert self.read == [7]

    def test_a_window_with_no_text_says_so(self, monkeypatch):
        class Win:
            title, _hWnd = "Game", 9

        monkeypatch.setattr(window_tool, "_find_window", lambda t: (Win(), None))
        monkeypatch.setattr(window_tool, "_read_hwnd", lambda hwnd: [])
        assert "no readable text" in window_tool.read_window_text("game").lower()

    def test_long_text_is_cut(self, monkeypatch):
        class Win:
            title, _hWnd = "Doc", 9

        monkeypatch.setattr(window_tool, "_find_window", lambda t: (Win(), None))
        monkeypatch.setattr(window_tool, "_read_hwnd", lambda hwnd: ["word " * 40] * 200)
        out = window_tool.read_window_text("doc")
        assert len(out) <= window_tool._READ_MAX_CHARS + 200
        assert out.endswith("[... cut]")


class TestWiring:
    def test_the_model_can_call_it(self):
        from core.brain import TOOLS, _TOOL_GROUP_NAMES
        assert any(t["name"] == "read_window_text" for t in TOOLS)
        assert "read_window_text" in _TOOL_GROUP_NAMES["window"]

    def test_asking_what_it_says_offers_it(self):
        from core.brain import _select_tools
        names = {t["name"] for t in _select_tools("what does it say on that window")}
        assert "read_window_text" in names

    def test_dispatch_reaches_it(self, monkeypatch):
        from core.brain import Brain
        monkeypatch.setattr(window_tool, "read_window_text",
                            lambda title="": f"read {title or 'last'}")
        assert Brain(profile={})._dispatch_tool("read_window_text", {"title": "x"}) == "read x"
