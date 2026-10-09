"""Clicking the desktop icon while El Fager is already running.

main.py holds a single-instance mutex, and a second launch used to find it,
raise a toast saying "Already running. Check the system tray.", and exit. The
shortcut runs pythonw, so there is no console either — from Mo's side the icon
simply did nothing, and the app he was trying to open never appeared.

A second launch now signals a named event that the running instance waits on,
and that instance brings its overlay forward. The second process still exits;
the difference is that it hands off first.
"""
import ctypes
import threading
import time
from pathlib import Path

import pytest

import main


@pytest.fixture(autouse=True)
def unique_event_name(monkeypatch):
    """Never touch the real instance's event while the tests run."""
    monkeypatch.setattr(main, "_SHOW_EVENT_NAME",
                        f"ElFagerShowRequestedTest{time.monotonic_ns()}")
    # Each test starts with no door open, whichever one opened it last.
    monkeypatch.setattr(main, "_SHOW_EVENT_HANDLE", None)


class _Signaler:
    """Stands in for HotkeySignaler.second_launch."""

    def __init__(self):
        self.fired = threading.Event()
        self.count = 0

    @property
    def second_launch(self):
        return self

    def emit(self):
        self.count += 1
        self.fired.set()


class TestHandoff:
    def test_signalling_with_nobody_listening_reports_failure(self):
        # The mutex is held but no watcher exists — an older build, or a
        # process wedged mid-shutdown. main.py falls back to a toast, so this
        # must answer honestly rather than pretend it was delivered.
        assert main._signal_running_instance() is False

    def test_a_watcher_receives_the_signal(self):
        signaler = _Signaler()
        main._watch_for_second_launch(signaler)
        time.sleep(0.1)                       # let the wait thread start

        assert main._signal_running_instance() is True
        assert signaler.fired.wait(timeout=3.0), \
            "the running instance never heard the second launch"

    def test_each_launch_wakes_the_watcher_once(self):
        # Auto-reset: three clicks are three separate summons, not one.
        signaler = _Signaler()
        main._watch_for_second_launch(signaler)
        time.sleep(0.1)
        for _ in range(3):
            assert main._signal_running_instance() is True
            time.sleep(0.15)
        assert signaler.count == 3, f"expected 3 wake-ups, got {signaler.count}"

    def test_the_watcher_does_not_block_startup(self):
        signaler = _Signaler()
        started = time.monotonic()
        main._watch_for_second_launch(signaler)
        assert time.monotonic() - started < 0.5, \
            "the watcher blocked the main thread instead of running as a daemon"

    def test_the_watcher_thread_is_a_daemon_so_quit_still_works(self):
        before = {t.ident for t in threading.enumerate()}
        main._watch_for_second_launch(_Signaler())
        time.sleep(0.1)
        new = [t for t in threading.enumerate() if t.ident not in before]
        assert new, "no watcher thread was started"
        assert all(t.daemon for t in new), \
            "a non-daemon watcher would keep the process alive after Quit"


class TestTheEventItself:
    def test_the_name_is_stable_so_both_processes_agree(self):
        # Both sides look the event up by name; if they ever disagree the
        # handoff silently stops working and the icon looks broken again.
        assert isinstance(main._SHOW_EVENT_NAME, str)
        assert main._SHOW_EVENT_NAME

    def test_opening_a_missing_event_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(main, "_SHOW_EVENT_NAME", "ElFagerDefinitelyNotThere")
        assert main._signal_running_instance() is False

    def test_a_failed_event_creation_leaves_startup_alone(self, monkeypatch):
        # If CreateEventW fails, the app must still boot — losing the handoff
        # is a nuisance, refusing to start is not acceptable.
        real = ctypes.windll.kernel32.CreateEventW
        monkeypatch.setattr(ctypes.windll.kernel32, "CreateEventW",
                            lambda *a: 0)
        try:
            main._watch_for_second_launch(_Signaler())      # must not raise
        finally:
            monkeypatch.setattr(ctypes.windll.kernel32, "CreateEventW", real)


