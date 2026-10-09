"""
El Fager — what Mo was looking at before he turned to it.

Calling El Fager covers the thing he means: the Cockpit raises itself and takes
focus, so by the time his words arrive the window in front is El Fager's own.
"What's this" had nothing to point at.

A background poll remembers the last foreground window that isn't El Fager —
the app, the title and when he left it — and the prompt carries it on every
turn. Polling rather than hooking each way in (wake word, hotkey, a click on
the Cockpit) means none of them can be missed.

Qt-free, and like telemetry it never raises: a failed read keeps what was
remembered before.
"""
import os
import threading
import time

POLL_SEC = 0.5

# The taskbar and the desktop take focus when clicked; neither is "this".
_SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}

_lock = threading.Lock()
_last: "dict | None" = None
_started = False


def observe(pid: int, title: str, class_name: str, app: str, hwnd: int = 0) -> None:
    """One foreground reading. Remembered unless it's El Fager or the shell."""
    global _last
    if pid == os.getpid() or not title.strip() or class_name in _SHELL_CLASSES:
        return
    if app.lower().endswith(".exe"):
        app = app[:-4]
    with _lock:
        _last = {"app": app, "title": title.strip()[:120], "at": time.monotonic(),
                 "hwnd": hwnd}


def _foreground() -> "tuple[int, str, str, str, int] | None":
    """(pid, title, class name, process name, handle) of the window in front."""
    try:
        import ctypes
        from ctypes import wintypes

        import psutil

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, len(cls))
        return pid.value, title.value, cls.value, psutil.Process(pid.value).name(), hwnd
    except Exception:
        return None


def el_fager_in_front() -> bool:
    fg = _foreground()
    return fg is not None and fg[0] == os.getpid()


def last() -> "dict | None":
    with _lock:
        return dict(_last) if _last else None


def describe() -> str:
    """One line for the prompt, or "" when nothing has been remembered."""
    seen = last()
    if not seen:
        return ""
    ago = time.monotonic() - seen["at"]
    if ago < 60:
        when = f"{ago:.0f} s ago"
    elif ago < 3600:
        when = f"{ago / 60:.0f} min ago"
    else:
        when = "over an hour ago"
    return f'{seen["app"]} — "{seen["title"]}" ({when})'


def start() -> None:
    """Begin polling. Called once from main.py; tests never start it."""
    global _started
    if _started:
        return
    _started = True

    def _loop():
        while True:
            fg = _foreground()
            if fg is not None:
                observe(*fg)
            time.sleep(POLL_SEC)

    threading.Thread(target=_loop, daemon=True, name="FocusContext").start()


def reset() -> None:
    """Test hook."""
    global _last
    with _lock:
        _last = None
