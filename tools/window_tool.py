"""
Window management tools for El Fager.

List, switch, resize, minimize, maximize, move, and snap any open window.
Uses pygetwindow which wraps win32 APIs on Windows.

read_window_text reads what a window says through UI Automation: exact text,
even while El Fager covers the window. It only reads — nothing is clicked,
typed or focused.
"""
import time


def _find_window(title: str):
    """Return the first window whose title contains `title` (case-insensitive)."""
    import pygetwindow as gw
    title_lower = title.lower()
    matches = [w for w in gw.getAllWindows() if title_lower in w.title.lower() and w.title.strip()]
    if not matches:
        return None, f"[No window found matching '{title}']"
    return matches[0], None


def list_windows(filter: str = "") -> str:
    """List all visible window titles, optionally filtered by a keyword."""
    try:
        import pygetwindow as gw
        windows = [w.title for w in gw.getAllWindows() if w.title.strip()]
        if filter:
            f = filter.lower()
            windows = [t for t in windows if f in t.lower()]
        if not windows:
            return "No windows found." if not filter else f"No windows matching '{filter}'."
        return "Open windows:\n" + "\n".join(f"  - {t}" for t in windows)
    except Exception as e:
        return f"[list_windows failed: {e}]"


def get_active_window() -> str:
    """Return the title and position of the currently focused window."""
    try:
        # Asking El Fager puts El Fager in front; Mo means what was there before.
        from core import focus_context
        if focus_context.el_fager_in_front() and focus_context.describe():
            return (f"El Fager's own window is in front now. Before that, Mo was "
                    f"in {focus_context.describe()}.")
        import pygetwindow as gw
        w = gw.getActiveWindow()
        if not w:
            return "No active window detected."
        return f"Active: '{w.title}' at ({w.left}, {w.top}), size {w.width}x{w.height}."
    except Exception as e:
        return f"[get_active_window failed: {e}]"


# ── Reading a window's text ──────────────────────────────────────────────────

_READ_MAX_CHARS = 3000
_EDIT = 50004
# UI Automation control types whose name is text on screen. Panes, groups and
# windows are containers: their names are class names, not words to read.
_TEXT_TYPES = frozenset({
    50000,  # button
    50002,  # check box
    50004,  # edit
    50005,  # hyperlink
    50007,  # list item
    50011,  # menu item
    50013,  # radio button
    50019,  # tab item
    50020,  # text
    50024,  # tree item
    50029,  # data item
    50030,  # document
    50034,  # header
    50035,  # header item
})
_VALUE_PROPERTY = 30045     # UIA_ValueValuePropertyId: an edit box's contents


def _collect_text(root, walker, value_of, max_nodes: int = 1500,
                  seconds: float = 3.0) -> "list[str]":
    """The on-screen text under `root`, in reading order.

    Bounded by element count and time: a WebView's tree is large, and walking
    all of it once took seconds. Off-screen subtrees are skipped — scrolled
    out of view is not what Mo is looking at."""
    deadline = time.monotonic() + seconds
    lines: "list[str]" = []
    stack, visited = [root], 0
    while stack and visited < max_nodes and time.monotonic() < deadline:
        element = stack.pop()
        visited += 1
        try:
            if element.CurrentIsOffscreen:
                continue
            ctype = element.CurrentControlType
            text = " ".join((element.CurrentName or "").split())
            if ctype == _EDIT:
                value = " ".join((value_of(element) or "").split())
                if value:
                    text = f"{text}: {value}" if text else value
            if ctype in _TEXT_TYPES and text and (not lines or lines[-1] != text):
                lines.append(text)
            children = []
            child = walker.GetFirstChildElement(element)
            while child:
                children.append(child)
                child = walker.GetNextSiblingElement(child)
        except Exception:
            continue            # an element that vanished mid-read
        stack.extend(reversed(children))
    return lines


def _read_hwnd(hwnd: int) -> "list[str]":
    import ctypes

    from tools.whatsapp_desktop import _uia

    _, uia = _uia()
    root = uia.ElementFromHandle(ctypes.c_void_p(hwnd))
    return _collect_text(
        root, uia.RawViewWalker,
        value_of=lambda el: str(el.GetCurrentPropertyValue(_VALUE_PROPERTY) or ""))


