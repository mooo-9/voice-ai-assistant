"""
El Fager — Command Center.

The design's dashboard surface, laid out on its 12-column grid: greeting and
the assistant's own briefing prose, then the data cards (calendar, tasks,
mail on the top row; health and news beneath), then the "it's getting to know
you" row, then the global command bar. Dawn palette — cyan belongs to the
Cockpit.

Three rules from the handoff shape everything here:

  * Nothing costs open latency. Cards paint from
    data/command_center_cache.json first and refresh in daemon threads; the
    briefing prose is regenerated on every open but arrives when it arrives.
  * Every card footer carries exactly ONE action, and it is an action you
    could equally have spoken.
  * Outbound actions keep stage → preview → confirm, because the command bar
    dispatches into the same PipelineWorker and the same tools as the voice
    path. There is no second send path on this surface.

Fully native: no QWebEngine anywhere. The day-arc, macro bars and sparklines
are QPainter widgets from ui/widgets.py, fed from local data files.
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import prose
from ui import theme, tokens
from ui.overlay import MessageBubble, _load_settings, _save_settings
from ui.widgets import Chip, DayArc, FlowHost, MacroBar, Sparkline, StatTile

_CACHE_FILE = Path("data/command_center_cache.json")
_DATA_DIR = Path(__file__).parent.parent / "data"

# 12 columns, 24px gutter, 48px margin, min width 1100 — the handoff's grid.
_COLUMNS = 12
_GUTTER = tokens.S6
_MARGIN = tokens.S8
_RESPONSE_MAX_HEIGHT = 260    # past this a long answer scrolls in its box
_MIN_WIDTH = 1100


# ──────────────────────────────────────────────────────────────────────────────
# Card data fetchers — each reuses an existing tool read function.
# All of them return a human-readable string even when unconfigured.
# ──────────────────────────────────────────────────────────────────────────────


def _fetch_calendar() -> str:
    from tools.calendar_tool import list_events
    return list_events("today", max_results=6)


def _fetch_tasks() -> str:
    from tools.todoist_tool import list_tasks
    return list_tasks("today")


def _fetch_mail() -> str:
    from tools.gmail_tool import list_messages
    return list_messages(n=5, unread_only=True)


def _fetch_news() -> str:
    from tools.news_tool import get_all_headlines
    return get_all_headlines(n=2)


def _fetch_health() -> str:
    from tools.health_tool import nutrition_summary, todays_workout
    return nutrition_summary() + "\n" + todays_workout()


_CARDS = [
    ("calendar", "CALENDAR", _fetch_calendar),
    ("tasks", "TASKS", _fetch_tasks),
    ("mail", "MAIL", _fetch_mail),
    ("health", "HEALTH", _fetch_health),
    ("news", "NEWS — YOUR BRIEF", _fetch_news),
]

# Where each card sits on the grid: (row, column, column span).
_CARD_CELLS = {
    "calendar": (0, 0, 4),
    "tasks": (0, 4, 4),
    "mail": (0, 8, 4),
    "health": (1, 0, 7),
    "news": (1, 7, 5),
}

# The one action per card footer. "say" dispatches the phrase into the same
# pipeline a spoken turn uses; "listen" opens the mic, because the phrase is
# only half of the ask (which meal? which task?).
_CARD_ACTIONS = {
    "calendar": ("Plan tomorrow →", "say",
                 "What's on my calendar tomorrow? Help me plan the day around it."),
    "tasks": ("Triage by voice →", "listen", ""),
    "mail": ("Draft replies →", "say",
             "Go through my unread mail and draft a reply to anything that "
             "needs one. Stage them for me to look at — don't send."),
    "health": ("Log a meal by voice →", "listen", ""),
    "news": ("Read it to me →", "say", "Read me today's news brief."),
}

# (singular, plural) noun for each card's header count chip ("1 event",
# "5 unread"…). Health has no chip — its arc + bars already carry the numbers.
_CARD_NOUNS = {
    "calendar": ("event", "events"),
    "tasks": ("task", "tasks"),
    "mail": ("unread", "unread"),
    "news": ("story", "stories"),
}

_MAX_FACT_CHIPS = 6
_MAX_SKILL_CHIPS = 6

# How long a briefing stays good before an open regenerates it. 0 means never
# — the prose then only changes when you press Refresh. Override in
# data/settings.json with "briefing_ttl_minutes".
_BRIEFING_TTL_DEFAULT = 30

# The briefing is written from what the cards already fetched, so it can only
# ever describe what is on screen — and it costs one short call, not a tool
# loop that goes and fetches everything a second time.
_BRIEFING_SYSTEM = (
    "You are El Fager, Mo's assistant. From the notes you are given, write his "
    "briefing as two to four short sentences of plain prose: what actually "
    "needs him today and what to do about it, hardest thing first. No lists, "
    "no headings, no greeting, no preamble. You may close with one short "
    "question if it earns its place. Never state anything the notes do not say."
)


def _mono(size: int, color: str, tracking: float = 1.4) -> str:
    return (
        f"color: {color}; font-family: {theme.FONT_MONO}; font-size: {size}px;"
        f" letter-spacing: {tracking}px; background: transparent; border: none;"
    )


def _split_items(text: str) -> "tuple[str | None, list[str]]":
    """Split a tool's human-readable text into (preamble, item list).

    Blank-line-separated blocks (mail) become one item each; otherwise each
    non-empty line (calendar/tasks/news) is an item. A lone first unit ending
    with ":" is a preamble, not an item.
    """
    t = text.strip()
    if "\n\n" in t:
        units = [b.strip() for b in t.split("\n\n") if b.strip()]
    else:
        units = [ln.strip() for ln in t.splitlines() if ln.strip()]
    pre = None
    if len(units) > 1 and units[0].endswith(":"):
        pre = units[0]
        units = units[1:]
    return pre, units


def _greeting() -> str:
    from core.briefing import CAIRO_TZ
    h = datetime.now(CAIRO_TZ).hour
    if h < 12:
        return "Good morning, Mo"
    if h < 17:
        return "Good afternoon, Mo"
    return "Good evening, Mo"


def _fetch_weather_compact() -> str:
    """First line of today's weather, compacted for the title bar ("" hides it)."""
    import re
    from tools.weather_tool import get_weather
    first = get_weather("Cairo").splitlines()[0]
    if "—" in first:
        first = first.split("—", 1)[1].strip()
    if first.startswith("[") or "not found" in first:
        return ""
    return re.sub(r"\s*\(feels[^)]*\)", "", first)


