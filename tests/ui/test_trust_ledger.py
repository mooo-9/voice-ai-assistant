"""Tests for the Trust Ledger surface.

The surface has to be as trustworthy as the record behind it: revoking
appends rather than edits, a sealed entry offers no action it cannot
perform, and the search strip never implies an answer the record doesn't
contain.
"""
import pytest

from core import ledger


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "_LEDGER", tmp_path / "action_ledger.jsonl")
    monkeypatch.setattr(ledger, "_KEY", tmp_path / "ledger.key")
    monkeypatch.setattr(ledger, "_fernet", None)
    yield
    monkeypatch.setattr(ledger, "_fernet", None)


def _window(qapp):
    from ui.trust_ledger import TrustLedgerWindow
    return TrustLedgerWindow()


def _rows(w):
    from ui.trust_ledger import _Row
    return [w._rows.itemAt(i).widget() for i in range(w._rows.count())
            if isinstance(w._rows.itemAt(i).widget(), _Row)]


class TestReadingTheRecord:
    def test_entries_appear_newest_first(self, qapp):
        ledger.append("sent", "whatsapp", "Omar", "first")
        ledger.append("sent", "gmail", "mo@x.com", "second")
        w = _window(qapp)
        assert _rows(w)[0]._entry["summary"] == "second"
        w.close()

    def test_the_counters_show_total_and_still_revocable(self, qapp):
        ledger.append("sent", "whatsapp", "Omar", "a")
        w = _window(qapp)
        assert "1 ENTRIES · 30 DAYS" in w._counters.text()
        assert "STILL REVOCABLE" in w._counters.text()
        w.close()

    def test_a_filter_narrows_to_one_category(self, qapp):
        ledger.append("sent", "whatsapp", "Omar", "a sent thing")
        ledger.append("read", "gmail", "mo@x.com", "a read thing")
        w = _window(qapp)
        w._set_filter("read")
        assert [r._entry["summary"] for r in _rows(w)] == ["a read thing"]
        w.close()


class TestRevoking:
    def test_revoking_appends_and_keeps_the_original(self, qapp):
        entry = ledger.append("sent", "whatsapp", "Omar", "the message")
        w = _window(qapp)
        w._revoke(entry)
        lines = ledger._LEDGER.read_bytes().splitlines()
        assert len(lines) == 2                       # appended, not rewritten
        summaries = [r._entry["summary"] for r in _rows(w)]
        assert "the message" in summaries            # original still on screen
        w.close()

    def test_the_revoked_original_is_marked_not_removed(self, qapp):
        entry = ledger.append("sent", "whatsapp", "Omar", "the message")
        w = _window(qapp)
        w._revoke(entry)
        original = next(r._entry for r in _rows(w) if r._entry["id"] == entry["id"])
        assert original["revoked"] is True
        w.close()

    def test_the_surface_never_claims_it_unsent_anything(self, qapp):
        # We cannot reach into WhatsApp; the record must say so.
        entry = ledger.append("sent", "whatsapp", "Omar", "the message")
        w = _window(qapp)
        w._revoke(entry)
        reversal = next(e for e in ledger.entries() if e.get("revokes") == entry["id"])
        assert reversal["reversal"] == "recorded"
        w.close()

    def test_the_action_names_what_it_would_actually_do(self):
        from ui.trust_ledger import _REVOKE_VERB
        assert _REVOKE_VERB["whatsapp"] == "Delete for all"
        assert _REVOKE_VERB["gmail"] == "Unsend"


class TestSearchStrip:
    def test_a_hit_is_reported_as_coming_from_the_ledger(self, qapp):
        ledger.append("sent", "whatsapp", "Omar", "whatsapp to Omar")
        w = _window(qapp)
        w._search.setText("omar")
        assert w._answer.isVisibleTo(w)
        assert "ANSWERED FROM THE LEDGER, NOT THE MODEL" in w._answer.text()
        assert "1 ENTRY MATCH" in w._answer.text()
        w.close()

    def test_a_miss_says_the_record_has_nothing_rather_than_guessing(self, qapp):
        ledger.append("sent", "whatsapp", "Omar", "whatsapp to Omar")
        w = _window(qapp)
        w._search.setText("my landlord")
        assert "NOTHING IN THE RECORD MATCHES" in w._answer.text()
        assert _rows(w) == []
        w.close()

    def test_clearing_the_search_returns_to_the_full_record(self, qapp):
        ledger.append("sent", "whatsapp", "Omar", "whatsapp to Omar")
        w = _window(qapp)
        w._search.setText("nothing matches this")
        w._search.setText("")
        assert not w._answer.isVisibleTo(w)
        assert len(_rows(w)) == 1
        w.close()


class TestTheLawIsOnScreen:
    def test_every_category_has_a_tint(self):
        from ui import tokens
        for category in ledger.CATEGORIES:
            assert category in tokens.LEDGER_TINT