def read_window_text(title: str = "") -> str:
    """What a window says. No title: the window Mo was in before El Fager."""
    try:
        from core import focus_context

        if title:
            win, err = _find_window(title)
            if err:
                return err
            hwnd, name = win._hWnd, win.title
        else:
            seen = focus_context.last()
            if focus_context.el_fager_in_front() and seen and seen.get("hwnd"):
                hwnd, name = seen["hwnd"], seen["title"]
            else:
                import pygetwindow as gw
                w = gw.getActiveWindow()
                if not w:
                    return "No active window detected."
                hwnd, name = w._hWnd, w.title
        lines = _read_hwnd(hwnd)
        if not lines:
            return f'No readable text in "{name}" — a screenshot may show more.'
        text = "\n".join(lines)
        if len(text) > _READ_MAX_CHARS:
            text = text[:_READ_MAX_CHARS] + "\n[... cut]"
        return f'Text in "{name}":\n{text}'
    except Exception as e:
        return f"[read_window_text failed: {e}]"


def switch_to_window(title: str) -> str:
    """Bring a window to the foreground by partial title match."""
    try:
        win, err = _find_window(title)
        if err:
            return err
        # pygetwindow.activate() can silently fail on Windows 11 due to focus-steal prevention.
        # Directly use win32gui as the primary method for reliability.
        try:
            import win32gui, win32con
            hwnd = win.getHandle()
            if win32gui.IsIconic(hwnd):  # minimized — restore first
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            win.activate()  # fallback to pygetwindow
        return f"Switched to: '{win.title}'."
    except Exception as e:
        return f"[switch_to_window failed: {e}]"


def minimize_window(title: str = None) -> str:
    """Minimize a window by title (or the active window if title is omitted)."""
    try:
        import pygetwindow as gw
        if title:
            win, err = _find_window(title)
            if err:
                return err
        else:
            win = gw.getActiveWindow()
            if not win:
                return "[No active window to minimize.]"
        win.minimize()
        return f"Minimized: '{win.title}'."
    except Exception as e:
        return f"[minimize_window failed: {e}]"


def maximize_window(title: str = None) -> str:
    """Maximize a window by title (or the active window if title is omitted)."""
    try:
        import pygetwindow as gw
        if title:
            win, err = _find_window(title)
            if err:
                return err
        else:
            win = gw.getActiveWindow()
            if not win:
                return "[No active window to maximize.]"
        win.maximize()
        return f"Maximized: '{win.title}'."
    except Exception as e:
        return f"[maximize_window failed: {e}]"


def restore_window(title: str = None) -> str:
    """Restore a minimized/maximized window to normal size."""
    try:
        import pygetwindow as gw
        if title:
            win, err = _find_window(title)
            if err:
                return err
        else:
            win = gw.getActiveWindow()
            if not win:
                return "[No active window to restore.]"
        win.restore()
        return f"Restored: '{win.title}'."
    except Exception as e:
        return f"[restore_window failed: {e}]"


def close_window(title: str) -> str:
    """Send a close signal to a window (app may prompt to save unsaved work)."""
    try:
        win, err = _find_window(title)
        if err:
            return err
        win.close()
        return f"Closed: '{win.title}'."
    except Exception as e:
        return f"[close_window failed: {e}]"


def resize_window(title: str, width: int, height: int) -> str:
    """Resize a window to specific dimensions in pixels."""
    try:
        win, err = _find_window(title)
        if err:
            return err
        win.resizeTo(width, height)
        return f"Resized '{win.title}' to {width}x{height}."
    except Exception as e:
        return f"[resize_window failed: {e}]"


def move_window(title: str, x: int, y: int) -> str:
    """Move a window's top-left corner to screen coordinates (x, y)."""
    try:
        win, err = _find_window(title)
        if err:
            return err
        win.moveTo(x, y)
        return f"Moved '{win.title}' to ({x}, {y})."
    except Exception as e:
        return f"[move_window failed: {e}]"


def snap_window(title: str, position: str) -> str:
    """Snap a window to a screen half or quarter. position: left, right, top-left, top-right, bottom-left, bottom-right, maximized."""
    try:
        import pyautogui
        win, err = _find_window(title)
        if err:
            return err

        sw, sh = pyautogui.size()
        half_w, half_h = sw // 2, sh // 2

        positions = {
            "left":         (0, 0, half_w, sh),
            "right":        (half_w, 0, half_w, sh),
            "top-left":     (0, 0, half_w, half_h),
            "top-right":    (half_w, 0, half_w, half_h),
            "bottom-left":  (0, half_h, half_w, half_h),
            "bottom-right": (half_w, half_h, half_w, half_h),
            "maximized":    (0, 0, sw, sh),
            "center":       (sw // 4, sh // 4, half_w, half_h),
        }

        pos = position.lower()
        if pos not in positions:
            opts = ", ".join(positions.keys())
            return f"[Unknown position '{position}'. Options: {opts}]"

        x, y, w, h = positions[pos]
        win.restore()
        win.moveTo(x, y)
        win.resizeTo(w, h)
        return f"Snapped '{win.title}' to {position} ({w}x{h} at {x},{y})."
    except Exception as e:
        return f"[snap_window failed: {e}]"
