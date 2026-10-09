"""
Comet — the browser El Fager opens things in.

Comet is Perplexity's Chromium-based browser. Everything that shows Mo a page
goes through open_url() so there is one place that decides which browser wins.

Default install (per-user):
    %LOCALAPPDATA%\\Perplexity\\Comet\\Application\\Comet.exe
Enterprise/machine-wide installs land under Program Files. Set COMET_PATH in
.env to point somewhere else.

If Comet isn't installed, open_url() falls back to the system default browser
and says so rather than failing silently.

Automation that needs Mo logged in (browser_tool, browser_agent) attaches to his
*running* Comet over the DevTools protocol, so it drives his real profile, his
real cookies and his real sessions. That is why open_url() starts Comet with a
debugging port. research_agent deliberately stays on Playwright's bundled
Chromium: it reads public pages headlessly in bulk, needs no login, and pointing
it at Comet would spray tabs across Mo's browser.

There is no logged-out fallback: Mo wants everything done as him. A Comet he
opened himself has no debugging port, so automation closes it gracefully and
reopens it with one (its tabs come back). Set COMET_REMOTE_DEBUG=0 to turn the
port off, which turns browser automation off with it. The port listens on
127.0.0.1 only, and while it is open any program running as Mo can drive his
logged-in browser.
"""

import os
import subprocess
import time
import webbrowser
from pathlib import Path

_EXE = "Comet.exe"
_APP_SUBPATH = Path("Perplexity") / "Comet" / "Application" / _EXE

# DevTools endpoint used to attach to Mo's running Comet.
_DEBUG_PORT = os.getenv("COMET_DEBUG_PORT", "9222").strip() or "9222"
_CDP_URL = f"http://127.0.0.1:{_DEBUG_PORT}"
_REMOTE_DEBUG = os.getenv("COMET_REMOTE_DEBUG", "1").strip().lower() not in ("0", "false", "no")
_AUTOSTART = os.getenv("COMET_AUTOSTART", "1").strip().lower() not in ("0", "false", "no")
# Comet's folder name for Mo's own profile; "Default" is the one named "mo".
_PROFILE = os.getenv("COMET_PROFILE", "Default").strip() or "Default"

# Resolved once — the install location doesn't move mid-session.
_cached_path: "str | None" = None
_resolved = False


def _candidates() -> "list[Path]":
    paths = []
    override = os.getenv("COMET_PATH", "").strip().strip('"')
    if override:
        paths.append(Path(override))
    for env_var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
        root = os.environ.get(env_var)
        if root:
            paths.append(Path(root) / _APP_SUBPATH)
    return paths


def _from_registry() -> "str | None":
    """Chromium browsers register themselves under App Paths."""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(
                hive,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Comet.exe",
            )
            value, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            if value and os.path.exists(value):
                return value
        except OSError:
            continue
    return None


def comet_path() -> "str | None":
    """Absolute path to Comet.exe, or None when it isn't installed."""
    global _cached_path, _resolved
    if _resolved:
        return _cached_path

    found = None
    for path in _candidates():
        if path.exists():
            found = str(path)
            break
    if found is None:
        found = _from_registry()

    _cached_path = found
    _resolved = True
    if found is None:
        print("[El Fager] Comet not found — falling back to the default browser. "
              "Set COMET_PATH in .env if it's installed somewhere unusual.")
    return found


def reset_cache() -> None:
    """Forget the resolved path (used by tests, and after installing Comet)."""
    global _cached_path, _resolved
    _cached_path = None
    _resolved = False


def is_available() -> bool:
    return comet_path() is not None


def autostart() -> bool:
    """Start Comet minimised, with its debugging port, at El Fager startup.

    A Comet Mo opens himself has no debugging port, and Chromium cannot add one
    to a live process — so automation would run logged out for the rest of the
    session. Getting in first fixes that for good: once a debuggable instance
    exists, Comet launched later (by Mo, by open_url) hands off to it and stays
    attachable.

    Minimised and unfocused, so it doesn't take over the screen at login. Set
    COMET_AUTOSTART=0 to skip it — automation then only gets Mo's logins when
    El Fager happens to open Comet before he does.
    """
    if not _AUTOSTART or not _REMOTE_DEBUG:
        return False
    exe = comet_path()
    if not exe:
        return False
    if cdp_alive():
        return True  # already attachable
    try:
        subprocess.Popen(_launch_args(exe), startupinfo=_minimised())
    except Exception as e:
        print(f"[El Fager] Comet autostart failed: {e}")
        return False
    return True


def _minimised():
    """Windows startupinfo that opens the window minimised, without stealing
    focus. None elsewhere — subprocess.STARTUPINFO is Windows-only."""
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    return si