def _spend_series(days: int = 7) -> "list[float]":
    """Per-day API spend for the last `days` days, oldest first.

    core/telemetry.py is reference-only for this surface, so the day grouping
    happens here, off its entry iterator.
    """
    from core.telemetry import _iter_entries
    totals: dict[str, float] = {}
    for e in _iter_entries(days):
        day = str(e.get("timestamp", ""))[:10]
        totals[day] = totals.get(day, 0.0) + float(e.get("cost_usd", 0) or 0)
    return [
        totals.get((datetime.now() - timedelta(days=o)).strftime("%Y-%m-%d"), 0.0)
        for o in range(days - 1, -1, -1)
    ]


def _meal_series(key: str, days: int = 7) -> "list[float]":
    """Per-day totals from data/meal_log.json, oldest first.

    The design's health card ends in two 8-week sparklines for weight and
    sleep; neither is logged anywhere in El Fager, so these plot the two
    series that do exist rather than inventing the two that don't.
    """
    try:
        log = json.loads((_DATA_DIR / "meal_log.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    by_day = {
        e.get("date"): (e.get("totals", {}) or {}).get(key, 0)
        for e in log.get("entries", [])
    }
    return [
        float(by_day.get((datetime.now() - timedelta(days=o)).strftime("%Y-%m-%d"), 0))
        for o in range(days - 1, -1, -1)
    ]


def _read_nutrition() -> "dict | None":
    """Local-file nutrition snapshot (same sources as ui/hud_scene_hub.py)."""
    try:
        profile_path = _DATA_DIR / "health_profile.json"
        if not profile_path.exists():
            return None
        p = json.loads(profile_path.read_text(encoding="utf-8"))
        targets = p.get("targets", {})
        overrides = p.get("overrides", {})
        out = {
            "kcal": round(targets.get("kcal", 0)),
            "protein": round(overrides.get("protein_g", targets.get("protein_g", 0))),
            "carbs": round(targets.get("carbs_g", 0)),
            "fat": round(targets.get("fat_g", 0)),
            "logged_kcal": 0, "logged_protein": 0, "logged_carbs": 0, "logged_fat": 0,
        }
        meal_log_path = _DATA_DIR / "meal_log.json"
        if meal_log_path.exists():
            log_data = json.loads(meal_log_path.read_text(encoding="utf-8"))
            today_s = datetime.now().date().isoformat()
            entry = next(
                (e for e in log_data.get("entries", []) if e.get("date") == today_s),
                None,
            )
            if entry:
                totals = entry.get("totals", {})
                out["logged_kcal"] = round(totals.get("kcal", 0))
                out["logged_protein"] = round(totals.get("protein_g", 0))
                out["logged_carbs"] = round(totals.get("carbs_g", 0))
                out["logged_fat"] = round(totals.get("fat_g", 0))
        return out
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Cross-thread bridge: fetchers run in daemon threads; Qt queues the signal
# delivery back to the main thread (same pattern as main.HotkeySignaler).
# ──────────────────────────────────────────────────────────────────────────────


class _CardSignaler(QObject):
    card_ready = pyqtSignal(str, str)   # (card_id, text)
    weather_ready = pyqtSignal(str)     # compact title-bar weather ("" = hide)
    briefing_ready = pyqtSignal(str)    # the assistant's synthesis ("" = keep cached)


# ──────────────────────────────────────────────────────────────────────────────
# One data card: title + count, a body, and exactly one action in the footer.
# ──────────────────────────────────────────────────────────────────────────────


class _Card(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("dataCard")
        self.setStyleSheet(theme.DATA_CARD)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        column = QVBoxLayout(self)
        column.setContentsMargins(16, 14, 16, 12)
        column.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        label = QLabel(title)
        label.setStyleSheet(_mono(11, tokens.TEXT_LOW, 2.0))
        head.addWidget(label)
        self.chip = Chip("")
        self.chip.setVisible(False)
        head.addWidget(self.chip)
        head.addStretch()
        column.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 2, 0, 0)
        self.body.setSpacing(0)
        column.addLayout(self.body)
        column.addStretch()

        rule = QWidget()
        rule.setFixedHeight(1)
        rule.setStyleSheet(theme.SEPARATOR)
        column.addWidget(rule)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 6, 0, 0)
        self.meta = QLabel("")
        self.meta.setStyleSheet(_mono(10, tokens.TEXT_LOW))
        foot.addWidget(self.meta)
        foot.addStretch()
        self.action = QPushButton("")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.setStyleSheet(
            f"QPushButton {{ color: {theme.ACCENT_BRIGHT}; background: transparent;"
            f" border: none; font-family: {theme.FONT}; font-size: 12px;"
            f" font-weight: 500; padding: 2px 0; text-align: right; }}"
            f"QPushButton:hover {{ color: {theme.SPEAKING}; }}"
            f"QPushButton:disabled {{ color: {tokens.TEXT_LOW}; }}"
        )
        foot.addWidget(self.action)
        column.addLayout(foot)


# ──────────────────────────────────────────────────────────────────────────────
# Command Center window
# ──────────────────────────────────────────────────────────────────────────────


