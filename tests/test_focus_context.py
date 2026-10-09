"""El Fager covers what Mo was looking at the moment he calls it: the Cockpit
raises itself and takes focus. So "what's this" and get_active_window saw El
Fager's own window. focus_context remembers the last window that wasn't El
Fager, and the prompt says what it was."""
import os

import pytest

from core import focus_context
from core.brain import Brain

OTHER_PID = os.getpid() + 1


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    focus_context.reset()
    now = {"t": 1000.0}
    monkeypatch.setattr(focus_context.time, "monotonic", lambda: now["t"])
    yield now
    focus_context.reset()


def test_it_remembers_the_window_mo_was_in():
    focus_context.observe(OTHER_PID, "Inbox – Gmail", "Chrome_WidgetWin_1", "comet.exe")
    line = focus_context.describe()
    assert "comet" in line
    assert '"Inbox – Gmail"' in line
    assert ".exe" not in line


def test_el_fagers_own_windows_never_replace_it():
    focus_context.observe(OTHER_PID, "Inbox – Gmail", "Chrome_WidgetWin_1", "comet.exe")
    focus_context.observe(os.getpid(), "El Fager", "Qt6QWindowIcon", "pythonw.exe")
    assert '"Inbox – Gmail"' in focus_context.describe()


@pytest.mark.parametrize("title, class_name", [
    ("", "Chrome_WidgetWin_1"),          # untitled
    ("", "Shell_TrayWnd"),               # the taskbar
    ("Program Manager", "Progman"),      # the desktop
    ("", "WorkerW"),
])
def test_the_shell_and_untitled_windows_are_not_remembered(title, class_name):
    focus_context.observe(OTHER_PID, "notes.txt - Notepad", "Notepad", "notepad.exe")
    focus_context.observe(OTHER_PID, title, class_name, "explorer.exe")
    assert '"notes.txt - Notepad"' in focus_context.describe()


def test_it_says_how_long_ago(_fresh):
    focus_context.observe(OTHER_PID, "notes.txt - Notepad", "Notepad", "notepad.exe")
    _fresh["t"] += 10
    assert "10 s ago" in focus_context.describe()
    _fresh["t"] += 170
    assert "3 min ago" in focus_context.describe()


def test_nothing_remembered_means_no_line():
    assert focus_context.describe() == ""
    text = " ".join(b["text"] for b in Brain(profile={})._build_system())
    assert "Before Mo turned to you" not in text


def test_the_prompt_names_the_window_outside_the_cached_prefix():
    focus_context.observe(OTHER_PID, "Inbox – Gmail", "Chrome_WidgetWin_1", "comet.exe")
    blocks = Brain(profile={})._build_system()
    assert '"Inbox – Gmail"' in blocks[-1]["text"]
    assert "Inbox" not in blocks[0]["text"]


def test_get_active_window_reports_mos_window_when_el_fager_is_in_front(monkeypatch):
    from tools import window_tool
    focus_context.observe(OTHER_PID, "Inbox – Gmail", "Chrome_WidgetWin_1", "comet.exe")
    monkeypatch.setattr(focus_context, "_foreground",
                        lambda: (os.getpid(), "El Fager", "Qt6QWindowIcon", "pythonw.exe"))
    result = window_tool.get_active_window()
    assert '"Inbox – Gmail"' in result
    assert "El Fager" in result


def test_get_active_window_is_unchanged_when_another_app_is_in_front(monkeypatch):
    from tools import window_tool
    focus_context.observe(OTHER_PID, "Inbox – Gmail", "Chrome_WidgetWin_1", "comet.exe")
    monkeypatch.setattr(focus_context, "_foreground",
                        lambda: (OTHER_PID, "notes.txt - Notepad", "Notepad", "notepad.exe"))

    class _Win:
        title, left, top, width, height = "notes.txt - Notepad", 0, 0, 800, 600

    import pygetwindow
    monkeypatch.setattr(pygetwindow, "getActiveWindow", lambda: _Win())
    assert window_tool.get_active_window().startswith("Active: 'notes.txt - Notepad'")
