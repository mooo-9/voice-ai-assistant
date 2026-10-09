"""
El Fager — Settings.

The design's left rail plus its five sections, replacing the cramped
QFormLayout dialog. Every control here is wired to something the app actually
reads, so nothing on this surface is a dead switch:

  Voice     → data/settings.json (main.py, the overlay, core.sound)
  Routines  → core.scheduler, which persists to data/schedules.json
  Skills    → `skills_disabled`, which core.brain._select_tools honours by
              never offering a disabled skill's tools to the model
  Memory    → core.memory's fact store (data/facts.json)
  System    → data/settings.json

Cockpit palette, per the CANON table (Settings is a cockpit-family surface).
"""

from PyQt6.QtCore import Qt, QPropertyAnimation, pyqtProperty
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui import theme, tokens
from ui.overlay import _load_settings, _save_settings

_SOUND_CUES = ("summon", "heard", "step", "resolved", "armed", "sent", "error")

# The six surfaces El Fager can touch, and what touching them means. The keys
# are core.brain.SKILL_TOOLS keys — that module owns which tools each covers.
_SKILLS = (
    ("gmail", "Gmail", "READ · DRAFT · SEND — SENDING ALWAYS STAGES"),
    ("whatsapp", "WhatsApp", "READ · DRAFT · SEND — SENDING ALWAYS STAGES"),
    ("calendar", "Calendar", "READ · CREATE · MOVE EVENTS"),
    ("todoist", "Todoist", "READ · ADD · COMPLETE TASKS"),
    ("browser", "Browser", "NAVIGATE · CLICK · FILL FORMS"),
    ("screen", "Screen", "READ WHAT'S ON SCREEN, WHEN ASKED"),
)

_DAYS = {"mon": "MON", "tue": "TUE", "wed": "WED", "thu": "THU",
         "fri": "FRI", "sat": "SAT", "sun": "SUN"}


def _mono(size: int, color: str, tracking: float = 1.2) -> str:
    return (
        f"color: {color}; font-family: {theme.FONT_MONO}; font-size: {size}px;"
        f" letter-spacing: {tracking}px; background: transparent; border: none;"
    )


def _wake_caption() -> str:
    """How the wake word has actually been performing, in one line."""
    try:
        from core import wake_metrics
        return wake_metrics.caption(7)
    except Exception:
        return "NO WAKES RECORDED YET"


def _scheduler():
    """The live scheduler when the app is running, else a disk-only one.

    Both persist to data/schedules.json, which is what start() reads at boot,
    so a routine switched off here stays off across a restart either way.
    """
    try:
        from core.scheduler import ElFagerScheduler, get_instance
        return get_instance() or ElFagerScheduler()
    except Exception:
        return None


def _when(trigger: dict) -> str:
    """A routine's schedule in one mono line — 'DAILY 07:30', 'FRI 18:00'."""
    kind = trigger.get("type")
    if kind == "cron":
        clock = f"{int(trigger.get('hour', 0)):02d}:{int(trigger.get('minute', 0)):02d}"
        day = trigger.get("day_of_week")
        if day:
            return f"{_DAYS.get(str(day).lower()[:3], str(day).upper())} {clock}"
        return f"DAILY {clock}"
    if kind == "interval":
        for unit, label in (("minutes", "MIN"), ("hours", "HR"), ("days", "DAY")):
            if trigger.get(unit):
                return f"EVERY {trigger[unit]} {label}"
        return "ON AN INTERVAL"
    if kind == "date":
        return f"ONCE · {str(trigger.get('run_date', ''))[:16].replace('T', ' ')}"
    return "—"


class Toggle(QPushButton):
    """40×22 track, 18px knob, cyan@80% on / white@12% off, 140ms."""

    def __init__(self, on: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._pos = 1.0 if on else 0.0
        self.toggled.connect(self._animate)

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, value: float):
        self._pos = value
        self.update()

    pos = pyqtProperty(float, fget=_get_pos, fset=_set_pos)

    def _animate(self, on: bool):
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(tokens.T_FAST)
        anim.setEndValue(1.0 if on else 0.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim = anim

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(tokens.CK_STATE["listening"])
        track.setAlphaF(0.8 * self._pos)
        off = QColor("#FFFFFF")
        off.setAlphaF(0.12 * (1 - self._pos))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(off)
        p.drawRoundedRect(0, 0, 40, 22, 11, 11)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, 40, 22, 11, 11)
        knob_x = 2 + self._pos * 18
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(int(knob_x), 2, 18, 18)