class TestReachableWhileStillStarting:
    """The gap between taking the lock and being able to hear a click.

    Mo opens El Fager from the desktop icon. If it is already running he
    should get the window he asked for; what he got instead, during the
    seconds the first instance spent loading Whisper and Chroma, was a toast
    saying it was not responding.
    """

    def test_the_event_exists_as_soon_as_it_is_created(self):
        assert main._signal_running_instance() is False,             "nothing should be reachable before the event is created"
        main._create_show_event()
        assert main._signal_running_instance() is True,             "a second launch cannot reach an instance that has taken the lock"

    def test_a_click_during_startup_is_delivered_not_dropped(self):
        # The click lands while the first instance is still building itself,
        # long before the waiter thread exists.
        main._create_show_event()
        assert main._signal_running_instance() is True

        # ...and the waiter starts only afterwards, as main() gets that far.
        signaler = _Signaler()
        main._watch_for_second_launch(signaler)

        assert signaler.fired.wait(timeout=3.0),             "the summon was dropped because the waiter had not started yet"

    def test_the_waiter_reuses_the_handle_from_startup(self):
        handle = main._create_show_event()
        main._watch_for_second_launch(_Signaler())
        assert main._SHOW_EVENT_HANDLE == handle,             "the waiter replaced the handle a second launch had already found"

    def test_a_failed_creation_reports_it(self, monkeypatch):
        real = ctypes.windll.kernel32.CreateEventW
        monkeypatch.setattr(ctypes.windll.kernel32, "CreateEventW",
                            lambda *a: 0)
        try:
            assert main._create_show_event() is None
        finally:
            monkeypatch.setattr(ctypes.windll.kernel32, "CreateEventW", real)

    def test_startup_opens_the_door_before_it_loads_anything(self):
        """Ordering is the whole fix, so the order itself is the assertion."""
        source = Path(main.__file__).read_text(encoding="utf-8")
        body = source[source.index("def main():"):]
        opened = body.index("_create_show_event()")
        for slow in ("VoiceInput()", "Memory()", "Brain(", "OverlayWindow("):
            assert opened < body.index(slow),                 f"a click landing while {slow} is built would find nobody home"


class TestTheWindowComesForward:
    """A summon that opens behind the browser is not a summon.

    The instance being woken sits in the background, and Windows does not let
    a background process take the foreground. The process Mo launched does
    hold that right, so it grants it before it exits.
    """

    def test_the_foreground_is_handed_over_before_the_signal(self, monkeypatch):
        main._create_show_event()
        order = []

        real_allow = ctypes.windll.user32.AllowSetForegroundWindow
        real_set = ctypes.windll.kernel32.SetEvent

        def allow(pid):
            order.append(("allow", pid))
            return real_allow(pid)

        def set_event(handle):
            order.append(("set", handle))
            return real_set(handle)

        monkeypatch.setattr(ctypes.windll.user32, "AllowSetForegroundWindow", allow)
        monkeypatch.setattr(ctypes.windll.kernel32, "SetEvent", set_event)
        try:
            assert main._signal_running_instance() is True
        finally:
            monkeypatch.setattr(ctypes.windll.user32,
                                "AllowSetForegroundWindow", real_allow)
            monkeypatch.setattr(ctypes.windll.kernel32, "SetEvent", real_set)

        assert [step for step, _ in order] == ["allow", "set"],             "the right to the foreground must be granted before the wake-up"
        assert order[0][1] == -1, "ASFW_ANY (-1) is what grants it"

    def test_nothing_is_granted_when_there_is_nobody_to_grant_it_to(self, monkeypatch):
        # No event: the instance is wedged or gone. Handing the foreground to
        # nothing would leave Mo's own window able to be stolen for nothing.
        calls = []
        real_allow = ctypes.windll.user32.AllowSetForegroundWindow
        monkeypatch.setattr(ctypes.windll.user32, "AllowSetForegroundWindow",
                            lambda pid: calls.append(pid))
        try:
            assert main._signal_running_instance() is False
        finally:
            monkeypatch.setattr(ctypes.windll.user32,
                                "AllowSetForegroundWindow", real_allow)
        assert calls == [], "granted the foreground with no instance listening"
