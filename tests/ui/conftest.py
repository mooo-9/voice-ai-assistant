import pytest


@pytest.fixture(scope="session")
def qapp():
    # QWebEngineWidgets MUST be imported before QApplication is instantiated
    # (same side-effect import as main.py) or OverlayWindow's HUD page fails.
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _nothing_outlives_its_test():
    """Surfaces subscribe to core/progress and core/staging when they are
    built. A test that fails before its close() — or never calls it — used to
    leave its window alive and subscribed, so every later test's steps and
    drafts reached it too; both suite crashes on 09-13/14 were that class of
    bug. After each test, drop every subscriber and delete every window the
    test made. deleteLater() runs no close handler, so nothing is saved to
    settings on the way out."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    before = set(QApplication.topLevelWidgets()) if QApplication.instance() else set()
    yield
    from core import progress, staging
    progress.reset()
    staging.reset()
    app = QApplication.instance()
    if app is None:
        return
    for widget in QApplication.topLevelWidgets():
        if widget not in before:
            widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
