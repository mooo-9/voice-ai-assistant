"""
El Fager — the Trust Ledger surface.

The readable face of core/ledger.py: every action El Fager took on Mo's
behalf, newest first, each one saying why it happened. Reached from the
cockpit's bottom bar.

Two things this surface must never do, and the code keeps both:
  * It never speculates. The search strip answers out of the record or says
    it found nothing — it is labelled ANSWERED FROM THE LEDGER, NOT THE MODEL
    because that is literally what core.ledger.search does.
  * It never edits. Revoking appends a new entry; the original stays on
    screen and picks up a REVOKED mark. Past its reversal window an entry
    reads SEALED, because the world can no longer be walked back.

Cockpit palette — this is a cockpit-family surface, not a Dawn one.
"""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import ledger
from ui import theme, tokens

_FILTERS = (("All", None), ("Sent", "sent"), ("Changed", "changed"),
            ("Read", "read"), ("Learned", "learned"))

# What revoking an entry would actually do, per medium. Shown on the button
# so the action is never vaguer than the thing it performs.
_REVOKE_VERB = {
    "whatsapp": "Delete for all",
    "gmail": "Unsend",
    "calendar": "Restore",
}


def _mono(size: int, color: str, tracking: float = 1.0) -> str:
    return (
        f"color: {color}; font-family: {theme.FONT_MONO}; font-size: {size}px;"
        f" letter-spacing: {tracking}px; background: transparent; border: none;"
    )


class _Row(QWidget):
    """One entry: time · category dot · kicker · summary · provenance."""

    def __init__(self, entry: dict, on_revoke, parent=None):
        super().__init__(parent)
        self._entry = entry
        tint = tokens.LEDGER_TINT.get(entry.get("category", "acted"), tokens.CK_TEXT_MID)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 10, 0, 10)
        row.setSpacing(14)

        time_lbl = QLabel(entry.get("at", ""))
        time_lbl.setFixedWidth(56)
        time_lbl.setStyleSheet(_mono(11, tokens.CK_TEXT_LOW))
        row.addWidget(time_lbl, 0, Qt.AlignmentFlag.AlignTop)

        dot = QLabel("•")
        dot.setFixedWidth(14)
        dot.setStyleSheet(_mono(15, tint, 0))
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        kicker = QLabel(entry.get("medium", "").upper())
        kicker.setStyleSheet(_mono(10, tint, 1.4))
        head.addWidget(kicker)
        target = QLabel(entry.get("target", ""))
        target.setStyleSheet(
            f"color: {tokens.CK_TEXT_HI}; font-family: {theme.FONT}; font-size: 11px;"
            f" background: {tokens.CK_CHIP}; border-radius: 9px; padding: 2px 9px;"
        )
        head.addWidget(target)
        head.addStretch()
        body.addLayout(head)

        summary = QLabel(entry.get("summary", ""))
        summary.setWordWrap(True)
        summary.setStyleSheet(
            f"color: {tokens.CK_TEXT_HI}; font-family: {theme.FONT};"
            f" font-size: 14px; background: transparent;"
        )
        if entry.get("revoked"):
            summary.setStyleSheet(
                f"color: {tokens.CK_TEXT_LOW}; font-family: {theme.FONT};"
                f" font-size: 14px; background: transparent;"
                f" text-decoration: line-through;"
            )
        body.addWidget(summary)

        # every entry says why it happened
        prov = QLabel(entry.get("provenance", ""))
        prov.setWordWrap(True)
        prov.setStyleSheet(_mono(10, tokens.CK_TEXT_FAINT, 1.2))
        body.addWidget(prov)
        row.addLayout(body, 1)

        # the action, or the reason there isn't one
        if entry.get("revoked"):
            mark = QLabel("REVOKED")
            mark.setStyleSheet(_mono(10, tokens.CK_STATE["error"], 1.4))
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        elif entry.get("sealed"):
            mark = QLabel("SEALED")
            mark.setStyleSheet(_mono(10, tokens.CK_TEXT_FAINT, 1.4))
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        else:
            verb = _REVOKE_VERB.get(entry.get("medium", ""), "Revoke")
            btn = QPushButton(verb)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ color: {tokens.CK_STATE['error']};"
                f" font-family: {theme.FONT}; font-size: 11px; background: transparent;"
                f" border: 1px solid {tokens.rgba(tokens.CK_STATE['error'], 0.35)};"
                f" border-radius: 8px; padding: 4px 10px; }}"
                f"QPushButton:hover {{ background: {tokens.rgba(tokens.CK_STATE['error'], 0.14)}; }}"
            )
            btn.clicked.connect(lambda: on_revoke(entry))
            row.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)


