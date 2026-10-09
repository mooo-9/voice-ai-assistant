"""
El Fager — the Ctrl+Space overlay (the primary everyday surface).

Rebuilt to `docs/design/design_handoff_el_fager/mocks/El Fager Voice Overlay.dc.html`:
a 384px column on the Dawn palette, topped by the horizon header (a state-hue
gradient line with a 5px sun straddling it, glowing only while live), and
showing exactly one exchange at a time rather than a chat thread —
HEARD → DOING → ANSWER, each phase replacing the last.

Hard rules this file honours:
  * Nothing web-rendered here. The window is built once at boot and only
    shown/hidden; summon is one frame plus a 140ms entrance, and the mic
    opens before that animation ends.
  * Glow means live. The sun glows while the mic is open, never as decoration.
  * State is never colour alone — every state carries a motion signature and
    a text label.
  * Organic loops stop when the window hides, so the tray costs no CPU.
  * Text direction follows the language of the exchange, not the OS locale.

Visuals come from ui/theme.py; no colour literals live here.
"""

import json
import threading
from math import cos, pi, sin
from pathlib import Path

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import progress, prose, sound, staging
from ui import theme, tokens

_SETTINGS_FILE = Path("data/settings.json")

# Card width per the handoff's CANON table ("Overlay (Ctrl+Space, 384px)").
_CARD_W = 384
_SHADOW_PAD = 14
_STAGE_MIN_H = 132

# The pipeline's own words for the pre-transcription stages; anything else
# arriving as a "processing" transcript is what the user actually said.
_PIPELINE_CAPTIONS = {"Transcribing...", "Loading Whisper model..."}

_STATE_LABELS = {
    "idle": "IDLE",
    "listening": "LISTENING",
    "processing": "THINKING",
    "speaking": "SPEAKING",
    "error": "ERROR",
}


def _load_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "model": "claude-sonnet-5",
        "tts_backend": "edge",
        "voice_en": "en-US-GuyNeural",
        "wake_word_enabled": True,
        "theme": "dark",
        "local_llm_fallback": True,
        "ollama_model": "llama3.2",
    }


def _save_settings(data: dict) -> None:
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def round_photo(png: "bytes | None", size: int, ratio: float) -> "QPixmap | None":
    """A profile photo clipped to a circle, sharp at the screen's scale.
    None when there's no photo or it won't load."""
    source = QPixmap()
    if not png or not source.loadFromData(png):
        return None
    side = max(1, round(size * ratio))
    scaled = source.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(side, side)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, side, side))
    painter.setClipPath(clip)
    painter.drawPixmap((side - scaled.width()) // 2, (side - scaled.height()) // 2, scaled)
    painter.end()
    out.setDevicePixelRatio(ratio)
    return out


def _align_left(label: QLabel) -> None:
    label.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    label.setAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )


def reduced_motion() -> bool:
    """Honour the OS reduced-motion flag (Windows: "animate controls").

    When it is off the surface still changes state — it just arrives in one
    paint instead of moving: no entrance, no organic loops.
    """
    try:
        import ctypes
        SPI_GETCLIENTAREAANIMATION = 0x1042
        enabled = ctypes.c_bool(True)
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        return not enabled.value
    except Exception:
        return False


def _c(hex_color: str, alpha: float) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


# ──────────────────────────────────────────────────────────────────────────────
# Animated primitives. None of these own a timer — the window drives them all
# from one QTimer it stops on hide, so a hidden overlay costs nothing.
# ──────────────────────────────────────────────────────────────────────────────

class _Bars(QWidget):
    """The listening signature: bars breathing on staggered periods."""

    _PERIODS = (1.1, 0.85, 0.7, 0.95, 1.2)
    _OFFSETS = (0.0, 0.12, 0.05, 0.2, 0.08)

    def __init__(self, count: int = 5, height: int = 18, parent=None):
        super().__init__(parent)
        self._n = count
        self.setFixedSize(count * 6 - 3, height)
        self._t = 0.0
        self._color = QColor(theme.STATE_COLORS["listening"])

    def set_color(self, hex_color: str):
        self._color = QColor(hex_color)
        self.update()

    def tick(self, dt: float):
        self._t += dt
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        mid = self._n // 2
        for i in range(self._n):
            phase = self._t / self._PERIODS[i % 5] + self._OFFSETS[i % 5]
            frac = 0.25 + 0.75 * (0.5 - 0.5 * cos(2 * pi * phase))
            h = self.height() * frac
            p.setBrush(QColor(theme.ACCENT_BRIGHT) if i == mid else self._color)
            p.drawRoundedRect(QRectF(i * 6, self.height() - h, 3, h), 1.5, 1.5)


class _ShimmerDot(QWidget):
    """The thinking signature: a dot shimmering on a 1.4s cycle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(6, 6)
        self._t = 0.0
        self._color = QColor(theme.THINKING)

    def tick(self, dt: float):
        self._t += dt
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        alpha = 0.45 + 0.55 * (0.5 - 0.5 * cos(2 * pi * self._t / 1.4))
        c = QColor(self._color)
        c.setAlphaF(alpha)
        p.setBrush(c)
        p.drawEllipse(QRectF(0.5, 0.5, 5, 5))


class _Ripple(QWidget):
    """The speaking signature: rings leaving the core on the TTS cadence."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._t = 0.0

    def tick(self, dt: float):
        self._t = (self._t + dt) % 1.5
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = cy = self.width() / 2
        frac = self._t / 1.5
        r = 7 * (0.45 + 1.25 * frac)
        p.setPen(QPen(_c(theme.SPEAKING, 0.6 * (1 - frac)), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SPEAKING))
        p.drawEllipse(QRectF(cx - 2.5, cy - 2.5, 5, 5))