class TestItIsReadableOverTheStage:
    """It opened as a child widget with the frameless flag, which does not
    make a window: nothing painted a background, so the sphere read straight
    through the record. It is a panel over the cockpit now, like the ? map."""

    def _cockpit(self, qapp, tmp_path, monkeypatch):
        import json
        from unittest.mock import MagicMock
        import ui.overlay as overlay_mod
        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(overlay_mod, "_SETTINGS_FILE", path)
        from ui.cockpit import CockpitWindow
        w = CockpitWindow(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        w.set_wake_listener(None)
        w.resize(1280, 760)
        return w

    def test_the_card_paints_a_solid_background(self, qapp):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage
        from ui import tokens
        w = _window(qapp)
        w.resize(900, 800)
        image = QImage(w._card.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        w._card.render(image)
        middle = image.pixelColor(image.width() // 2, image.height() // 2)
        assert middle.alpha() == 255, "the stage shows through the record"
        assert middle.name().lower() == tokens.CK_PANEL.lower()
        w.close()

    def test_it_covers_the_cockpit_and_centres_the_card(self, qapp, tmp_path, monkeypatch):
        cockpit = self._cockpit(qapp, tmp_path, monkeypatch)
        cockpit.open_ledger()
        ledger_window = cockpit._ledger_window
        stage = ledger_window.parentWidget()
        assert stage is cockpit._chrome, "it should cover the stage, not the title bar"
        assert ledger_window.size() == stage.size(), "the backdrop leaves the stage showing"
        card = ledger_window._card
        assert card.width() <= stage.width() - 40 and card.height() <= stage.height() - 40
        centre_gap = abs((card.x() + card.width() // 2) - stage.width() // 2)
        assert centre_gap <= 2, "the card is not centred on the cockpit"
        cockpit.close()

    def test_a_resized_cockpit_keeps_it_covered(self, qapp, tmp_path, monkeypatch):
        cockpit = self._cockpit(qapp, tmp_path, monkeypatch)
        cockpit.open_ledger()
        cockpit.resize(1000, 700)
        for _ in range(10):
            qapp.processEvents()
        assert cockpit._ledger_window.size() == cockpit._chrome.size()
        cockpit.close()

    def test_escape_puts_it_away(self, qapp, tmp_path, monkeypatch):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent, QKeySequence
        cockpit = self._cockpit(qapp, tmp_path, monkeypatch)
        cockpit.open_ledger()
        w = cockpit._ledger_window
        w.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                  Qt.KeyboardModifier.NoModifier))
        assert w.isHidden()
        cockpit.close()


class TestALongRecordScrolls:
    """With a real day's worth of entries the card has to use the height it
    has and scroll the rest — under every filter, and in search."""

    def _full(self, qapp, tmp_path, monkeypatch, n=60):
        cats = ["sent", "changed", "read", "learned"]
        for i in range(n):
            ledger.append(cats[i % 4], "whatsapp", f"target {i}",
                          f"entry number {i}", provenance="CONFIRMED BY VOICE")
        import json
        from unittest.mock import MagicMock
        import ui.overlay as overlay_mod
        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(overlay_mod, "_SETTINGS_FILE", path)
        from ui.cockpit import CockpitWindow
        cockpit = CockpitWindow(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        from PyQt6.QtCore import Qt
        cockpit.set_wake_listener(None)
        cockpit.resize(1280, 760)
        # Laid out but never on Mo's screen: the scroll range is only computed
        # once the widgets have a real geometry.
        cockpit.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        cockpit.show()
        cockpit.open_ledger()
        for _ in range(20):
            qapp.processEvents()
        return cockpit, cockpit._ledger_window

    def _scroll(self, w):
        from PyQt6.QtWidgets import QScrollArea
        return w.findChildren(QScrollArea)[0]

    def test_the_card_fills_the_stage_it_is_given(self, qapp, tmp_path, monkeypatch):
        cockpit, w = self._full(qapp, tmp_path, monkeypatch)
        stage = w.parentWidget()
        assert w._card.height() == min(760, stage.height() - 80)
        assert w._card.width() == min(860, stage.width() - 80)
        cockpit.close()

    def test_the_list_scrolls_under_every_filter(self, qapp, tmp_path, monkeypatch):
        cockpit, w = self._full(qapp, tmp_path, monkeypatch)
        for key in (None, "sent", "changed", "read", "learned"):
            w._set_filter(key)
            for _ in range(10):
                qapp.processEvents()
            bar = self._scroll(w).verticalScrollBar()
            assert bar.maximum() > 0, f"the {key or 'all'} list does not scroll"
            bar.setValue(bar.maximum())
            assert bar.value() == bar.maximum()
        cockpit.close()

    def test_a_small_window_still_fits_the_card_inside_it(self, qapp, tmp_path, monkeypatch):
        cockpit, w = self._full(qapp, tmp_path, monkeypatch)
        cockpit.resize(900, 560)
        for _ in range(20):
            qapp.processEvents()
        stage = w.parentWidget()
        assert w._card.width() <= stage.width() and w._card.height() <= stage.height()
        assert self._scroll(w).verticalScrollBar().maximum() > 0
        cockpit.close()
