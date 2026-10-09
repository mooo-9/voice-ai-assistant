"""Closing the Cockpit or the overlay from the taskbar, or with Alt+F4, runs
closeEvent — which only hides the window, because El Fager keeps running.
It also unsubscribed the window from core/progress and core/staging, and
nothing subscribed it again, so after one Alt+F4 the reopened surface
silently stopped showing tool steps and drafts waiting for a yes."""
from unittest.mock import MagicMock

import pytest

from core import progress, staging


@pytest.fixture(autouse=True)
def _own_settings(tmp_path, monkeypatch):
    import ui.overlay as overlay_mod
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(overlay_mod, "_SETTINGS_FILE", path)


def _cockpit():
    from ui.cockpit import CockpitWindow
    w = CockpitWindow(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    w.set_wake_listener(None)
    return w


def _overlay():
    from ui.overlay import OverlayWindow
    brain = MagicMock()
    brain._offline_mode = False
    w = OverlayWindow(MagicMock(), brain, MagicMock(), MagicMock())
    w.set_wake_listener(None)
    return w


@pytest.mark.parametrize("make", [_cockpit, _overlay], ids=["cockpit", "overlay"])
def test_a_closed_surface_still_hears_about_steps_and_drafts(qapp, make):
    w = make()
    w.close()                                  # the taskbar's close, or Alt+F4
    assert not w.isVisible()

    steps, drafts = [], []
    w.progress_changed.connect(lambda: steps.append(1))
    w.staged_changed.connect(lambda: drafts.append(1))
    progress.begin_turn("send omar a message")
    staging.stage("whatsapp", "Omar", "on my way")

    assert steps, "the surface no longer hears about tool steps"
    assert drafts, "the surface no longer hears about staged drafts"