def _launch_args(exe: str, url: str = "") -> "list[str]":
    # Comet has several profiles (mo, mm, saheb ziad) and a bare launch hands
    # the link to whichever was last active — a video could open signed in as
    # someone else. The flag routes it to Mo's, even into a running Comet.
    args = [exe, f"--profile-directory={_PROFILE}"]
    if _REMOTE_DEBUG:
        # Makes this instance attachable, so automation gets Mo's session.
        # Ignored when Comet is already running: Chromium hands the URL to the
        # existing process, which keeps whatever flags it started with.
        args.append(f"--remote-debugging-port={_DEBUG_PORT}")
    if url:
        args.append(url)
    return args


def open_url(url: str = "") -> bool:
    """Open url in Comet — or just the browser when url is empty. Returns True
    if Comet handled it, False if this fell back to the default browser."""
    exe = comet_path()
    if exe:
        try:
            subprocess.Popen(_launch_args(exe, url))
            return True
        except Exception as e:
            print(f"[El Fager] Comet launch failed ({e}) — using default browser.")
    try:
        webbrowser.open(url or "about:blank")
    except Exception as e:
        print(f"[El Fager] Could not open a browser: {e}")
    return False


def open_comet(url: str = "") -> str:
    """Tool entry point: open Comet, optionally at a URL."""
    target = url.strip()
    used_comet = open_url(target)
    where = "Comet" if used_comet else "your default browser (Comet not found)"
    return f"Opened {target or 'the browser'} in {where}."


# ── Automation against Mo's real profile ──────────────────────────────────────

def cdp_alive(timeout: float = 0.4) -> bool:
    """True when a debuggable Comet is listening."""
    try:
        import httpx
        return httpx.get(f"{_CDP_URL}/json/version", timeout=timeout).status_code == 200
    except Exception:
        return False


class CometUnavailable(RuntimeError):
    """Mo's own Comet can't be driven right now. Automation stops and says why
    rather than falling back to a logged-out browser."""


def _comet_running() -> bool:
    try:
        import psutil
        return any((proc.info.get("name") or "").lower() == _EXE.lower()
                   for proc in psutil.process_iter(["name"]))
    except Exception:
        return False


def _close_comet(timeout: float = 30.0) -> bool:
    """Ask every Comet window to close, as clicking X would — no force, so
    Comet saves the session and restores the tabs when it reopens. Live, with
    ~10 processes open, it took longer than 10 s and the application failed."""
    try:
        subprocess.run(["taskkill", "/IM", _EXE], capture_output=True, timeout=5)
    except Exception as e:
        print(f"[El Fager] Couldn't ask Comet to close: {e}")
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _comet_running():
            return True
        time.sleep(0.25)
    return False


def _start_debuggable_comet(wait_seconds: float = 8.0) -> bool:
    """Start Mo's Comet with its debugging port and wait for it to answer.

    Only works when no Comet is running: a running one takes the launch over
    and keeps the flags it started with.
    """
    exe = comet_path()
    if not exe or not _REMOTE_DEBUG:
        return False
    try:
        subprocess.Popen(_launch_args(exe))
    except Exception as e:
        print(f"[El Fager] Couldn't start Comet for automation: {e}")
        return False

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if cdp_alive():
            return True
        time.sleep(0.25)
    return False


def automation_context(playwright, headless: bool = False):
    """Return (browser, context, owned) for automation.

    Visible automation only ever drives Mo's own signed-in Comet profile
    (_PROFILE), attached over the debugging port; `owned` is False, and callers
    must leave his browser and tabs open when they're done. A Comet he opened
    himself has no port, so it is closed gracefully and reopened with one —
    Comet restores its tabs. If none of that works, CometUnavailable says why;
    there is no logged-out fallback.

    headless=True is for reading public pages in bulk, which needs no login and
    must not open tabs in front of Mo: it launches Playwright's own Chromium and
    `owned` is True.
    """
    if headless:
        browser = playwright.chromium.launch(headless=True)
        return browser, browser.new_context(), True

    if not comet_path():
        raise CometUnavailable("Comet isn't installed, so I can't use your signed-in browser.")
    if not _REMOTE_DEBUG:
        raise CometUnavailable(
            "COMET_REMOTE_DEBUG is off, so I can't drive your signed-in Comet.")

    if not cdp_alive():
        if _comet_running():
            print("[El Fager] Reopening Comet with its debugging port — its tabs come back.")
            if not _close_comet():
                raise CometUnavailable(
                    "Comet didn't close, so I couldn't reopen it signed in for this. "
                    "Close it and ask me again.")
        if not _start_debuggable_comet():
            raise CometUnavailable("Comet didn't start in a way I can drive. Try again in a moment.")

    try:
        browser = playwright.chromium.connect_over_cdp(_CDP_URL)
    except Exception as e:
        raise CometUnavailable(f"Couldn't attach to your Comet ({e}).") from e
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, context, False