class _Section(QWidget):
    """A settings section: title (+ what it is, + how to say it), then rows."""

    def __init__(self, title: str, description: str = "", hint: str = "", parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(18)

        head_row = QHBoxLayout()
        head_row.setSpacing(12)
        head = QLabel(title)
        head.setStyleSheet(
            f"color: {tokens.CK_TEXT_HI}; font-family: {theme.FONT};"
            f" font-size: 21px; font-weight: 500; background: transparent;"
        )
        head_row.addWidget(head)
        head_row.addStretch()
        if hint:
            # Every section says out loud how to reach it by voice.
            spoken = QLabel(hint)
            spoken.setStyleSheet(_mono(10, tokens.CK_TEXT_FAINT, 1.4))
            head_row.addWidget(spoken, 0, Qt.AlignmentFlag.AlignVCenter)
        self._col.addLayout(head_row)

        if description:
            desc = QLabel(description)
            desc.setWordWrap(True)
            policy = desc.sizePolicy()
            policy.setHeightForWidth(True)
            desc.setSizePolicy(policy)
            desc.setStyleSheet(
                f"color: {tokens.CK_TEXT_MID}; font-family: {theme.FONT};"
                f" font-size: 13px; background: transparent;"
            )
            self._col.addWidget(desc)
            self._description = desc

    def row(self, label: str, control: QWidget, note: str = "",
            value: str = "") -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        line = QHBoxLayout(wrap)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(16)

        text = QVBoxLayout()
        text.setSpacing(3)
        name = QLabel(label)
        name.setStyleSheet(
            f"color: {tokens.CK_TEXT_HI}; font-family: {theme.FONT};"
            f" font-size: 14px; background: transparent;"
        )
        text.addWidget(name)
        if note:
            hint = QLabel(note)
            hint.setWordWrap(True)
            hint.setStyleSheet(_mono(10, tokens.CK_TEXT_FAINT))
            text.addWidget(hint)
        line.addLayout(text, 1)
        if value:
            when = QLabel(value)
            when.setStyleSheet(_mono(11, tokens.CK_TEXT_LOW, 1.4))
            line.addWidget(when, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self._col.addWidget(wrap)
        return wrap

    def note(self, text: str, accent: str = "") -> QLabel:
        """A standing rule or an empty state — text with no control beside it."""
        label = QLabel(text)
        label.setWordWrap(True)
        policy = label.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        label.setSizePolicy(policy)
        if accent:
            label.setStyleSheet(
                f"color: {tokens.CK_TEXT_MID}; font-family: {theme.FONT};"
                f" font-size: 13px; background: {tokens.CK_CARD};"
                f" border-radius: {tokens.R3}px; border-left: 2px solid {accent};"
                f" padding: 12px 14px;"
            )
        else:
            label.setStyleSheet(_mono(10, tokens.CK_TEXT_FAINT))
        self._col.addWidget(label)
        return label

    def finish(self):
        self._col.addStretch()


class SettingsWindow(QWidget):
    """Left rail + sections, 120ms cross-fade between them."""

    def __init__(self, on_applied=None, memory=None, parent=None):
        super().__init__(parent)
        self._on_applied = on_applied
        self._memory = memory
        self._settings = _load_settings()
        self._forgotten: "dict | None" = None   # the last fact, for Undo
        self._build_ui()

    # ------------------------------------------------------------------ #

    def _build_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("El Fager — Settings")
        self.setStyleSheet(f"background: {tokens.CK_PANEL};")
        self.resize(760, 560)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        # ── left rail ──
        rail = QWidget()
        rail.setFixedWidth(196)
        rail.setStyleSheet(
            f"background: {tokens.CK_VOID};"
            f" border-right: 1px solid {tokens.CK_HAIRLINE};"
        )
        rail_col = QVBoxLayout(rail)
        rail_col.setContentsMargins(18, 24, 18, 20)
        rail_col.setSpacing(6)

        title = QLabel("Settings")
        title.setStyleSheet(
            f"color: {tokens.CK_TEXT_HI}; font-family: {theme.FONT};"
            f" font-size: 17px; font-weight: 500; background: transparent; border: none;"
        )
        rail_col.addWidget(title)
        rail_col.addSpacing(14)

        self._pages = QStackedWidget()
        self._pages.setStyleSheet("background: transparent;")
        self._rail_buttons = []
        for index, name in enumerate(
                ("Voice", "Routines", "Skills", "Memory", "System")):
            btn = QPushButton(name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=index: self.show_section(i))
            rail_col.addWidget(btn)
            self._rail_buttons.append(btn)
        rail_col.addStretch()

        back = QPushButton("Back to cockpit")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(
            f"QPushButton {{ {_mono(10, tokens.CK_TEXT_LOW)} padding: 6px 4px;"
            f" text-align: left; }}"
            f"QPushButton:hover {{ color: {tokens.CK_TEXT_HI}; }}"
        )
        back.clicked.connect(self.hide)
        rail_col.addWidget(back)
        row.addWidget(rail)

        # ── pages ──
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_col = QVBoxLayout(body)
        body_col.setContentsMargins(32, 28, 32, 24)
        body_col.addWidget(self._pages)
        row.addWidget(body, 1)

        self._pages.addWidget(self._voice_page())
        self._pages.addWidget(self._routines_page())
        self._pages.addWidget(self._skills_page())
        self._pages.addWidget(self._memory_page())
        self._pages.addWidget(self._system_page())
        self.show_section(0)

    # ── sections ──────────────────────────────────────────────────────────

    def _voice_page(self) -> QWidget:
        page = _Section("Voice", "How it hears you and how it talks back.",
                        hint='SAY: "CHANGE THE VOICE"')

        self._wake = Toggle(self._settings.get("wake_word_enabled", True))
        self._wake.toggled.connect(lambda v: self._set("wake_word_enabled", v))
        # The note carries the front door's measured reliability, because
        # "it seems fine" is not a number.
        page.row("Wake word", self._wake,
                 f"SAY “HEY FAGER” TO SUMMON WITHOUT THE KEYBOARD · {_wake_caption()}")

        self._wake_surface = QComboBox()
        self._wake_surface.addItems(["overlay", "cockpit"])
        self._wake_surface.setCurrentText(
            self._settings.get("wake_surface", "overlay"))
        self._wake_surface.currentTextChanged.connect(
            lambda v: self._set("wake_surface", v))
        self._style_combo(self._wake_surface)
        page.row("Answers on", self._wake_surface,
                 "WHICH SURFACE COMES UP WHEN YOU CALL IT · THE COCKPIT COSTS "
                 "A ONE-TIME BUILD ON THE FIRST WAKE")

        self._barge_in = Toggle(self._settings.get("barge_in", True))
        self._barge_in.toggled.connect(lambda v: self._set("barge_in", v))
        page.row("Barge-in", self._barge_in,
                 "TALKING OVER IT CUTS IT OFF MID-WORD · TURN OFF IF LOUD "
                 "SPEAKERS MAKE IT INTERRUPT ITSELF")

        self._duck = Toggle(self._settings.get("duck_while_listening", True))
        self._duck.toggled.connect(lambda v: self._set("duck_while_listening", v))
        page.row("Quiet while listening", self._duck,
                 "OTHER APPS DROP TO 15% WHILE THE MIC IS OPEN")

        self._vocabulary = QLineEdit(
            ", ".join(self._settings.get("voice_vocabulary", []) or []))
        self._vocabulary.setCursorPosition(0)     # show the list from its start
        self._vocabulary.setPlaceholderText("Estanna, Fares Sokar, …")
        self._vocabulary.setMinimumWidth(260)
        self._vocabulary.setStyleSheet(
            f"QLineEdit {{ background: {tokens.CK_CARD}; color: {tokens.CK_TEXT_HI};"
            f" border: 1px solid {tokens.CK_HAIRLINE}; border-radius: {tokens.R2}px;"
            f" padding: 6px 10px; font-family: {theme.FONT}; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {tokens.CK_STATE['listening']}; }}"
        )
        self._vocabulary.editingFinished.connect(self._save_vocabulary)
        page.row("Names it should know", self._vocabulary, "SEPARATE WITH COMMAS")

        cues = self._settings.get("sound_cues", True)
        self._cues = Toggle(cues if isinstance(cues, bool) else any(cues.values()))
        self._cues.toggled.connect(lambda v: self._set("sound_cues", v))
        page.row("Sound cues", self._cues,
                 "SEVEN SYNTHESISED CUES · NO CUE PLAYS OVER THE ASSISTANT EXCEPT ERROR")

        self._tts = QComboBox()
        # "auto" was already what voice_out defaults to and what _synthesize
        # honours; leaving it out of this list meant the control showed "edge"
        # while the setting said otherwise, and overwrote it on first touch.
        self._tts.addItems(["auto", "groq", "edge"])
        self._tts.setCurrentText(self._settings.get("tts_backend", "auto"))
        self._tts.currentTextChanged.connect(lambda v: self._set("tts_backend", v))
        self._style_combo(self._tts)
        page.row("Voice backend", self._tts,
                 "AUTO PREFERS THE NEURAL VOICE · EDGE IS THE ROBOTIC FALLBACK")

        self._voice_en = QComboBox()
        self._voice_en.addItems(
            ["en-US-GuyNeural", "en-US-JennyNeural", "en-GB-RyanNeural"])
        self._voice_en.setCurrentText(self._settings.get("voice_en", "en-US-GuyNeural"))
        self._voice_en.currentTextChanged.connect(lambda v: self._set("voice_en", v))
        self._style_combo(self._voice_en)
        page.row("English voice", self._voice_en)

        page.finish()
        return page

    def _routines_page(self) -> QWidget:
        """What it does without being asked — the scheduler's own jobs."""
        page = _Section(
            "Routines",
            "What it does without being asked. Each one knocks before "
            "entering rather than speaking over you.",
            hint='SAY: "TURN OFF THE MORNING BRIEF"',
        )
        self._routine_toggles: dict[str, Toggle] = {}
        jobs = self._routines()
        for job in jobs:
            job_id = job.get("id")
            if not job_id:
                continue
            toggle = Toggle(job.get("enabled", True))
            toggle.toggled.connect(
                lambda on, jid=job_id: self._set_routine(jid, on))
            self._routine_toggles[job_id] = toggle
            page.row(
                job.get("name", job_id),
                toggle,
                note=(job.get("description", "") or "").upper(),
                value=_when(job.get("trigger", {})),
            )
        if not jobs:
            page.note("NO ROUTINES YET — ASK IT TO REMIND YOU DAILY AND ONE "
                      "APPEARS HERE.")
        page.finish()
        return page

    def _skills_page(self) -> QWidget:
        """What it can touch. A switch here removes the tools from the model."""
        page = _Section(
            "Skills & permissions",
            "What it can touch. Switching one off takes those tools away from "
            "the assistant entirely — it cannot call what it isn't handed.",
        )
        disabled = set(self._settings.get("skills_disabled", []) or [])
        self._skill_toggles: dict[str, Toggle] = {}
        for key, label, permission in _SKILLS:
            toggle = Toggle(key not in disabled)
            toggle.toggled.connect(lambda on, k=key: self._set_skill(k, on))
            self._skill_toggles[key] = toggle
            page.row(label, toggle, note=permission)

        # Stated, not switchable — the one rule with no toggle beside it.
        page.note(
            "Anything that leaves this machine — a message, a mail, an invite "
            "— is staged for you to look at first. That one has no off switch.",
            accent=tokens.CK_STATE["speaking"],
        )
        page.finish()
        return page

    def _memory_page(self) -> QWidget:
        """Everything it learned about you, and the way to take it back."""
        page = _Section("Memory", "Loading what it knows…",
                        hint='SAY: "FORGET THAT I LIKE BLACK COFFEE"')
        self._mem_section = page

        self._mem_search = QLineEdit()
        self._mem_search.setPlaceholderText("Search what it knows")
        self._mem_search.setStyleSheet(
            f"QLineEdit {{ background: {tokens.CK_CARD}; color: {tokens.CK_TEXT_HI};"
            f" border: 1px solid {tokens.CK_HAIRLINE}; border-radius: {tokens.R2}px;"
            f" padding: 8px 12px; font-family: {theme.FONT}; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {tokens.CK_STATE['listening']}; }}"
        )
        self._mem_search.textChanged.connect(lambda _: self._paint_facts())
        page._col.addWidget(self._mem_search)

        # Undo strip — a forget is reversible for as long as you're looking at it.
        self._mem_undo = QWidget()
        self._mem_undo.setStyleSheet("background: transparent;")
        undo_row = QHBoxLayout(self._mem_undo)
        undo_row.setContentsMargins(0, 0, 0, 0)
        self._mem_undo_label = QLabel("")
        self._mem_undo_label.setStyleSheet(_mono(10, tokens.CK_TEXT_LOW, 1.4))
        undo_row.addWidget(self._mem_undo_label, 1)
        undo_btn = QPushButton("Undo")
        undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        undo_btn.setStyleSheet(
            f"QPushButton {{ color: {tokens.CK_STATE['listening']};"
            f" background: transparent; border: none; font-family: {theme.FONT};"
            f" font-size: 13px; padding: 2px 6px; }}"
        )
        undo_btn.clicked.connect(self._undo_forget)
        undo_row.addWidget(undo_btn)
        self._mem_undo.setVisible(False)
        page._col.addWidget(self._mem_undo)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(theme.SCROLL_AREA)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self._mem_list = QVBoxLayout(holder)
        self._mem_list.setContentsMargins(0, 0, 6, 0)
        self._mem_list.setSpacing(0)
        self._mem_list.addStretch()
        scroll.setWidget(holder)
        page._col.addWidget(scroll, 1)

        self._paint_facts()
        return page

    def _system_page(self) -> QWidget:
        page = _Section("System", "The machine underneath.")

        self._model = QComboBox()
        self._model.addItems(
            ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5-20251001"])
        self._model.setCurrentText(self._settings.get("model", "claude-sonnet-5"))
        self._model.currentTextChanged.connect(lambda v: self._set("model", v))
        self._style_combo(self._model)
        page.row("Model", self._model)

        self._theme = QComboBox()
        self._theme.addItems(["dark", "darker", "oled"])
        self._theme.setCurrentText(self._settings.get("theme", "dark"))
        self._theme.currentTextChanged.connect(lambda v: self._set("theme", v))
        self._style_combo(self._theme)
        page.row("Brightness", self._theme,
                 "A BRIGHTNESS AXIS, NOT A LIGHT THEME — THE GROUND STAYS DARK")

        self._fallback = Toggle(self._settings.get("local_llm_fallback", True))
        self._fallback.toggled.connect(lambda v: self._set("local_llm_fallback", v))
        page.row("Local fallback", self._fallback,
                 "ANSWER FROM A LOCAL MODEL WHEN THE NETWORK IS GONE")

        statement = QLabel(
            "Everything El Fager knows is stored on this machine. The Trust "
            "Ledger is encrypted at rest and never leaves it."
        )
        statement.setWordWrap(True)
        statement.setStyleSheet(_mono(10, tokens.CK_TEXT_FAINT))
        page._col.addWidget(statement)

        page.finish()
        return page

    # ── behaviour ─────────────────────────────────────────────────────────

    def _style_combo(self, combo: QComboBox):
        combo.setFixedWidth(210)
        combo.setStyleSheet(
            f"QComboBox {{ background: {tokens.CK_CARD}; color: {tokens.CK_TEXT_HI};"
            f" border: 1px solid {tokens.CK_HAIRLINE}; border-radius: {tokens.R2}px;"
            f" padding: 6px 10px; font-family: {theme.FONT}; font-size: 13px; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {tokens.CK_CARD};"
            f" color: {tokens.CK_TEXT_HI};"
            f" selection-background-color: {tokens.CK_STATE['listening']};"
            f" selection-color: {tokens.CK_TEXT_ON_FILL}; }}"
        )

    def _set(self, key: str, value):
        """Every change writes through immediately — there is no OK button."""
        self._settings[key] = value
        _save_settings(self._settings)
        if self._on_applied:
            self._on_applied(self._settings)

    def _save_vocabulary(self):
        """The names Whisper is told to expect: one list, in the order typed,
        blanks and repeats (any case) dropped."""
        names, seen = [], set()
        for name in self._vocabulary.text().split(","):
            name = " ".join(name.split())
            if name and name.lower() not in seen:
                names.append(name)
                seen.add(name.lower())
        if names != self._settings.get("voice_vocabulary", []):
            self._set("voice_vocabulary", names)

    # ── Routines ──────────────────────────────────────────────────────────

    def _routines(self) -> list:
        scheduler = _scheduler()
        if scheduler is None:
            return []
        try:
            return scheduler.list_job_info()
        except Exception:
            return []

    def _set_routine(self, job_id: str, on: bool):
        """Pause/resume writes data/schedules.json, so it survives a restart."""
        scheduler = _scheduler()
        if scheduler is None:
            return
        try:
            scheduler.resume_job(job_id) if on else scheduler.pause_job(job_id)
        except Exception:
            pass

    # ── Skills ────────────────────────────────────────────────────────────

    def _set_skill(self, key: str, on: bool):
        """Off means core.brain never offers that skill's tools to the model."""
        disabled = set(self._settings.get("skills_disabled", []) or [])
        disabled.discard(key) if on else disabled.add(key)
        self._set("skills_disabled", sorted(disabled))

    # ── Memory ────────────────────────────────────────────────────────────

    def _facts(self) -> list:
        if self._memory is None:
            return []
        query = self._mem_search.text().strip()
        try:
            facts = (self._memory.search_facts(query) if query
                     else self._memory.get_all_facts())
        except Exception:
            return []
        facts.sort(key=lambda f: f.get("created_at", ""), reverse=True)
        return facts

    def _paint_facts(self):
        while self._mem_list.count() > 1:          # keep the trailing stretch
            item = self._mem_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) first: deleteLater() alone leaves the old
                # rows painted over the new ones until the event loop turns.
                widget.setParent(None)
                widget.deleteLater()

        facts = self._facts()
        for fact in facts:
            self._mem_list.insertWidget(self._mem_list.count() - 1,
                                        self._fact_row(fact))
        if not facts:
            empty = QLabel("Nothing matches." if self._mem_search.text().strip()
                           else "It hasn't learned anything about you yet.")
            empty.setStyleSheet(_mono(10, tokens.CK_TEXT_FAINT))
            self._mem_list.insertWidget(self._mem_list.count() - 1, empty)

        total = 0 if self._memory is None else len(self._memory.get_all_facts())
        self._mem_section._description.setText(
            f"{total} {'fact' if total == 1 else 'facts'} it learned about you. "
            "Forget anything — no questions asked."
        )

    def _fact_row(self, fact: dict) -> QWidget:
        row = QWidget()
        row.setStyleSheet(
            f"background: transparent; border-top: 1px solid {tokens.CK_HAIRLINE};")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 8, 0, 8)
        line.setSpacing(12)

        tag = QLabel(str(fact.get("category", "other")).upper())
        tag.setFixedWidth(76)
        tag.setStyleSheet(_mono(9, tokens.CK_TEXT_FAINT, 1.4))
        line.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)

        text = QLabel(fact.get("content", ""))
        text.setWordWrap(True)
        policy = text.sizePolicy()
        policy.setHeightForWidth(True)
        text.setSizePolicy(policy)
        text.setStyleSheet(
            f"color: {tokens.CK_TEXT_HI}; font-family: {theme.FONT};"
            f" font-size: 13px; background: transparent; border: none;"
        )
        line.addWidget(text, 1)

        forget = QPushButton("Forget")
        forget.setCursor(Qt.CursorShape.PointingHandCursor)
        forget.setStyleSheet(
            f"QPushButton {{ {_mono(10, tokens.CK_TEXT_LOW)} padding: 4px 8px; }}"
            f"QPushButton:hover {{ color: {tokens.CK_STATE['error']}; }}"
        )
        forget.clicked.connect(lambda _=False, f=fact: self._forget(f))
        line.addWidget(forget, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _forget(self, fact: dict):
        if self._memory is None:
            return
        self._memory.delete_fact(fact.get("id", ""))
        self._forgotten = fact
        self._mem_undo_label.setText(f"FORGOT “{fact.get('content', '')[:60]}”")
        self._mem_undo.setVisible(True)
        self._paint_facts()

    def _undo_forget(self):
        """Restores the content and its tag; the fact is stored fresh, so it
        comes back with a new id and today's date."""
        if self._memory is None or self._forgotten is None:
            return
        self._memory.store_fact(self._forgotten.get("content", ""),
                                self._forgotten.get("category", "other"))
        self._forgotten = None
        self._mem_undo.setVisible(False)
        self._paint_facts()

    def show_section(self, index: int):
        """120ms cross-fade between sections."""
        self._pages.setCurrentIndex(index)
        page = self._pages.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(120)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        # Drop the effect when the fade lands, so a page can never be left
        # stuck at zero opacity if the animation is interrupted.
        anim.finished.connect(lambda: page.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade = anim
        self._paint_rail(index)

    def _paint_rail(self, active: int):
        for i, btn in enumerate(self._rail_buttons):
            on = i == active
            btn.setStyleSheet(
                f"QPushButton {{ text-align: left; padding: 9px 12px;"
                f" border-radius: {tokens.R2}px; font-family: {theme.FONT};"
                f" font-size: 14px;"
                f" color: {tokens.CK_TEXT_HI if on else tokens.CK_TEXT_MID};"
                f" background: {tokens.CK_CARD if on else 'transparent'};"
                f" border: 1px solid "
                f"{tokens.rgba(tokens.CK_STATE['listening'], 0.35) if on else 'transparent'}; }}"
                f"QPushButton:hover {{ color: {tokens.CK_TEXT_HI}; }}"
            )

    def open(self):
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.center().x() - self.width() // 2,
                      area.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
