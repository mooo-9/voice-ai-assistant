""""What's this" photographed the whole monitor — with the Cockpit, which had
just raised itself, on top of whatever Mo meant. It now photographs the window
he was in, drawn as it is even while El Fager covers it."""
import os

import pytest
from PIL import Image

from core import focus_context, pipeline
from tools import screen_tool, whatsapp_desktop

OTHER_PID = os.getpid() + 1


@pytest.fixture(autouse=True)
def _fresh():
    focus_context.reset()
    yield
    focus_context.reset()


def test_the_remembered_window_keeps_its_handle():
    focus_context.observe(OTHER_PID, "Inbox – Gmail", "Chrome_WidgetWin_1", "comet.exe", 4242)
    assert focus_context.last()["hwnd"] == 4242


class TestWhatsThisPhotographsTheWindowMoMeant:
    def test_the_remembered_window_is_captured(self, monkeypatch):
        focus_context.observe(OTHER_PID, "Inbox – Gmail", "Chrome_WidgetWin_1", "comet.exe", 4242)
        asked = []
        monkeypatch.setattr(screen_tool, "capture_window",
                            lambda hwnd: asked.append(hwnd) or ("b64", "path"))
        monkeypatch.setattr(screen_tool, "capture_screenshot",
                            lambda: pytest.fail("photographed the whole screen"))
        assert pipeline._capture_for_turn() == ("b64", "path")
        assert asked == [4242]

    def test_a_window_that_cannot_be_captured_falls_back_to_the_screen(self, monkeypatch):
        focus_context.observe(OTHER_PID, "Inbox – Gmail", "Chrome_WidgetWin_1", "comet.exe", 4242)
        monkeypatch.setattr(screen_tool, "capture_window", lambda hwnd: (None, None))
        monkeypatch.setattr(screen_tool, "capture_screenshot", lambda: ("screen", "p"))
        assert pipeline._capture_for_turn() == ("screen", "p")

    def test_nothing_remembered_means_the_screen(self, monkeypatch):
        monkeypatch.setattr(screen_tool, "capture_window",
                            lambda hwnd: pytest.fail("no window to capture"))
        monkeypatch.setattr(screen_tool, "capture_screenshot", lambda: ("screen", "p"))
        assert pipeline._capture_for_turn() == ("screen", "p")


class TestAnalyzeScreenSeesTheSameWindow:
    """analyze_screen — the tool the model picks for "what am I looking at?" —
    still grabbed the whole monitor, Cockpit included."""

    @pytest.fixture
    def sent(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock
        from tools import screen_analysis_tool
        path = tmp_path / "shot.png"
        path.write_bytes(b"png")
        monkeypatch.setattr(screen_tool, "capture_for_mo", lambda: ("WINDOW_B64", str(path)))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        client = MagicMock()
        client.messages.create.return_value.content = [MagicMock(text="It's OSN+.")]
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: client)
        self.path = path
        return screen_analysis_tool, client

    def test_it_sends_the_window_mo_meant(self, sent):
        tool, client = sent
        assert tool.analyze_screen("what am I looking at?") == "It's OSN+."
        image = client.messages.create.call_args.kwargs["messages"][0]["content"][0]
        assert image["source"]["data"] == "WINDOW_B64"
        assert not self.path.exists()               # the temp capture is cleaned up

    def test_no_capture_is_said_plainly(self, sent, monkeypatch):
        tool, client = sent
        monkeypatch.setattr(screen_tool, "capture_for_mo", lambda: (None, None))
        assert "couldn't capture" in tool.analyze_screen().lower()
        client.messages.create.assert_not_called()


class TestCaptureWindow:
    def _fake(self, monkeypatch, image):
        monkeypatch.setattr(screen_tool, "_window_exists", lambda hwnd: True)
        monkeypatch.setattr(whatsapp_desktop, "_capture",
                            lambda hwnd: None if image is None else (image, 0, 0))

    def test_a_drawn_window_becomes_a_png_capped_at_1280_wide(self, monkeypatch):
        image = Image.new("RGB", (2560, 1440), (30, 120, 200))
        self._fake(monkeypatch, image)
        b64, path = screen_tool.capture_window(4242)
        try:
            assert b64
            with Image.open(path) as saved:
                assert saved.size == (1280, 720)
        finally:
            screen_tool.delete_temp_screenshot(path)

    def test_a_minimised_window_gives_nothing(self, monkeypatch):
        self._fake(monkeypatch, None)
        assert screen_tool.capture_window(4242) == (None, None)

    def test_an_all_black_capture_gives_nothing(self, monkeypatch):
        # Some GPU-drawn windows come back black from PrintWindow; a black
        # picture would have the model describe nothing with confidence.
        self._fake(monkeypatch, Image.new("RGB", (800, 600), (0, 0, 0)))
        assert screen_tool.capture_window(4242) == (None, None)

    def test_a_closed_window_gives_nothing(self, monkeypatch):
        monkeypatch.setattr(screen_tool, "_window_exists", lambda hwnd: False)
        monkeypatch.setattr(whatsapp_desktop, "_capture",
                            lambda hwnd: pytest.fail("captured a closed window"))
        assert screen_tool.capture_window(4242) == (None, None)
