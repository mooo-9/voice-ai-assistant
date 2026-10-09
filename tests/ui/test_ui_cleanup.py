"""A test that fails before its close() used to leave its window alive and
subscribed to core/progress and core/staging, so every later test's steps and
drafts reached it too — the class of bug behind both suite crashes on
09-13/14. tests/ui/conftest.py now cleans up after every test.

These two check that it does, so they rely on running in file order."""
from unittest.mock import MagicMock

_left_open = []


def test_a_window_a_test_forgets_to_close(qapp, tmp_path, monkeypatch):
    import ui.overlay as overlay_mod
    monkeypatch.setattr(overlay_mod, "_SETTINGS_FILE", tmp_path / "settings.json")
    from ui.cockpit import CockpitWindow

    w = CockpitWindow(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    w.set_wake_listener(None)
    _left_open.append(w)          # and never closed


def test_the_next_test_starts_clean(qapp):
    from PyQt6 import sip

    from core import progress, staging

    assert _left_open and sip.isdeleted(_left_open[0])
    assert progress._subscribers == []
    assert staging._subscribers == []