class _StateDot(QWidget):
    """Footer mic dot: glows while live, breathes on a 3.6s sine when idle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._t = 0.0
        self._color = QColor(theme.STATE_COLORS["idle"])
        self._live = False

    def set_state(self, hex_color: str, live: bool):
        self._color = QColor(hex_color)
        self._live = live
        self.update()

    def tick(self, dt: float):
        self._t += dt
        if not self._live:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = cy = self.width() / 2
        if self._live:
            glow = QRadialGradient(cx, cy, 8)
            glow.setColorAt(0.0, _c(self._color.name(), 0.55))
            glow.setColorAt(1.0, _c(self._color.name(), 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QRectF(cx - 8, cy - 8, 16, 16))
            r, alpha = 3.0, 1.0
        else:
            # idle breath: scale 1 → 1.1, opacity .45 → .85
            f = 0.5 - 0.5 * cos(2 * pi * self._t / 3.6)
            r, alpha = 3.0 * (1 + 0.1 * f), 0.45 + 0.4 * f
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(self._color.name(), alpha))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))


class _HorizonHeader(QWidget):
    """The horizon: a state-hue rule with a sun on it, glowing only while live.

    The glow pools above the line and only when a mic is open or an action is
    armed — it is the one place in this window allowed to glow.
    """

    _SUN_X = 23.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor(theme.STATE_COLORS["idle"])
        self._live = False

    def set_state(self, hex_color: str, live: bool):
        self._color = QColor(hex_color)
        self._live = live
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        name = self._color.name()
        # The horizon sits a few px above the bottom edge so the sun can
        # straddle it without the widget clipping its lower half.
        y = h - 3.0

        if self._live:
            p.save()
            p.translate(self._SUN_X + 3, y)
            p.scale(1.0, 26.0 / 140.0)   # 140x26 ellipse of light
            glow = QRadialGradient(0, 0, 140)
            glow.setColorAt(0.0, _c(name, 0.16))
            glow.setColorAt(0.7, _c(name, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QRectF(-140, -140, 280, 280))
            p.restore()

        line = QLinearGradient(0, 0, w, 0)
        line.setColorAt(0.0, _c(name, 0.0))
        line.setColorAt(min(0.99, 18.0 / max(w, 1)), _c(name, 1.0))
        line.setColorAt(0.42, _c(name, 0.35))
        line.setColorAt(0.75, _c("#FFFFFF", 0.06))
        line.setColorAt(1.0, _c("#FFFFFF", 0.06))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(line)
        p.drawRect(QRectF(0, y, w, 1))

        # the sun straddles the horizon
        if self._live:
            halo = QRadialGradient(self._SUN_X + 2.5, y + 0.5, 8)
            halo.setColorAt(0.0, _c(name, 0.5))
            halo.setColorAt(1.0, _c(name, 0.0))
            p.setBrush(halo)
            p.drawEllipse(QRectF(self._SUN_X - 5.5, y - 7.5, 16, 16))
        p.setBrush(self._color)
        p.drawEllipse(QRectF(self._SUN_X, y - 2, 5, 5))


class _CircleButton(QPushButton):
    """30px round footer control. `glyph` is drawn, not typed, so it never
    depends on a symbol font being present."""

    def __init__(self, glyph: str, tooltip: str, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self.setFixedSize(30, 30)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse()
        p.setPen(QPen(_c("#FFFFFF", 0.25 if hovered else 0.10), 1))
        p.setBrush(QColor(tokens.SURFACE_1))
        p.drawEllipse(QRectF(0.5, 0.5, 29, 29))

        ink = QPen(QColor(theme.TEXT_SECONDARY), 1.1)
        p.setPen(ink)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._glyph == "keyboard":
            p.drawRoundedRect(QRectF(9, 11, 12, 7.5), 1.5, 1.5)
            p.drawLine(QPoint(12, 16), QPoint(18, 16))
        elif self._glyph == "expand":
            # Four corner brackets — the standard "go full screen" mark, and
            # the only glyph here that has to read as *bigger*, not *another*.
            for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                cx = 15 + dx * -5      # 10 or 20
                cy = 15 + dy * -5
                p.drawLine(QPoint(cx, cy), QPoint(cx + dx * 4, cy))
                p.drawLine(QPoint(cx, cy), QPoint(cx, cy + dy * 4))
        else:  # gear — short thick teeth on a tight ring so it does not
               # read as a brightness glyph at 30px
            p.drawEllipse(QRectF(12.0, 12.0, 6, 6))
            p.setPen(QPen(QColor(theme.TEXT_SECONDARY), 1.6))
            for i in range(8):
                a = pi * i / 4
                p.drawLine(
                    QPoint(round(15 + 6.0 * cos(a)), round(15 + 6.0 * sin(a))),
                    QPoint(round(15 + 7.6 * cos(a)), round(15 + 7.6 * sin(a))),
                )


# ──────────────────────────────────────────────────────────────────────────────
# Bubble message widget — the overlay no longer threads bubbles, but the
# Command Center still builds its response list from this.
# ──────────────────────────────────────────────────────────────────────────────

class MessageBubble(QWidget):
    def __init__(self, role: str, text: str, timestamp: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        is_user = (role == "user")
        if is_user:
            layout.addStretch()

        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(460)
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        if timestamp:
            lbl.setToolTip(timestamp)

        lbl.setStyleSheet(theme.BUBBLE_USER if is_user else theme.BUBBLE_ASSISTANT)

        layout.addWidget(lbl)
        if not is_user:
            layout.addStretch()


# ──────────────────────────────────────────────────────────────────────────────
# Main overlay window
# ──────────────────────────────────────────────────────────────────────────────

class OverlayWindow(QWidget):
    text_submitted = pyqtSignal(str)
    # The registry notifies on whichever thread staged the action; emitting a
    # signal hops it back to the GUI thread.
    staged_changed = pyqtSignal()
    staged_result = pyqtSignal(str)
    progress_changed = pyqtSignal()
    # The footer's expand button. main.py owns the Cockpit (it is lazy and
    # single-instance), so the overlay asks rather than constructs.
    expand_requested = pyqtSignal()

    def __init__(self, voice_in, brain, voice_out, memory):
        super().__init__()
        self.voice_in = voice_in
        self.brain = brain
        self.voice_out = voice_out
        self.memory = memory
        self._worker = None
        self._current_state = "idle"
        self._phase = "prompt"
        self._wake_listener = None
        self._mic_muted = False
        self._heard = ""
        self._answer = ""

    def set_wake_listener(self, listener):
        self._wake_listener = listener
        self._setup_window()
        self._build_ui()
        self._setup_timers()
        self._apply_theme()
        self._set_phase("prompt")
        self._paint_state("idle")

        self._staged_cb = self.staged_changed.emit
        staging.subscribe(self._staged_cb)
        self.staged_changed.connect(self._refresh_staged)
        self.staged_result.connect(self._on_staged_result)
        self._refresh_staged()

        self._progress_cb = self.progress_changed.emit
        progress.subscribe(self._progress_cb)
        self.progress_changed.connect(self._refresh_progress)

    # ------------------------------------------------------------------ #
    #  Window setup                                                        #
    # ------------------------------------------------------------------ #

    def _setup_window(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setWindowTitle("El Fager")
        self.setFixedWidth(_CARD_W + 2 * _SHADOW_PAD)
        self._drag_start = None
        self._drag_origin = None

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW_PAD, _SHADOW_PAD, _SHADOW_PAD, _SHADOW_PAD)
        outer.setSpacing(0)

        self._card = QWidget(self)
        self._card.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(64)
        shadow.setOffset(0, 24)
        shadow.setColor(_c("#000000", 0.6))
        self._card.setGraphicsEffect(shadow)
        outer.addWidget(self._card)

        inner = QVBoxLayout(self._card)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        inner.addWidget(self._build_header())
        inner.addWidget(self._build_stage())
        inner.addWidget(self._build_receipts())
        inner.addWidget(self._build_footer())

    def _build_receipts(self) -> QWidget:
        """What actually left the machine — max three, newest first."""
        self._receipts_box = QWidget(self._card)
        self._receipts_box.setStyleSheet(
            f"border-top: 1px solid {tokens.rgba('#FFFFFF', 0.06)};"
        )
        col = QVBoxLayout(self._receipts_box)
        col.setContentsMargins(18, 10, 18, 12)
        col.setSpacing(7)
        self._receipt_rows: list[QLabel] = []
        for _ in range(3):
            row = QLabel("")
            row.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT_MONO};"
                f" font-size: 11px; border: none;"
            )
            row.setVisible(False)
            col.addWidget(row)
            self._receipt_rows.append(row)
        self._receipts_box.setVisible(False)
        return self._receipts_box

    # ── header ────────────────────────────────────────────────────────────
    def _build_header(self) -> QWidget:
        self._header = _HorizonHeader(self._card)
        row = QHBoxLayout(self._header)
        row.setContentsMargins(16, 12, 16, 14)
        row.setSpacing(9)

        name = QLabel("El Fager")
        name.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-family: {theme.FONT};"
            f" font-size: 13px; font-weight: 500;"
        )
        row.addWidget(name)

        self._state_label = QLabel(_STATE_LABELS["idle"])
        row.addWidget(self._state_label)
        row.addStretch()

        esc = QPushButton("ESC")
        esc.setCursor(Qt.CursorShape.PointingHandCursor)
        esc.setToolTip("Dismiss")
        esc.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_MUTED}; font-family: {theme.FONT_MONO};"
            f" font-size: 10px; border: 1px solid {tokens.rgba('#FFFFFF', 0.10)};"
            f" border-radius: 5px; padding: 2px 6px; background: transparent; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY};"
            f" border-color: {tokens.rgba('#FFFFFF', 0.20)}; }}"
        )
        esc.clicked.connect(self._hide)
        row.addWidget(esc)
        return self._header

    # ── stage: one exchange, phases replacing each other ──────────────────
    def _build_stage(self) -> QWidget:
        stage = QWidget(self._card)
        stage.setMinimumHeight(_STAGE_MIN_H)
        col = QVBoxLayout(stage)
        col.setContentsMargins(18, 18, 18, 14)
        col.setSpacing(12)
        col.addStretch()

        # phase: prompt — nothing said yet
        self._prompt_box = QWidget()
        pb = QVBoxLayout(self._prompt_box)
        pb.setContentsMargins(0, 10, 0, 10)
        pb.setSpacing(12)
        self._prompt_bars = _Bars(5, 18)
        bars_row = QHBoxLayout()
        bars_row.addStretch()
        bars_row.addWidget(self._prompt_bars)
        bars_row.addStretch()
        pb.addLayout(bars_row)
        prompt_lbl = QLabel("Say it.")
        prompt_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_lbl.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-family: {theme.FONT}; font-size: 14px;"
        )
        pb.addWidget(prompt_lbl)
        col.addWidget(self._prompt_box)

        # phase: hearing — your words at full size while you speak
        self._hearing_box = QWidget()
        hb = QHBoxLayout(self._hearing_box)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(12)
        self._hearing_bars = _Bars(3, 20)
        hb.addWidget(self._hearing_bars, 0, Qt.AlignmentFlag.AlignTop)
        self._heard_big = QLabel("")
        self._heard_big.setWordWrap(True)
        self._heard_big.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-family: {theme.FONT};"
            f" font-size: {theme.SZ_EMPHASIS}px;"
        )
        hb.addWidget(self._heard_big, 1)
        col.addWidget(self._hearing_box)

        # heard, shrunk to a mono caption once the work starts
        self._heard_small = QLabel("")
        self._heard_small.setWordWrap(True)
        self._heard_small.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT_MONO}; font-size: 11px;"
        )
        col.addWidget(self._heard_small)

        # phase: doing
        self._doing_box = QWidget()
        db = QHBoxLayout(self._doing_box)
        db.setContentsMargins(0, 0, 0, 0)
        db.setSpacing(8)
        self._doing_dot = _ShimmerDot()
        db.addWidget(self._doing_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._doing_caption = QLabel("")
        self._doing_caption.setStyleSheet(
            f"color: {theme.THINKING}; font-family: {theme.FONT_MONO}; font-size: 12px;"
        )
        db.addWidget(self._doing_caption, 1)
        col.addWidget(self._doing_box)

        # phase: answer
        self._answer_box = QWidget()
        ab = QHBoxLayout(self._answer_box)
        ab.setContentsMargins(0, 0, 0, 0)
        ab.setSpacing(11)
        self._answer_rule = QWidget()
        self._answer_rule.setFixedWidth(3)
        ab.addWidget(self._answer_rule)
        self._answer_label = QLabel("")
        self._answer_label.setWordWrap(True)
        self._answer_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-family: {theme.FONT};"
            f" font-size: {theme.SZ_BODY}px;"
        )
        ab.addWidget(self._answer_label, 1)
        col.addWidget(self._answer_box)

        # speaking strip — click to stop the voice
        self._speaking_box = QWidget()
        self._speaking_box.setCursor(Qt.CursorShape.PointingHandCursor)
        sb = QHBoxLayout(self._speaking_box)
        sb.setContentsMargins(14, 0, 0, 0)
        sb.setSpacing(8)
        self._ripple = _Ripple()
        sb.addWidget(self._ripple)
        speak_lbl = QLabel("STREAMING AUDIO · TAP TO MUTE")
        speak_lbl.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT_MONO};"
            f" font-size: 10px; letter-spacing: 1px;"
        )
        sb.addWidget(speak_lbl, 1)
        self._speaking_box.mousePressEvent = lambda _e: self._stop_tts()
        col.addWidget(self._speaking_box)

        # phase: error
        self._error_box = QWidget()
        eb = QHBoxLayout(self._error_box)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(8)
        self._error_dot = QWidget()
        self._error_dot.setFixedSize(5, 5)
        self._error_dot.setStyleSheet(
            f"background: {theme.ERROR}; border-radius: 2px;"
        )
        eb.addWidget(self._error_dot, 0, Qt.AlignmentFlag.AlignTop)
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            f"color: {theme.ERROR}; font-family: {theme.FONT_MONO}; font-size: 12px;"
        )
        eb.addWidget(self._error_label, 1)
        col.addWidget(self._error_box)

        col.addWidget(self._build_staged_card())
        col.addStretch()
        return stage

    # ── the armed action: shown whenever something is staged ──────────────
    def _build_staged_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("stagedCard")
        card.setStyleSheet(
            f"QWidget#stagedCard {{ background: {tokens.SURFACE_1};"
            f" border: 1px solid {tokens.rgba(theme.ACCENT, 0.40)};"
            f" border-radius: 14px; }}"
        )
        glow = QGraphicsDropShadowEffect(card)
        glow.setBlurRadius(20)
        glow.setOffset(0, 0)
        glow.setColor(_c(theme.ACCENT, 0.12))
        card.setGraphicsEffect(glow)

        box = QVBoxLayout(card)
        box.setContentsMargins(13, 13, 13, 13)
        box.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._staged_kicker = QLabel("")
        self._staged_kicker.setStyleSheet(
            f"color: {theme.ACCENT_BRIGHT}; font-family: {theme.FONT_MONO};"
            f" font-size: 10px; letter-spacing: 1.4px; border: none;"
        )
        head.addWidget(self._staged_kicker)
        self._staged_target = QLabel("")
        self._staged_target.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-family: {theme.FONT}; font-size: 11px;"
            f" background: {tokens.SURFACE_2}; border-radius: 9px; padding: 2px 9px;"
        )
        head.addWidget(self._staged_target)
        head.addStretch()
        box.addLayout(head)

        # the body reads in the target medium's own shape, beside the
        # recipient's photo when there is one
        body_row = QHBoxLayout()
        body_row.setSpacing(10)
        self._staged_photo = QLabel("")
        self._staged_photo.setFixedSize(40, 40)
        self._staged_photo.setStyleSheet("border: none; background: transparent;")
        self._staged_photo.setVisible(False)
        body_row.addWidget(self._staged_photo, 0, Qt.AlignmentFlag.AlignTop)
        self._staged_body = QLabel("")
        self._staged_body.setWordWrap(True)
        self._staged_body.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-family: {theme.FONT};"
            f" font-size: 13px; background: {tokens.SURFACE_0};"
            f" border: 1px solid {tokens.rgba('#FFFFFF', 0.07)};"
            f" border-radius: 10px; padding: 9px 12px;"
        )
        body_row.addWidget(self._staged_body, 1)
        box.addLayout(body_row)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self._staged_send = QPushButton("Send")
        self._staged_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._staged_send.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_ON_ACCENT}; background: {theme.ACCENT};"
            f" border: none; border-radius: 9px; padding: 6px 14px;"
            f" font-family: {theme.FONT}; font-size: 12px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_BRIGHT}; }}"
            f"QPushButton:pressed {{ background: {theme.ACCENT_PRESS}; }}"
            f"QPushButton:disabled {{ background: {tokens.SURFACE_3};"
            f" color: {theme.TEXT_MUTED}; }}"
        )
        self._staged_send.clicked.connect(self._confirm_staged)
        actions.addWidget(self._staged_send)

        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_SECONDARY}; background: transparent;"
            f" border: none; padding: 6px 8px; font-family: {theme.FONT};"
            f" font-size: 12px; font-weight: 500; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}"
        )
        cancel.clicked.connect(self._cancel_staged)
        actions.addWidget(cancel)
        actions.addStretch()

        self._staged_hint = QLabel('SAY "SEND"')
        self._staged_hint.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT_MONO};"
            f" font-size: 10px; border: none;"
        )
        actions.addWidget(self._staged_hint)
        box.addLayout(actions)

        self._staged_card = card
        card.setVisible(False)
        return card

    # ── footer ────────────────────────────────────────────────────────────
    def _build_footer(self) -> QWidget:
        foot = QWidget(self._card)
        foot.setStyleSheet(f"border-top: 1px solid {tokens.rgba('#FFFFFF', 0.06)};")
        wrap = QVBoxLayout(foot)
        wrap.setContentsMargins(14, 10, 14, 10)
        wrap.setSpacing(8)

        # live indicators (pomodoro / focus / offline / memory) stay available
        self._status_bar = QLabel("")
        self._status_bar.setStyleSheet(theme.STATUS_BAR + "border: none;")
        self._status_bar.setVisible(False)
        wrap.addWidget(self._status_bar)

        # voice mode
        self._voice_row = QWidget()
        vr = QHBoxLayout(self._voice_row)
        vr.setContentsMargins(0, 0, 0, 0)
        vr.setSpacing(10)
        self._mic_dot = _StateDot()
        self._mic_dot.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_dot.setToolTip("Mute / unmute the mic")
        self._mic_dot.mousePressEvent = lambda _e: self._toggle_mute()
        vr.addWidget(self._mic_dot)
        self._mic_label = QLabel("MIC IS LIVE WHILE OPEN")
        self._mic_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-family: {theme.FONT_MONO};"
            f" font-size: 10px; letter-spacing: 1.2px; border: none;"
        )
        vr.addWidget(self._mic_label)
        vr.addStretch()
        self._settings_btn = _CircleButton("gear", "Settings")
        self._settings_btn.clicked.connect(self._open_settings)
        vr.addWidget(self._settings_btn)
        self._type_btn = _CircleButton("keyboard", "Type instead")
        self._type_btn.clicked.connect(lambda: self._set_typed_mode(True))
        vr.addWidget(self._type_btn)
        self._expand_btn = _CircleButton("expand", "Open the Cockpit  (full screen)")
        self._expand_btn.clicked.connect(self._expand_to_cockpit)
        vr.addWidget(self._expand_btn)
        wrap.addWidget(self._voice_row)

        # typed mode — same brain, same exchange
        self._typed_row = QWidget()
        tr = QHBoxLayout(self._typed_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(10)
        self._text_input = QLineEdit()
        self._text_input.setPlaceholderText("Type it — same brain, same exchange")
        self._text_input.setStyleSheet(
            f"QLineEdit {{ background: {tokens.SURFACE_1};"
            f" border: 1px solid {tokens.rgba(theme.ACCENT, 0.35)};"
            f" border-radius: {tokens.R_PILL}px; color: {theme.TEXT_PRIMARY};"
            f" padding: 7px 14px; font-family: {theme.FONT}; font-size: 13px; }}"
            f"QLineEdit::placeholder {{ color: {theme.TEXT_MUTED}; }}"
        )
        self._text_input.returnPressed.connect(self._on_text_entered)
        self._text_input.installEventFilter(self)
        tr.addWidget(self._text_input, 1)
        hide_btn = QPushButton("HIDE")
        hide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        hide_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_MUTED}; font-family: {theme.FONT_MONO};"
            f" font-size: 10px; background: transparent; border: none; padding: 4px 8px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}"
        )
        hide_btn.clicked.connect(lambda: self._set_typed_mode(False))
        tr.addWidget(hide_btn)
        self._typed_row.setVisible(False)
        wrap.addWidget(self._typed_row)
        return foot

    def _apply_theme(self):
        settings = _load_settings()
        self._card.setStyleSheet(theme.card_style(settings.get("theme", "dark")))

    def _setup_timers(self):
        # One loop drives every organic motion, and it only runs while the
        # window is on screen — a hidden overlay costs nothing.
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._tick)

        self._status_poll_timer = QTimer(self)
        self._status_poll_timer.setInterval(1000)
        self._status_poll_timer.timeout.connect(self._update_status_bar)

    def _tick(self):
        dt = 0.033
        if self._prompt_box.isVisible():
            self._prompt_bars.tick(dt)
        if self._hearing_box.isVisible():
            self._hearing_bars.tick(dt)
        if self._doing_box.isVisible():
            self._doing_dot.tick(dt)
        if self._speaking_box.isVisible():
            self._ripple.tick(dt)
        self._mic_dot.tick(dt)

    # ------------------------------------------------------------------ #
    #  Phase + state rendering                                             #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  The armed action                                                    #
    # ------------------------------------------------------------------ #

    _VERBS = {"calendar": "Delete"}
    _HINTS = {"calendar": 'SAY "DELETE"'}

    @pyqtSlot()
    def _refresh_staged(self):
        action = staging.current()
        if action is None:
            self._staged_card.setVisible(False)
        else:
            self._staged_kicker.setText(f"{action.medium.upper()} →")
            self._staged_target.setText(action.target)
            _align_left(self._staged_body)
            self._staged_body.setText(action.body)
            photo = round_photo(action.photo, 40, self.devicePixelRatioF())
            if photo is not None:
                self._staged_photo.setPixmap(photo)
            self._staged_photo.setVisible(photo is not None)
            self._staged_send.setText(self._VERBS.get(action.medium, "Send"))
            self._staged_send.setEnabled(True)
            self._staged_hint.setText(
                self._HINTS.get(action.medium, 'SAY "SEND"')
            )
            self._staged_card.setVisible(True)

        rows = staging.receipts()
        for i, row in enumerate(self._receipt_rows):
            if i < len(rows):
                row.setText(f"{rows[i]['summary']} · {rows[i]['at']}")
                row.setVisible(True)
            else:
                row.setVisible(False)
        self._receipts_box.setVisible(bool(rows))

        self._paint_state(self._current_state)
        self._resize_to_content()

    @pyqtSlot()
    def _refresh_progress(self):
        """The caption says which tool is working, not a generic spinner."""
        label = progress.active_label()
        if label and self._phase in ("hearing", "doing"):
            self._doing_caption.setText(f"{label}…")
            tint = tokens.SKILL_TINT.get(
                progress.skill_for(label.replace(" ", "_")) or "", theme.THINKING
            )
            self._doing_caption.setStyleSheet(
                f"color: {tint}; font-family: {theme.FONT_MONO}; font-size: 12px;"
            )

    def _confirm_staged(self):
        """Confirm off the GUI thread — a send opens apps and waits on focus."""
        self._staged_send.setEnabled(False)
        threading.Thread(target=self._run_confirm, daemon=True).start()

    def _run_confirm(self):
        try:
            result = staging.confirm()
        except Exception as e:
            result = f"[send failed: {e}]"
        self.staged_result.emit(result)

    def _cancel_staged(self):
        staging.cancel()

    @pyqtSlot(str)
    def _on_staged_result(self, result: str):
        self._answer = result
        _align_left(self._answer_label)
        self._answer_label.setText(result)
        self._set_phase("answering")

    def _set_phase(self, phase: str):
        """One exchange at a time: each phase replaces the one before it."""
        self._phase = phase
        self._prompt_box.setVisible(phase == "prompt")
        self._hearing_box.setVisible(phase == "hearing")
        self._heard_small.setVisible(
            bool(self._heard) and phase in ("doing", "answering", "error")
        )
        self._doing_box.setVisible(phase == "doing")
        self._answer_box.setVisible(phase == "answering" and bool(self._answer))
        self._speaking_box.setVisible(
            phase == "answering" and self._current_state == "speaking"
        )
        self._error_box.setVisible(phase == "error")
        self._resize_to_content()

    def _paint_state(self, state: str):
        """Hue + label together — the design forbids signalling by colour alone."""
        color = theme.STATE_COLORS.get(state, theme.TEXT_MUTED)
        armed = staging.current() is not None
        # Glow = live: a mic is open OR an action is armed. Never decoration.
        live = armed or (state in ("listening", "speaking") and not self._mic_muted)
        self._header.set_state(color, live or state != "idle")
        self._mic_dot.set_state(color, live)
        self._state_label.setText(_STATE_LABELS.get(state, state.upper()))
        self._state_label.setStyleSheet(
            f"color: {color}; font-family: {theme.FONT_MONO}; font-size: 10px;"
            f" font-weight: 500; letter-spacing: 1.4px;"
        )
        self._prompt_bars.set_color(color if state == "listening" else theme.ACCENT)
        self._hearing_bars.set_color(color if state == "listening" else theme.ACCENT)
        rule = theme.SPEAKING if state == "speaking" else theme.ACCENT
        self._answer_rule.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {rule}, stop:1 transparent); border-radius: 2px;"
        )

    def _resize_to_content(self):
        # The card grows with the exchange but never past the screen — a long
        # answer is spoken, and the caption is a caption, not a transcript.
        wanted = self.sizeHint().height()
        screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        if screen is not None:
            wanted = min(wanted, int(screen.availableGeometry().height() * 0.7))
        self.setFixedHeight(wanted)

    # ------------------------------------------------------------------ #
    #  Live status indicators                                              #
    # ------------------------------------------------------------------ #

    def _update_status_bar(self):
        if not self.isVisible():
            return
        indicators = []

        # Pomodoro
        try:
            pom_file = Path("data/pomodoro_active.json")
            if pom_file.exists():
                data = json.loads(pom_file.read_text(encoding="utf-8"))
                if data.get("active"):
                    from datetime import datetime
                    end_ts = data.get("end_time")
                    if end_ts:
                        end = datetime.fromisoformat(end_ts)
                        remaining = int((end - datetime.now()).total_seconds())
                        if remaining > 0:
                            m, s = divmod(remaining, 60)
                            label = data.get("label", "Focus")
                            indicators.append(f"{label.upper()} {m}:{s:02d}")
        except Exception:
            pass

        # Focus mode
        try:
            focus_file = Path("data/focus_mode.json")
            if focus_file.exists():
                data = json.loads(focus_file.read_text(encoding="utf-8"))
                if data.get("active"):
                    indicators.append("FOCUS ON")
        except Exception:
            pass

        # Offline / local LLM
        try:
            if getattr(self.brain, "_offline_mode", False):
                indicators.append("OFFLINE")
        except Exception:
            pass

        # Vector memory failed to load (facts still work)
        try:
            if getattr(self.memory, "degraded", False):
                indicators.append("MEMORY OFF")
        except Exception:
            pass

        if indicators:
            self._status_bar.setText("  ·  ".join(indicators))
            self._status_bar.setVisible(True)
        else:
            self._status_bar.setVisible(False)

    # ------------------------------------------------------------------ #
    #  Controls                                                            #
    # ------------------------------------------------------------------ #

    def _toggle_mute(self):
        self._mic_muted = not self._mic_muted
        self._mic_label.setText("MIC MUTED" if self._mic_muted else "MIC IS LIVE WHILE OPEN")
        try:
            if self._mic_muted:
                self.voice_in.stop_recording()
            # Un-mute just happens naturally at next pipeline start
        except Exception:
            pass
        self._paint_state(self._current_state)

    def _stop_tts(self):
        try:
            self.voice_out.stop()
        except Exception:
            pass

    def _set_typed_mode(self, on: bool):
        self._typed_row.setVisible(on)
        self._voice_row.setVisible(not on)
        self._resize_to_content()
        if on:
            self._text_input.setFocus()

    def _expand_to_cockpit(self):
        """Small surface hands off to the big one: the overlay gets out of the
        way first, so the Cockpit opens onto a clear screen."""
        self._hide()
        self.expand_requested.emit()

    def _open_settings(self):
        """The design's Settings surface; changes apply as they're made."""
        if getattr(self, "_settings_window", None) is None:
            from ui.settings import SettingsWindow
            self._settings_window = SettingsWindow(
                on_applied=self._apply_settings, memory=self.memory)
        self._settings_window.open()

    def _apply_settings(self, settings: dict):
        self._apply_theme()
        # Propagate model change to brain
        new_model = settings.get("model", "claude-sonnet-5")
        if hasattr(self.brain, "_model"):
            self.brain._model = new_model
        # Propagate TTS/voice changes to voice_out
        if hasattr(self.voice_out, "apply_settings"):
            self.voice_out.apply_settings(settings)
        # Propagate wake word toggle
        if self._wake_listener is not None and hasattr(self._wake_listener, "available"):
            should_run = settings.get("wake_word_enabled", True)
            if should_run and not getattr(self._wake_listener, "_running", False):
                self._wake_listener.start()
            elif not should_run and getattr(self._wake_listener, "_running", False):
                self._wake_listener.stop()

    # ------------------------------------------------------------------ #
    #  State management                                                    #
    # ------------------------------------------------------------------ #

    @pyqtSlot(str, str, str)
    def on_state_update(self, state: str, transcript: str, response: str):
        self._current_state = state

        if state == "listening":
            self._heard = ""
            self._answer = ""
            self._heard_big.setText("")
            self._set_phase("prompt")

        elif state == "processing":
            if transcript and transcript not in _PIPELINE_CAPTIONS:
                self._heard = transcript
                _align_left(self._heard_big)
                self._heard_big.setText(transcript)
                _align_left(self._heard_small)
                self._heard_small.setText(transcript)
                sound.play("heard")    # the utterance commits hearing → doing
                self._doing_caption.setText("working…")
                # HEARD holds the stage for a beat before it shrinks into
                # DOING — STT gives us the line all at once, so this is where
                # the design's "your words large" moment lives.
                self._set_phase("hearing")
                QTimer.singleShot(450, self._heard_settles)
            else:
                self._doing_caption.setText((transcript or "working…").lower())
                self._set_phase("doing")

        elif state == "speaking":
            if response:
                self._answer = response
                _align_left(self._answer_label)
                self._answer_label.setText(prose.plain(response))
            self._set_phase("answering")

        self._paint_state(state)

    def _heard_settles(self):
        """The heard line shrinks to a caption — unless the answer beat us."""
        if self._phase == "hearing":
            self._set_phase("doing")

    @pyqtSlot(str)
    def on_error(self, message: str):
        self._current_state = "error"
        self._error_label.setText(message.upper())
        self._set_phase("error")
        self._paint_state("error")
        # Only a mic-class failure opens typing on its own (the design's
        # "mic busy/denied" edge state); other errors just report and clear.
        low = message.lower()
        if "heard" in low or "mic" in low or "whisper" in low:
            self._set_typed_mode(True)
        # the design returns the surface to rest on its own after 4s
        QTimer.singleShot(4000, self._recover_from_error)

    def _recover_from_error(self):
        if self._current_state == "error" and self.isVisible():
            self._current_state = "idle"
            self._heard = ""
            self._set_phase("prompt")
            self._paint_state("idle")

    @pyqtSlot()
    def on_pipeline_done(self):
        if self._current_state == "speaking":
            sound.play("resolved")    # the run finished
            self._text_input.clear()
            self._paint_state("idle")
            QTimer.singleShot(800, self._auto_restart_listen)
        elif self._current_state == "listening":
            self._paint_state("idle")

    def _auto_restart_listen(self):
        if self.isVisible() and not (self._worker and self._worker.isRunning()):
            if not self._mic_muted:
                self._reset_ui()
                self._start_pipeline()

    # ------------------------------------------------------------------ #
    #  Toggle / show / hide                                               #
    # ------------------------------------------------------------------ #

    def toggle(self):
        if self.isVisible():
            if self._worker and self._worker.isRunning():
                self._hide()
            else:
                self._reset_ui()
                self._start_pipeline()
        else:
            self._show_and_start()

    def wake_word_activate(self):
        # Show first, decide about the mic after — same reason as
        # _show_and_start: a running worker must never mean an invisible
        # window.
        if self._worker and self._worker.isRunning():
            if not self.isVisible():
                self._show_anchored()
            return
        if self._mic_muted:
            return
        self._reset_ui()
        self._show_anchored()
        self._start_pipeline()

    def present(self):
        """Show the surface at rest — anchored and animated, but no mic.
        Used at launch, where appearing is not the same as being summoned."""
        self._reset_ui()
        self._show_anchored()
        self.activateWindow()

    def _show_and_start(self):
        # Showing is unconditional. This used to return early whenever a
        # worker was still running, which is exactly what produced "it lags,
        # then it won't open but audio still works": a hide left the old
        # worker alive, and every reopen after that silently did nothing while
        # the orphan kept the microphone.
        self._reset_ui()
        # Shown and listening in the same tick — the 140ms entrance animates
        # in parallel, so the mic is never gated on it.
        self._show_anchored()
        if self._worker and self._worker.isRunning():
            return          # a turn is already live; don't stack a second
        if not self._mic_muted:
            self._start_pipeline()

    def _show_anchored(self):
        """Anchor to the screen edge nearest the cursor, then enter in 140ms."""
        self._resize_to_content()
        screen = QApplication.screenAt(self.cursor().pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        cursor_x = self.cursor().pos().x()
        near_right = cursor_x >= area.center().x()
        x = area.right() - self.width() - 10 if near_right else area.left() + 10
        y = area.bottom() - self.height() - 56
        self.move(x, y + 6)
        self.show()
        self.raise_()
        sound.play("summon")          # with the 140ms visual snap
        if not reduced_motion():
            self._anim_timer.start()      # nothing breathes under reduced motion
        self._status_poll_timer.start()
        self._enter(QPoint(x, y))

    def _enter(self, target: QPoint):
        if reduced_motion():
            self.move(target)
            self.setWindowOpacity(1.0)
            return
        self.setWindowOpacity(0.0)
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(tokens.T_FAST)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(tokens.T_FAST)
        slide.setEndValue(target)
        for anim in (fade, slide):
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._enter_anims = (fade, slide)

    def _hide(self):
        self._anim_timer.stop()          # organic loops pause when hidden
        self._status_poll_timer.stop()
        if self._worker and self._worker.isRunning():
            # cancel(), not quit() + wait(2000). quit() asks a thread's event
            # loop to exit and PipelineWorker.run() has none, so it did
            # nothing — the wait then froze this thread, the GUI thread, for
            # up to two seconds and the worker outlived the hide regardless.
            self._worker.cancel()
            # A short courtesy wait for the common case where it stops at
            # once. It is not required to have finished: _show_and_start no
            # longer refuses to open while one is winding down.
            self._worker.wait(150)
        self.hide()
        self._current_state = "idle"

    def _reset_ui(self):
        self._heard = ""
        self._answer = ""
        self._heard_big.setText("")
        self._heard_small.setText("")
        self._answer_label.setText("")
        self._text_input.clear()
        self._current_state = "idle"
        self._set_phase("prompt")
        self._paint_state("idle")

    # ------------------------------------------------------------------ #
    #  Pipeline                                                            #
    # ------------------------------------------------------------------ #

    def _start_pipeline(self, text_input: "str | None" = None):
        from core.pipeline import PipelineWorker

        self._worker = PipelineWorker(
            self.voice_in,
            self.brain,
            self.voice_out,
            self.memory,
            text_input=text_input,
        )
        self._worker.state_update.connect(self.on_state_update)
        self._worker.done.connect(self.on_pipeline_done)
        self._worker.error.connect(self.on_error)

        if self._wake_listener:
            self._worker.started.connect(self._wake_listener.pause)
            self._worker.done.connect(self._wake_listener.resume)
            self._worker.error.connect(lambda _: self._wake_listener.resume() if self._wake_listener else None)

        self._worker.start()

    def analyze_screen(self):
        if self._worker and self._worker.isRunning():
            if not self.isVisible():
                self._show_anchored()
            return          # a turn is already live; don't stack a second
        self._reset_ui()
        self._show_anchored()
        self._start_pipeline(text_input="what's on my screen")

    def query_memory(self):
        if self._worker and self._worker.isRunning():
            if not self.isVisible():
                self._show_anchored()
            return          # a turn is already live; don't stack a second
        self._reset_ui()
        self._show_anchored()
        self._start_pipeline(text_input="what do you know about me?")

    def run_briefing(self, prompt: str):
        if self._worker and self._worker.isRunning():
            return
        self._reset_ui()
        self._show_anchored()
        self._start_pipeline(text_input=prompt)

    def _on_text_entered(self):
        text = self._text_input.text().strip()
        if not text or (self._worker and self._worker.isRunning()):
            return
        self._heard = text
        _align_left(self._heard_small)
        self._heard_small.setText(text)
        self._text_input.clear()
        self._start_pipeline(text_input=text)

    # ------------------------------------------------------------------ #
    #  Keyboard                                                            #
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        if obj is self._text_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._hide()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self._hide()
        elif event.key() == Qt.Key.Key_T and not self._typed_row.isVisible():
            self._set_typed_mode(True)
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    #  Window dragging (frameless window moves by dragging the card)      #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Close → hide to tray (real quit is via tray menu)                  #
    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        # Only hidden, never destroyed: it stays subscribed to steps and
        # drafts, or the next open would show neither.
        event.ignore()
        self._hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