class TrustLedgerWindow(QWidget):
    """The record, readable. Opens over the cockpit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = None
        self._build_ui()
        self.refresh()
        if parent is not None:
            # Keep covering the stage: it is a child of the cockpit rather
            # than a widget in its layout, so nothing else would resize it.
            parent.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def _build_ui(self):
        # A panel over the cockpit, the way the ? map is: the stage dimmed
        # behind, the record on a card of its own. It used to carry the
        # frameless flag while parented to the cockpit — which does not make a
        # window — so it painted no background at all and the sphere read
        # straight through every line of it.
        self.setWindowTitle("El Fager — Trust Ledger")
        self.setObjectName("scrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#scrim {{ background: {tokens.rgba(tokens.CK_VOID, 0.93)}; }}")
        self.resize(940, 820)

        backdrop = QVBoxLayout(self)
        backdrop.setContentsMargins(40, 40, 40, 40)
        self._card = QWidget()
        self._card.setObjectName("card")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card.setStyleSheet(
            f"#card {{ background: {tokens.CK_PANEL};"
            f" border: 1px solid {tokens.CK_HAIRLINE};"
            f" border-radius: {tokens.R3}px; }}"
        )
        backdrop.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        col = QVBoxLayout(self._card)
        col.setContentsMargins(32, 26, 32, 22)
        col.setSpacing(16)

        # header + the two counters that matter
        head = QHBoxLayout()
        title = QLabel("Trust Ledger")
        title.setStyleSheet(
            f"color: {tokens.CK_TEXT_HI}; font-family: {theme.FONT};"
            f" font-size: 21px; font-weight: 500; background: transparent;"
        )
        head.addWidget(title)
        head.addStretch()
        self._counters = QLabel("")
        self._counters.setStyleSheet(_mono(11, tokens.CK_TEXT_MID, 1.2))
        head.addWidget(self._counters)
        close = QPushButton("ESC")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            f"QPushButton {{ {_mono(10, tokens.CK_TEXT_LOW)}"
            f" border: 1px solid {tokens.rgba('#FFFFFF', 0.10)};"
            f" border-radius: 5px; padding: 3px 7px; }}"
            f"QPushButton:hover {{ color: {tokens.CK_TEXT_HI}; }}"
        )
        close.clicked.connect(self.hide)
        head.addWidget(close)
        col.addLayout(head)

        # search — reads the record, never the model
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self._search = QLineEdit()
        self._search.setPlaceholderText("What did you send Omar?  ·  everything sent yesterday")
        self._search.setStyleSheet(
            f"QLineEdit {{ background: {tokens.CK_CARD}; color: {tokens.CK_TEXT_HI};"
            f" border: 1px solid {tokens.CK_HAIRLINE}; border-radius: {tokens.R2}px;"
            f" padding: 9px 14px; font-family: {theme.FONT}; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {tokens.CK_STATE['listening']}; }}"
        )
        self._search.textChanged.connect(self.refresh)
        search_row.addWidget(self._search, 1)
        col.addLayout(search_row)

        self._answer = QLabel("")
        self._answer.setWordWrap(True)
        self._answer.setVisible(False)
        col.addWidget(self._answer)

        # filter pills
        pills = QHBoxLayout()
        pills.setSpacing(8)
        self._pills = {}
        for label, key in _FILTERS:
            pill = QPushButton(label)
            pill.setCursor(Qt.CursorShape.PointingHandCursor)
            pill.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            pills.addWidget(pill)
            self._pills[key] = pill
        pills.addStretch()
        col.addLayout(pills)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(theme.SCROLL_AREA)
        self._rows_host = QWidget()
        self._rows_host.setStyleSheet("background: transparent;")
        self._rows = QVBoxLayout(self._rows_host)
        # Room for the scrollbar: a long record put it over the revoke button.
        self._rows.setContentsMargins(0, 0, 14, 0)
        self._rows.setSpacing(0)
        self._rows.addStretch()
        scroll.setWidget(self._rows_host)
        col.addWidget(scroll, 1)

        law = QLabel(
            "THE LAW — APPEND-ONLY. NOTHING HERE IS EVER EDITED OR DELETED. "
            "A REVOKE WRITES A NEW ENTRY; THE ORIGINAL STAYS, MARKED REVOKED. "
            "STORED LOCALLY, ENCRYPTED AT REST, NEVER LEAVES THE MACHINE."
        )
        law.setWordWrap(True)
        law.setStyleSheet(_mono(10, tokens.CK_TEXT_FAINT, 1.2))
        col.addWidget(law)

    def resizeEvent(self, event):
        """The card takes the stage it is given, up to its own size. Left to
        its layout it asked only for what its rows needed, which on a long
        record meant four entries and a scrollbar for the other fifty-six."""
        super().resizeEvent(event)
        # 40px of backdrop all round, so the card gets what is left of both.
        self._card.setFixedSize(min(860, max(320, self.width() - 80)),
                                min(760, max(280, self.height() - 80)))

    def _set_filter(self, key: "str | None"):
        self._filter = key
        self.refresh()

    def _paint_pills(self):
        for key, pill in self._pills.items():
            on = key == self._filter
            pill.setStyleSheet(
                f"QPushButton {{ color: "
                f"{tokens.CK_TEXT_ON_FILL if on else tokens.CK_TEXT_MID};"
                f" background: {tokens.CK_STATE['listening'] if on else tokens.CK_CARD};"
                f" border: 1px solid "
                f"{'transparent' if on else tokens.CK_HAIRLINE};"
                f" border-radius: {tokens.R_PILL}px; padding: 5px 14px;"
                f" font-family: {theme.FONT}; font-size: 12px; }}"
            )

    def refresh(self):
        """Rebuild from the record. Search wins over the filter when present."""
        query = self._search.text().strip()
        if query:
            rows = ledger.search(query)
            self._answer.setText(
                f"ANSWERED FROM THE LEDGER, NOT THE MODEL  ·  "
                f"{len(rows)} ENTR{'Y' if len(rows) == 1 else 'IES'} MATCH “{query}”"
                if rows else
                f"ANSWERED FROM THE LEDGER, NOT THE MODEL  ·  "
                f"NOTHING IN THE RECORD MATCHES “{query}”"
            )
            self._answer.setStyleSheet(
                f"{_mono(10, tokens.CK_STATE['speaking'], 1.4)}"
                f" border-left: 2px solid {tokens.CK_STATE['speaking']};"
                f" padding: 8px 12px; background: {tokens.CK_CARD};"
            )
            self._answer.setVisible(True)
        else:
            rows = ledger.entries(limit=100, category=self._filter)
            self._answer.setVisible(False)

        while self._rows.count() > 1:
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget:
                # setParent(None) removes it from the display now; deleteLater
                # alone leaves the old rows painted over the new ones until
                # the event loop catches up.
                widget.setParent(None)
                widget.deleteLater()

        for entry in rows:
            self._rows.insertWidget(self._rows.count() - 1, _Row(entry, self._revoke))
            rule = QWidget()
            rule.setFixedHeight(1)
            rule.setStyleSheet(f"background: {tokens.CK_HAIRLINE};")
            self._rows.insertWidget(self._rows.count() - 1, rule)

        counts = ledger.counters()
        self._counters.setText(
            f"{counts['total_30d']} ENTRIES · 30 DAYS      "
            f"{counts['revocable']} STILL REVOCABLE"
        )
        self._paint_pills()

    def _revoke(self, entry: dict):
        """Append the reversal. Nothing on disk is rewritten.

        `performed` stays False: recording a revocation is not the same as
        reaching into WhatsApp and deleting the message, and the ledger must
        not imply otherwise.
        """
        ledger.revoke(
            entry["id"],
            provenance="YOU REVOKED THIS FROM THE LEDGER",
            performed=False,
        )
        self.refresh()

    def open(self):
        self.refresh()
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        else:
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.center().x() - self.width() // 2,
                          area.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