class CommandCenterWindow(QWidget):
    def __init__(self, voice_in, brain, voice_out, memory, wake_listener=None):
        super().__init__()
        self.voice_in = voice_in
        self.brain = brain
        self.voice_out = voice_out
        self.memory = memory
        self._wake_listener = wake_listener
        self._worker = None
        self._cards: dict[str, _Card] = {}
        self._card_rows: dict[str, QVBoxLayout] = {}
        self._card_texts: dict[str, str] = {}       # raw text per card (cache + diffing)
        self._pending: set[str] = set()
        self._heard_bubbled = False                 # this turn's words are on screen
        self._signaler = _CardSignaler()
        self._signaler.card_ready.connect(self._on_card_ready)
        self._signaler.weather_ready.connect(self._on_weather_ready)
        self._signaler.briefing_ready.connect(self._on_briefing_ready)
        self._weather_at = None       # throttle: title-bar weather every 15 min
        self._briefing_pending = False
        self._briefing_wanted = False  # stale, but waiting on today's cards
        self._proposal: "dict | None" = None

        self._setup_window()
        self._build_ui()
        self._apply_theme()
        self._load_cache()
        self._update_system_strip()
        self._refresh_weather()

    # ------------------------------------------------------------------ #
    #  Window setup (frameless card, same conventions as ui/overlay.py)   #
    # ------------------------------------------------------------------ #

    def _setup_window(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setWindowTitle("El Fager — Command Center")
        self.setMinimumSize(_MIN_WIDTH, 720)

        settings = _load_settings()
        geom = settings.get("command_center_geometry")
        if geom and len(geom) == 4 and geom[2] >= _MIN_WIDTH:
            self.setGeometry(*geom)
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            width = min(1280, max(_MIN_WIDTH, screen.width() - 160))
            height = min(880, max(720, screen.height() - 140))
            self.resize(width, height)
            self.move((screen.width() - width) // 2, max(20, (screen.height() - height) // 2))

        self._drag_start = None
        self._drag_origin = None

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        self._card = QWidget(self)
        self._card.setObjectName("card")
        outer.addWidget(self._card)

        inner = QVBoxLayout(self._card)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        inner.addWidget(self._build_title_bar())

        # ── Scrolling content: hero, then the 12-column grid ──────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(theme.SCROLL_AREA)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_col = QVBoxLayout(content)
        content_col.setContentsMargins(_MARGIN, tokens.S6, _MARGIN, tokens.S4)
        content_col.setSpacing(tokens.S5)
        content_col.addWidget(self._build_hero())
        content_col.addWidget(self._build_grid())
        content_col.addStretch()

        scroll.setWidget(content)
        inner.addWidget(scroll, 1)

        inner.addWidget(self._build_command_bar())

    # ── Title bar ─────────────────────────────────────────────────────────

    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background: transparent;")
        row = QHBoxLayout(bar)
        row.setContentsMargins(_MARGIN, 13, tokens.S5, 13)
        row.setSpacing(10)

        name = QLabel("El Fager — Command Center")
        name.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-family: {theme.FONT};"
            f" font-size: 13px; font-weight: 500; background: transparent;"
        )
        row.addWidget(name)

        # Assistant-state dot — mirrors the pipeline so the surface feels alive.
        self._state_chip = Chip("idle", dot=theme.STATE_COLORS["idle"])
        row.addWidget(self._state_chip)
        row.addStretch()

        self._date_label = QLabel(datetime.now().strftime("%A, %B %d").upper())
        self._date_label.setStyleSheet(_mono(11, tokens.TEXT_LOW))
        row.addWidget(self._date_label)

        self._weather_label = QLabel("")
        self._weather_label.setVisible(False)
        self._weather_label.setStyleSheet(_mono(11, tokens.TEXT_LOW))
        row.addWidget(self._weather_label)

        self._clock_label = QLabel(datetime.now().strftime("%I:%M %p").lstrip("0"))
        self._clock_label.setStyleSheet(_mono(13, theme.TEXT_PRIMARY, 1.0))
        row.addWidget(self._clock_label)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(10_000)
        self._clock_timer.timeout.connect(self._paint_clock)
        self._clock_timer.start()

        # Space Grotesk carries U+2212/U+00D7 but not the box-drawing dash or
        # U+2715, and relying on font fallback for a button label is a coin flip.
        min_btn = QPushButton("−")
        min_btn.setFixedSize(22, 20)
        min_btn.setStyleSheet(theme.BTN_GHOST)
        min_btn.setToolTip("Hide")
        min_btn.clicked.connect(self._hide)
        row.addWidget(min_btn)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 20)
        close_btn.setStyleSheet(theme.BTN_CLOSE)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self._hide)
        row.addWidget(close_btn)
        return bar

    def _paint_clock(self):
        self._clock_label.setText(datetime.now().strftime("%I:%M %p").lstrip("0"))

    # ── Hero: the greeting and the assistant's own briefing ───────────────

    def _build_hero(self) -> QWidget:
        hero = QWidget()
        hero.setStyleSheet("background: transparent;")
        col = QVBoxLayout(hero)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        self._greeting_label = QLabel(_greeting())
        # The display step is weight 600; QSS synthesizes it, the axis doesn't.
        self._greeting_label.setFont(theme.ui_font(32, 600))
        self._greeting_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        col.addWidget(self._greeting_label)

        self._briefing_label = QLabel("")
        self._briefing_label.setWordWrap(True)
        self._briefing_label.setMaximumWidth(760)
        policy = self._briefing_label.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        self._briefing_label.setSizePolicy(policy)
        self._briefing_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._briefing_label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-family: {theme.FONT};"
            f" font-size: 14px; background: transparent;"
        )
        col.addWidget(self._briefing_label)

        state_row = QHBoxLayout()
        state_row.setSpacing(10)
        self._briefing_state = QLabel("")
        self._briefing_state.setStyleSheet(_mono(10, tokens.TEXT_LOW))
        state_row.addWidget(self._briefing_state)
        # Cached prose is the norm, so there has to be a way to ask for new.
        self._briefing_refresh = QPushButton("Rewrite it")
        self._briefing_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._briefing_refresh.setToolTip("Write a fresh briefing from today's cards")
        self._briefing_refresh.setStyleSheet(theme.BTN_GHOST)
        self._briefing_refresh.clicked.connect(
            lambda: self.refresh_briefing(force=True))
        state_row.addWidget(self._briefing_refresh)
        state_row.addStretch()
        col.addLayout(state_row)
        return hero

    def _paint_briefing(self, text: str):
        """Set the prose and claim the height it needs.

        A wrapped QLabel only gets more than one line if something asks for
        its heightForWidth; inside a scrolled column nothing does, so the
        second sentence was being cut in half.
        """
        self._briefing_label.setText(text)
        width = self._briefing_label.maximumWidth()
        self._briefing_label.setMinimumHeight(
            self._briefing_label.heightForWidth(width))

    # ── The 12-column grid ────────────────────────────────────────────────

    def _build_grid(self) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(_GUTTER)
        grid.setVerticalSpacing(_GUTTER)
        for column in range(_COLUMNS):
            grid.setColumnStretch(column, 1)

        for card_id, title, _fetch in _CARDS:
            row, column, span = _CARD_CELLS[card_id]
            grid.addWidget(self._build_data_card(card_id, title), row, column, 1, span)

        grid.addWidget(self._build_knows_you(), 2, 0, 1, _COLUMNS)
        return holder

    def _build_data_card(self, card_id: str, title: str) -> QWidget:
        card = _Card(title)
        self._cards[card_id] = card

        if card_id == "health":
            card.body.addWidget(self._build_health_visuals())
            self._health_note = QLabel("")
            self._health_note.setWordWrap(True)
            self._health_note.setStyleSheet(
                f"color: {theme.TEXT_SECONDARY}; font-family: {theme.FONT};"
                f" font-size: 12px; background: transparent;"
            )
            card.body.addWidget(self._health_note)
        else:
            rows_box = QWidget()
            rows_box.setStyleSheet("background: transparent; border: none;")
            rows_layout = QVBoxLayout(rows_box)
            rows_layout.setContentsMargins(0, 0, 0, 0)
            rows_layout.setSpacing(0)
            card.body.addWidget(rows_box)
            self._card_rows[card_id] = rows_layout

        label, kind, command = _CARD_ACTIONS[card_id]
        card.action.setText(label)
        card.action.clicked.connect(
            lambda _=False, k=kind, c=command: self._run_card_action(k, c))
        self._set_card_text(card_id, "Loading…")
        return card

    def _run_card_action(self, kind: str, command: str):
        """One action per card, and it is one you could equally have said."""
        if self._worker and self._worker.isRunning():
            return
        if kind == "listen":
            self._start_listening()
        else:
            self._dispatch(command)

    def _set_card_text(self, card_id: str, text: str):
        """Store a card's raw text and re-render its body."""
        self._card_texts[card_id] = text
        if card_id == "health":
            self._health_note.setText(text)
            return
        rows = self._card_rows[card_id]
        while rows.count():
            item = rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) first: deleteLater() alone leaves the old
                # rows painted over the new ones until the event loop turns.
                widget.setParent(None)
                widget.deleteLater()
        chip = self._cards[card_id].chip
        _pre, items = _split_items(text)
        empty_state = len(items) == 1 and items[0].lower().startswith("no ")
        if (text in ("Loading…", "Nothing here.") or text.startswith("[")
                or empty_state or not items):
            chip.setVisible(False)
            label = QLabel(text.strip() or "Nothing here.")
            label.setWordWrap(True)
            label.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT};"
                f" font-size: 12px; background: transparent; padding: 3px 0;"
            )
            rows.addWidget(label)
            return
        singular, plural = _CARD_NOUNS[card_id]
        chip.set_text(f"{len(items)} {singular if len(items) == 1 else plural}")
        chip.setVisible(True)
        # A "…(5 shown):" preamble is redundant once the chip carries the count.
        for index, item_text in enumerate(items):
            if index:
                divider = QWidget()
                divider.setFixedHeight(1)
                divider.setStyleSheet(theme.SEPARATOR)
                rows.addWidget(divider)
            rows.addWidget(self._make_row(item_text, emphasized=(index == 0)))

    def _make_row(self, text: str, emphasized: bool) -> QWidget:
        """One item row: leading dot + text; the first/next item is emphasized."""
        row = QWidget()
        row.setStyleSheet("background: transparent; border: none;")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 5, 0, 5)
        line.setSpacing(9)
        dot = QLabel()
        dot.setFixedSize(6, 6)
        dot.setStyleSheet(
            f"background-color: {theme.ACCENT if emphasized else theme.TEXT_MUTED};"
            f" border-radius: 3px;"
        )
        line.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        color = theme.TEXT_PRIMARY if emphasized else theme.TEXT_SECONDARY
        label.setStyleSheet(
            f"color: {color}; font-family: {theme.FONT}; font-size: 13px;"
            f" background: transparent;"
        )
        line.addWidget(label, stretch=1)
        return row

    def _build_health_visuals(self) -> QWidget:
        """Day-arc + macro bars + the two trends there is data for."""
        row = QWidget()
        row.setStyleSheet("background: transparent; border: none;")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(22)

        self._health_arc = DayArc(unit="kcal")
        layout.addWidget(self._health_arc, 0, Qt.AlignmentFlag.AlignTop)

        bars = QVBoxLayout()
        bars.setSpacing(6)
        self._macro_bars = {
            "protein": MacroBar("Protein", theme.ACCENT),
            "carbs": MacroBar("Carbs", theme.SPEAKING),
            "fat": MacroBar("Fat", tokens.STATE["idle"]),
        }
        for bar in self._macro_bars.values():
            bars.addWidget(bar)
        bars.addStretch()
        layout.addLayout(bars, 1)

        trends = QVBoxLayout()
        trends.setSpacing(6)
        self._trends = {}
        for key, caption in (("kcal", "KCAL 7D"), ("protein_g", "PROTEIN 7D")):
            block = QVBoxLayout()
            block.setSpacing(2)
            label = QLabel(caption)
            label.setStyleSheet(_mono(10, tokens.TEXT_LOW))
            block.addWidget(label)
            spark = Sparkline()
            spark.setFixedWidth(180)
            block.addWidget(spark)
            self._trends[key] = spark
            trends.addLayout(block)
        trends.addStretch()
        layout.addLayout(trends, 0)

        self._health_visuals = row
        self._update_health_visuals()
        return row

    # ── "It's getting to know you" ────────────────────────────────────────

    def _build_knows_you(self) -> QWidget:
        """Learned facts you can forget, the skills in play, and one proposal.

        Every piece here is the real store: the ✕ on a chip calls
        Memory.delete_fact, and the yes on a proposal saves an actual skill.
        """
        panel = QWidget()
        panel.setObjectName("knowsYou")
        panel.setStyleSheet(
            f"QWidget#knowsYou {{ background: {tokens.rgba(theme.ACCENT, 0.05)};"
            f" border: 1px solid {tokens.rgba(theme.ACCENT, 0.18)};"
            f" border-radius: {tokens.R3}px; }}"
        )
        column = QVBoxLayout(panel)
        column.setContentsMargins(16, 14, 16, 16)
        column.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("IT'S GETTING TO KNOW YOU")
        title.setStyleSheet(_mono(11, theme.ACCENT, 2.0))
        head.addWidget(title)
        head.addStretch()
        rule = QLabel("MEMORY IS YOURS — EVERYTHING HERE CAN BE FORGOTTEN")
        rule.setStyleSheet(_mono(10, tokens.TEXT_LOW))
        head.addWidget(rule)
        column.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)

        facts_col = QVBoxLayout()
        facts_col.setSpacing(10)
        self._facts_caption = QLabel("LEARNED FACTS · 0")
        self._facts_caption.setStyleSheet(_mono(10, tokens.TEXT_LOW))
        facts_col.addWidget(self._facts_caption)
        self._facts_host = FlowHost()
        facts_col.addWidget(self._facts_host)
        facts_col.addStretch()
        body.addLayout(facts_col, 12)

        body.addWidget(self._divider())

        skills_col = QVBoxLayout()
        skills_col.setSpacing(10)
        self._skills_caption = QLabel("ACTIVE SKILLS · 0")
        self._skills_caption.setStyleSheet(_mono(10, tokens.TEXT_LOW))
        skills_col.addWidget(self._skills_caption)
        self._skills_host = FlowHost()
        skills_col.addWidget(self._skills_host)
        skills_col.addStretch()
        body.addLayout(skills_col, 10)

        body.addWidget(self._divider())

        auto_col = QVBoxLayout()
        auto_col.setSpacing(10)
        caption = QLabel("PROPOSED AUTOMATION")
        caption.setStyleSheet(_mono(10, tokens.TEXT_LOW))
        auto_col.addWidget(caption)
        self._proposal_box = QWidget()
        self._proposal_box.setStyleSheet(
            f"background: {tokens.SURFACE_1};"
            f" border: 1px solid {theme.BORDER_STRONG};"
            f" border-radius: {tokens.R2}px;"
        )
        proposal_row = QHBoxLayout(self._proposal_box)
        proposal_row.setContentsMargins(14, 10, 14, 10)
        proposal_row.setSpacing(12)
        self._proposal_label = QLabel("")
        self._proposal_label.setWordWrap(True)
        policy = self._proposal_label.sizePolicy()
        policy.setHeightForWidth(True)
        self._proposal_label.setSizePolicy(policy)
        self._proposal_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-family: {theme.FONT};"
            f" font-size: 12.5px; background: transparent; border: none;"
        )
        proposal_row.addWidget(self._proposal_label, 1)
        self._proposal_yes = QPushButton("Yes")
        self._proposal_yes.setCursor(Qt.CursorShape.PointingHandCursor)
        self._proposal_yes.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_ON_ACCENT}; background: {theme.ACCENT};"
            f" border: none; border-radius: 9px; padding: 6px 13px;"
            f" font-family: {theme.FONT}; font-size: 12px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_BRIGHT}; }}"
            f"QPushButton:pressed {{ background: {theme.ACCENT_PRESS}; }}"
        )
        self._proposal_yes.clicked.connect(self._accept_proposal)
        proposal_row.addWidget(self._proposal_yes)
        self._proposal_no = QPushButton("Not now")
        self._proposal_no.setCursor(Qt.CursorShape.PointingHandCursor)
        self._proposal_no.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_SECONDARY}; background: transparent;"
            f" border: none; font-family: {theme.FONT}; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}"
        )
        self._proposal_no.clicked.connect(self._dismiss_proposal)
        proposal_row.addWidget(self._proposal_no)
        auto_col.addWidget(self._proposal_box)

        self._proposal_receipt = QLabel("")
        self._proposal_receipt.setWordWrap(True)
        self._proposal_receipt.setStyleSheet(_mono(10, tokens.TEXT_LOW))
        auto_col.addWidget(self._proposal_receipt)
        auto_col.addStretch()
        body.addLayout(auto_col, 14)

        column.addLayout(body)
        self.refresh_knows_you()
        return panel

    def _divider(self) -> QWidget:
        line = QWidget()
        line.setFixedWidth(1)
        line.setStyleSheet(theme.SEPARATOR)
        return line

    def refresh_knows_you(self):
        self._paint_facts()
        self._paint_skills()
        self._paint_proposal()

    def _clear(self, host: FlowHost):
        while host.flow.count():
            item = host.flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _paint_facts(self):
        self._clear(self._facts_host)
        try:
            facts = self.memory.get_all_facts() if self.memory is not None else []
        except Exception:
            facts = []
        facts = sorted(facts, key=lambda f: f.get("created_at", ""), reverse=True)
        self._facts_caption.setText(f"LEARNED FACTS · {len(facts)}")
        for fact in facts[:_MAX_FACT_CHIPS]:
            self._facts_host.flow.addWidget(self._fact_chip(fact))
        if len(facts) > _MAX_FACT_CHIPS:
            more = QLabel(f"+{len(facts) - _MAX_FACT_CHIPS} more")
            more.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT};"
                f" font-size: 12px; background: transparent; padding: 5px 4px;"
            )
            self._facts_host.flow.addWidget(more)
        if not facts:
            empty = QLabel("Nothing learned yet — it fills in as you talk.")
            empty.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT};"
                f" font-size: 12px; background: transparent;"
            )
            self._facts_host.flow.addWidget(empty)
        self._facts_host.refresh()

    def _fact_chip(self, fact: dict) -> QWidget:
        """A pill whose ✕ genuinely forgets — Memory.delete_fact, not a hide."""
        chip = QWidget()
        chip.setStyleSheet(
            f"background: {tokens.SURFACE_1}; border: 1px solid {theme.BORDER_STRONG};"
            f" border-radius: {tokens.R_PILL}px;"
        )
        row = QHBoxLayout(chip)
        row.setContentsMargins(12, 5, 10, 5)
        row.setSpacing(7)
        text = QLabel(fact.get("content", "")[:44])
        text.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-family: {theme.FONT};"
            f" font-size: 12px; background: transparent; border: none;"
        )
        row.addWidget(text)
        forget = QPushButton("×")
        forget.setCursor(Qt.CursorShape.PointingHandCursor)
        forget.setToolTip("Forget this")
        forget.setFixedWidth(14)
        forget.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_MUTED}; background: transparent;"
            f" border: none; font-family: {theme.FONT}; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {theme.ERROR}; }}"
        )
        forget.clicked.connect(lambda _=False, f=fact: self._forget_fact(f))
        row.addWidget(forget)
        return chip

    def _forget_fact(self, fact: dict):
        if self.memory is None:
            return
        try:
            self.memory.delete_fact(fact.get("id", ""))
        except Exception:
            return
        self._paint_facts()

    def _paint_skills(self):
        self._clear(self._skills_host)
        try:
            from core.skills.store import SkillStore
            skills = SkillStore().list_all()
        except Exception:
            skills = []
        self._skills_caption.setText(f"ACTIVE SKILLS · {len(skills)}")
        for skill in skills[:_MAX_SKILL_CHIPS]:
            label = QLabel(str(skill.get("name", ""))[:26])
            label.setStyleSheet(
                f"color: {theme.TEXT_SECONDARY}; font-family: {theme.FONT_MONO};"
                f" font-size: 12px; background: {tokens.SURFACE_1};"
                f" border: 1px solid {theme.BORDER_STRONG};"
                f" border-radius: {tokens.R1}px; padding: 5px 10px;"
            )
            self._skills_host.flow.addWidget(label)
        if len(skills) > _MAX_SKILL_CHIPS:
            more = QLabel(f"+{len(skills) - _MAX_SKILL_CHIPS}")
            more.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT};"
                f" font-size: 12px; background: transparent; padding: 5px 4px;"
            )
            self._skills_host.flow.addWidget(more)
        if not skills:
            empty = QLabel("No skills yet.")
            empty.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT};"
                f" font-size: 12px; background: transparent;"
            )
            self._skills_host.flow.addWidget(empty)
        self._skills_host.refresh()

    def _paint_proposal(self):
        """One proposal at a time — propose, never impose."""
        try:
            from core.skills.miner import HabitMiner
            pending = HabitMiner().pending()
        except Exception:
            pending = []
        self._proposal = pending[0] if pending else None
        if self._proposal is None:
            self._proposal_box.setVisible(False)
            if not self._proposal_receipt.text():
                self._proposal_receipt.setText(
                    "NOTHING TO PROPOSE — IT WATCHES FOR REPEATED ASKS.")
            return
        self._proposal_box.setVisible(True)
        self._proposal_label.setText(
            f"You've asked “{self._proposal['example'][:90]}” "
            f"{self._proposal['count']} times across "
            f"{self._proposal['days_seen']} days. Save it as a skill?"
        )
        self._proposal_receipt.setText("")

    def _accept_proposal(self):
        if self._proposal is None:
            return
        try:
            from tools.skill_tool import accept_skill_proposal
            result = accept_skill_proposal(self._proposal["id"])
        except Exception as e:
            result = f"Could not save it: {e}"
        self._proposal_box.setVisible(False)
        self._proposal_receipt.setText(result.upper()[:120])
        self._proposal = None
        self._paint_skills()

    def _dismiss_proposal(self):
        if self._proposal is None:
            return
        try:
            from tools.skill_tool import dismiss_skill_proposal
            dismiss_skill_proposal(self._proposal["id"])
        except Exception:
            pass
        self._proposal_box.setVisible(False)
        self._proposal_receipt.setText("DISMISSED — I'LL STOP SUGGESTING THIS ONE.")
        self._proposal = None

    # ── The global command bar ────────────────────────────────────────────

    def _build_command_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(
            f"background: {tokens.SURFACE_0}; border-top: 1px solid {theme.BORDER};"
            f" border-bottom-left-radius: {tokens.R4}px;"
            f" border-bottom-right-radius: {tokens.R4}px;"
        )
        column = QVBoxLayout(bar)
        column.setContentsMargins(_MARGIN, 12, _MARGIN, 14)
        column.setSpacing(8)

        # Answers land above the bar so the exchange stays with the input.
        # The bar sits outside the page's scroll area, so a long answer
        # scrolls in its own box rather than pushing the page off the window.
        self._response_container = QWidget()
        self._response_container.setStyleSheet("background: transparent;")
        self._response_layout = QVBoxLayout(self._response_container)
        self._response_layout.setContentsMargins(0, 0, 0, 0)
        self._response_layout.setSpacing(4)
        self._response_scroll = QScrollArea()
        self._response_scroll.setWidgetResizable(True)
        self._response_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._response_scroll.setStyleSheet(theme.SCROLL_AREA)
        # The bar's border-top cascades onto the viewport and draws a stray rule.
        self._response_scroll.viewport().setStyleSheet("background: transparent; border: none;")
        self._response_scroll.setMaximumHeight(_RESPONSE_MAX_HEIGHT)
        self._response_scroll.setWidget(self._response_container)
        self._response_scroll.setVisible(False)
        column.addWidget(self._response_scroll)

        # Voice first: talking is the whole bar, the keyboard is one button.
        self._voice_bar = QWidget()
        self._voice_bar.setStyleSheet("background: transparent;")
        voice_row = QHBoxLayout(self._voice_bar)
        voice_row.setContentsMargins(0, 0, 0, 0)
        voice_row.setSpacing(8)

        self._talk_btn = QPushButton("Talk to El Fager")
        self._talk_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._talk_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_ON_ACCENT}; background: {theme.ACCENT};"
            f" border: none; border-radius: {tokens.R2}px; padding: 11px 20px;"
            f" font-family: {theme.FONT}; font-size: {theme.SZ_UI}px;"
            f" font-weight: 500; text-align: left; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_BRIGHT}; }}"
            f"QPushButton:pressed {{ background: {theme.ACCENT_PRESS}; }}"
            f"QPushButton:disabled {{ background: {tokens.SURFACE_2};"
            f" color: {theme.TEXT_MUTED}; }}"
        )
        self._talk_btn.clicked.connect(self._start_listening)
        voice_row.addWidget(self._talk_btn, 1)

        self._type_toggle = QPushButton("Type")
        self._type_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._type_toggle.setToolTip("Type instead")
        self._type_toggle.setStyleSheet(theme.BTN_GHOST)
        self._type_toggle.clicked.connect(lambda: self._set_typing(True))
        voice_row.addWidget(self._type_toggle)
        column.addWidget(self._voice_bar)

        self._command_input = QLineEdit()
        self._command_input.setPlaceholderText(
            "Type it — same brain, same exchange")
        self._command_input.setStyleSheet(theme.TEXT_INPUT)
        self._command_input.returnPressed.connect(self._on_command_entered)
        self._command_input.installEventFilter(self)
        self._command_input.setVisible(False)
        column.addWidget(self._command_input)

        # System strip: today's spend + latency, 7-day spend trend, status.
        strip = QHBoxLayout()
        strip.setSpacing(20)
        self._cost_tile = StatTile("today")
        strip.addWidget(self._cost_tile)
        self._latency_tile = StatTile("avg latency")
        strip.addWidget(self._latency_tile)
        self._spend_spark = Sparkline()
        self._spend_spark.setToolTip("API spend, last 7 days")
        strip.addWidget(self._spend_spark, 1)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(theme.STATUS_BAR)
        strip.addWidget(self._status_label)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setToolTip("Refresh all cards")
        refresh_btn.setStyleSheet(theme.BTN_GHOST)
        refresh_btn.clicked.connect(self.refresh_cards)
        strip.addWidget(refresh_btn)
        column.addLayout(strip)
        return bar

    # ------------------------------------------------------------------ #
    #  Painting from local state                                          #
    # ------------------------------------------------------------------ #

    def _refresh_weather(self):
        """Title-bar weather — daemon thread, throttled; never blocks the open."""
        now = datetime.now()
        if self._weather_at and (now - self._weather_at).total_seconds() < 900:
            return
        self._weather_at = now

        def run():
            try:
                text = _fetch_weather_compact()
            except Exception:
                text = ""
            self._signaler.weather_ready.emit(text)

        threading.Thread(target=run, daemon=True).start()

    def _on_weather_ready(self, text: str):
        self._weather_label.setText(f"· {text.upper()}" if text else "")
        self._weather_label.setVisible(bool(text))

    def _update_system_strip(self):
        """Refresh cost/latency tiles + spend sparkline (local jsonl reads)."""
        try:
            from core.telemetry import summarize
            s = summarize(days=1)
            if s["requests"]:
                self._cost_tile.set_value(f"${s['cost_usd']:.2f}")
                lat = s["avg_latency_ms"]
                self._latency_tile.set_value(f"{lat / 1000:.1f}s" if lat else "—")
            else:
                self._cost_tile.set_value("—")
                self._latency_tile.set_value("—")
            self._spend_spark.set_points(_spend_series(7))
        except Exception:
            pass  # telemetry must never break the surface

    def _update_health_visuals(self):
        """Re-read local nutrition files and repaint arc + bars + trends."""
        n = _read_nutrition()
        if n is None or not n["kcal"]:
            self._health_visuals.setVisible(False)  # the note explains instead
            return
        self._health_visuals.setVisible(True)
        self._health_arc.set_values(n["logged_kcal"], n["kcal"])
        for key, bar in self._macro_bars.items():
            bar.set_values(n[f"logged_{key}"], n[key])
        for key, spark in self._trends.items():
            spark.set_points(_meal_series(key))

    def _apply_theme(self):
        # The Command Center always sits on the default Dawn ground; the
        # settings "theme" brightness option keeps applying to the overlay only.
        self._card.setStyleSheet(theme.card_style())

    # ------------------------------------------------------------------ #
    #  Card data: cache-first paint, background refresh                   #
    # ------------------------------------------------------------------ #

    def _read_cache(self) -> dict:
        try:
            if _CACHE_FILE.exists():
                return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _load_cache(self):
        cache = self._read_cache()
        for card_id, entry in cache.get("cards", {}).items():
            if card_id in self._cards and entry.get("text"):
                self._set_card_text(card_id, entry["text"])
                if entry.get("updated"):
                    self._cards[card_id].meta.setText(f"AS OF {entry['updated']}")
        briefing = cache.get("briefing", {})
        if briefing.get("text"):
            # Something to read in the first frame, rather than an empty box —
            # and stamped, so it is clear you are reading a cached one.
            self._paint_briefing(briefing["text"])
            self._briefing_state.setText(self._briefing_stamp(self._briefing_age()))

    def _save_cache_entry(self, key: str, value: dict, section: str = "cards"):
        try:
            cache = self._read_cache()
            if section:
                cache.setdefault(section, {})[key] = value
            else:
                cache[key] = value
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(
                json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            print(f"[CommandCenter] Cache write failed: {e}")

    def refresh_cards(self):
        """Kick one daemon thread per card; the view never blocks on fetches."""
        for card_id, _title, fetch in _CARDS:
            if card_id in self._pending:
                continue
            self._pending.add(card_id)
            threading.Thread(
                target=self._fetch_one, args=(card_id, fetch), daemon=True
            ).start()

    def _fetch_one(self, card_id: str, fetch):
        try:
            text = fetch()
        except Exception as e:
            text = f"[{card_id} error: {e}]"
        self._signaler.card_ready.emit(card_id, text)

    def _on_card_ready(self, card_id: str, text: str):
        self._pending.discard(card_id)
        if card_id not in self._cards:
            return
        # Keep last-known data if the refresh failed outright.
        prev = self._card_texts.get(card_id, "")
        if text.startswith(f"[{card_id} error:") and prev not in ("", "Loading…"):
            self._cards[card_id].meta.setText("REFRESH FAILED")
            self._maybe_write_briefing()
            return
        clean = text.strip() or "Nothing here."
        self._set_card_text(card_id, clean)
        now = datetime.now()
        self._cards[card_id].meta.setText(f"AS OF {now:%H:%M}")
        # Dated, so a surface reading this cache can tell a card about today
        # from one written days ago.
        self._save_cache_entry(card_id, {"text": clean, "updated": f"{now:%H:%M}",
                                         "date": now.date().isoformat()})
        if card_id == "health":
            self._update_health_visuals()
        self._maybe_write_briefing()

    def _maybe_write_briefing(self):
        """The deferred write, once every card has reported — including the
        ones that failed, or a single dead fetch would strand the briefing."""
        if self._briefing_wanted and not self._pending:
            self._briefing_wanted = False
            self._write_briefing()

    # ------------------------------------------------------------------ #
    #  The briefing prose — generated per open, never on the open path    #
    # ------------------------------------------------------------------ #

    def _briefing_ttl(self) -> int:
        try:
            ttl = int(_load_settings().get("briefing_ttl_minutes",
                                           _BRIEFING_TTL_DEFAULT))
        except (TypeError, ValueError):
            return _BRIEFING_TTL_DEFAULT
        return max(0, ttl)

    def _briefing_age(self) -> "float | None":
        """Minutes since the cached briefing was written (None = no cache)."""
        at = self._read_cache().get("briefing", {}).get("at")
        if not at:
            return None
        try:
            written = datetime.fromisoformat(at)
        except ValueError:
            return None
        if written.date() != datetime.now().date():
            return None      # yesterday's briefing is never fresh
        return (datetime.now() - written).total_seconds() / 60

    def _briefing_notes(self) -> str:
        """Today, as the cards already have it — the input to the synthesis."""
        notes = [f"Time now: {datetime.now().strftime('%A %H:%M')}."]
        weather = self._weather_label.text().strip("· ")
        if weather:
            notes.append(f"Weather: {weather}")
        for card_id, title, _fetch in _CARDS:
            text = (self._card_texts.get(card_id) or "").strip()
            if text and text != "Loading…" and not text.startswith("["):
                notes.append(f"{title}:\n{text}")
        return "\n\n".join(notes)

    def refresh_briefing(self, force: bool = False):
        """Rewrite the prose — but only when it is actually out of date.

        Opening the window used to mean a full tool loop every time. Now the
        cached line is reused until it goes stale (briefing_ttl_minutes, 30 by
        default; 0 = only on Rewrite), and a rewrite is one short call against
        the card text this surface already has, rather than the model going
        and fetching the same day a second time.
        """
        if self._briefing_pending or self.brain is None:
            return
        if not force:
            ttl = self._briefing_ttl()
            age = self._briefing_age()
            if ttl == 0:
                # 0 means only on Rewrite. Checking age first let a briefing
                # from yesterday through, because _briefing_age returns None
                # across a date change — so "never regenerate on open" quietly
                # regenerated on the first open after midnight.
                self._briefing_state.setText(self._briefing_stamp(age))
                return
            if age is not None and age < ttl:
                self._briefing_state.setText(self._briefing_stamp(age))
                return
            if self._pending:
                # Cards are still arriving. Writing now would describe the
                # last open's data, so wait and write once they land.
                self._briefing_wanted = True
                self._briefing_state.setText("BRIEFING · WAITING FOR TODAY…")
                return
        self._write_briefing()

    def _write_briefing(self):
        notes = self._briefing_notes()
        if notes.count("\n\n") < 1:
            # Nothing to write about yet — don't spend a call saying so.
            self._briefing_state.setText(self._briefing_stamp(self._briefing_age()))
            return

        self._briefing_pending = True
        self._briefing_state.setText("BRIEFING · WRITING…")

        def run():
            text = ""
            try:
                result = self.brain.synthesize(notes, system=_BRIEFING_SYSTEM,
                                               max_tokens=300)
                text = result if isinstance(result, str) else ""
            except Exception:
                text = ""
            self._signaler.briefing_ready.emit(text)

        threading.Thread(target=run, daemon=True).start()

    def _briefing_stamp(self, age: "float | None") -> str:
        if age is None:
            return "BRIEFING · —"
        if age < 1:
            return "BRIEFING · JUST NOW"
        return f"BRIEFING · {int(age)} MIN AGO"

    def _on_briefing_ready(self, text: str):
        self._briefing_pending = False
        clean = " ".join(text.split())
        if not clean:
            # Keep whatever was cached rather than blanking the hero.
            self._briefing_state.setText("BRIEFING · UNAVAILABLE, SHOWING THE LAST ONE")
            return
        self._paint_briefing(clean)
        now = datetime.now()
        self._briefing_state.setText(self._briefing_stamp(0))
        self._save_cache_entry(
            "briefing",
            {"text": clean, "updated": now.strftime("%H:%M"),
             "at": now.isoformat(timespec="seconds")},
            section="",
        )

    # ------------------------------------------------------------------ #
    #  Command bar → existing brain pipeline                              #
    # ------------------------------------------------------------------ #

    def _set_typing(self, on: bool):
        """The keyboard is opt-in; leaving it returns the bar to voice."""
        self._command_input.setVisible(on)
        self._voice_bar.setVisible(not on)
        if on:
            self._command_input.setFocus()

    def _start_listening(self):
        """Talk button — the same pipeline the overlay uses, no text needed."""
        if self._worker and self._worker.isRunning():
            return
        self._talk_btn.setText("Listening…")
        self._talk_btn.setEnabled(False)
        self._dispatch(None)

    def _on_command_entered(self):
        text = self._command_input.text().strip()
        if not text or (self._worker and self._worker.isRunning()):
            return
        self._dispatch(text)

    def _dispatch(self, text: "str | None", echo: "str | None" = None):
        from core.pipeline import PipelineWorker

        self._command_input.setEnabled(False)
        spoken = echo or text
        if spoken:
            self._add_bubble("user", spoken)
        self._heard_bubbled = bool(spoken)
        self._status_label.setText("Listening…" if text is None else "Thinking…")

        self._worker = PipelineWorker(
            self.voice_in, self.brain, self.voice_out, self.memory, text_input=text
        )
        self._worker.state_update.connect(self._on_state_update)
        self._worker.done.connect(self._on_pipeline_done)
        self._worker.error.connect(self._on_error)
        if self._wake_listener:
            self._worker.started.connect(self._wake_listener.pause)
            self._worker.done.connect(self._wake_listener.resume)
        self._worker.start()

    def _set_assistant_state(self, state: str):
        self._state_chip.set_text(state)
        self._state_chip.set_dot(theme.STATE_COLORS.get(state, theme.TEXT_MUTED))
        if state in ("listening", "processing", "speaking"):
            self._state_chip.start_pulse()
        else:
            self._state_chip.stop_pulse()

    def _on_state_update(self, state: str, transcript: str, response: str):
        self._set_assistant_state(state)
        if state == "listening":
            self._heard_bubbled = False              # a new voice turn begins
        if (state == "processing" and transcript
                and transcript not in ("Transcribing...", "Loading Whisper model...")
                and not self._heard_bubbled):
            self._add_bubble("user", transcript)     # what a spoken turn heard
            self._heard_bubbled = True
        if state == "speaking" and response:
            self._add_bubble("assistant", response)
            self._status_label.setText("")

    def _on_pipeline_done(self):
        self._command_input.setEnabled(True)
        self._talk_btn.setText("Talk to El Fager")
        self._talk_btn.setEnabled(True)
        self._command_input.clear()
        self._status_label.setText("")
        self._set_assistant_state("idle")
        # A command may have changed today's data (new event, task, meal…)
        self.refresh_cards()
        self._update_system_strip()  # …and today's spend/latency certainly did
        self.refresh_knows_you()     # …and it may have learned something new

    def _on_error(self, message: str):
        self._command_input.setEnabled(True)
        self._talk_btn.setText("Talk to El Fager")
        self._talk_btn.setEnabled(True)
        self._status_label.setText(message)
        self._set_assistant_state("error")

    def _add_bubble(self, role: str, text: str):
        self._response_scroll.setVisible(True)
        # Keep only the latest exchange visible — the overlay owns long history.
        while self._response_layout.count() > 3:
            item = self._response_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        ts = datetime.now().strftime("%H:%M")
        bubble = MessageBubble(role, prose.plain(text), ts, self._response_container)
        # MessageBubble is the overlay's widget; recolor its single label for
        # this surface rather than touching ui/overlay.py.
        label = bubble.findChild(QLabel)
        if label is not None:
            label.setStyleSheet(
                theme.BUBBLE_USER if role == "user" else theme.BUBBLE_ASSISTANT
            )
        self._response_layout.addWidget(bubble)
        QTimer.singleShot(0, self._show_latest_bubble)

    def _show_latest_bubble(self):
        """Fit the box to the exchange, then scroll the newest bubble to its top.

        A scroll area does not grow with its content — left alone it sat at
        48px and hid the answer. Runs once laid out, so wrapped labels know
        their height. The bubble is looked up now, not captured: an older one
        may already have been trimmed away."""
        width = self._response_scroll.viewport().width()
        wanted = (self._response_layout.heightForWidth(width)
                  if self._response_layout.hasHeightForWidth()
                  else self._response_container.sizeHint().height())
        self._response_scroll.setFixedHeight(min(wanted, _RESPONSE_MAX_HEIGHT))
        item = self._response_layout.itemAt(self._response_layout.count() - 1)
        if item is not None and item.widget() is not None:
            # Its first line at the top: ensureWidgetVisible on a bubble taller
            # than the box lands mid-answer and hides how it starts.
            self._response_scroll.verticalScrollBar().setValue(item.widget().y())

    # ------------------------------------------------------------------ #
    #  Show / hide                                                        #
    # ------------------------------------------------------------------ #

    def toggle(self):
        if self.isVisible():
            self._hide()
        else:
            self._greeting_label.setText(_greeting())
            self._date_label.setText(datetime.now().strftime("%A, %B %d").upper())
            self._paint_clock()
            self._update_system_strip()
            self._refresh_weather()
            self.show()
            self.raise_()
            self.activateWindow()
            self._fade_in()
            # Cache is already painted; everything live refreshes behind it.
            self.refresh_cards()
            self.refresh_knows_you()
            self.refresh_briefing()

    def _fade_in(self):
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(tokens.T_FAST)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = anim

    def _hide(self):
        self._save_geometry()
        self.hide()

    def _save_geometry(self):
        settings = _load_settings()
        g = self.geometry()
        settings["command_center_geometry"] = [g.x(), g.y(), g.width(), g.height()]
        _save_settings(settings)

    def closeEvent(self, event):
        event.ignore()
        self._hide()

    # ------------------------------------------------------------------ #
    #  Keyboard + dragging (same conventions as ui/overlay.py)            #
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        if obj is self._command_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape and self._command_input.isVisible():
                self._set_typing(False)
                return True
            if event.key() == Qt.Key.Key_Escape:
                self._hide()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self._hide()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._drag_origin = self.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_start is not None
            and self._drag_origin is not None
        ):
            delta = event.globalPosition().toPoint() - self._drag_start
            self.move(self._drag_origin + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
