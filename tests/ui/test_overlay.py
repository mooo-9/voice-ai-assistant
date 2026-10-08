"""Tests for the Ctrl+Space overlay.

The surface shows ONE exchange at a time — HEARD → DOING → ANSWER, each
phase replacing the last — rather than a scrolling chat thread. These tests
pin the design's hard rules as much as the wiring: state is never signalled
by colour alone, organic loops stop when the window hides, and text
direction follows the language of the exchange.
"""
from unittest.mock import MagicMock


def _make_overlay(qapp):
    """Build a minimal OverlayWindow without a real wake listener or pipeline."""
    import ui.overlay
    voice_in = MagicMock()
    brain = MagicMock()
    brain._offline_mode = False
    voice_out = MagicMock()
    memory = MagicMock()
    w = ui.overlay.OverlayWindow(voice_in, brain, voice_out, memory)
    w.set_wake_listener(None)  # builds the UI
    return w


class TestCompactSurface:
    def test_no_mode_machinery_remains(self, qapp):
        w = _make_overlay(qapp)
        assert not hasattr(w, "cycle_mode")
        assert not hasattr(w, "switch_to_jarvis_hud")
        assert not hasattr(w, "_mode")
        assert not hasattr(w, "_stack")
        w.close()

    def test_no_webengine_in_overlay_module(self):
        import ui.overlay as mod
        assert not hasattr(mod, "HudWebView")
        assert not hasattr(mod, "HudCanvas")

    def test_builds_ui_with_input_and_stage(self, qapp):
        w = _make_overlay(qapp)
        assert w._text_input is not None
        assert w._prompt_box is not None
        assert w._header is not None
        w.close()

    def test_card_is_the_design_width(self, qapp):
        import ui.overlay as mod
        w = _make_overlay(qapp)
        assert w.width() == mod._CARD_W + 2 * mod._SHADOW_PAD
        w.close()

    def test_typing_is_hidden_behind_the_key_icon(self, qapp):
        # isVisibleTo(): a child of an unshown window is never isVisible().
        w = _make_overlay(qapp)
        assert not w._typed_row.isVisibleTo(w)
        w._set_typed_mode(True)
        assert w._typed_row.isVisibleTo(w)
        assert not w._voice_row.isVisibleTo(w)
        w.close()


class TestExchangePhases:
    def test_listening_shows_the_prompt(self, qapp):
        w = _make_overlay(qapp)
        w.on_state_update("listening", "", "")
        assert w._current_state == "listening"
        assert w._phase == "prompt"
        assert w._state_label.text() == "LISTENING"
        w.close()

    def test_heard_holds_the_stage_then_shrinks(self, qapp):
        w = _make_overlay(qapp)
        w.on_state_update("processing", "what time is it", "")
        assert w._heard == "what time is it"
        assert w._heard_big.text() == "what time is it"
        assert w._phase == "hearing"      # your words, full size
        w._heard_settles()
        assert w._phase == "doing"        # then a mono caption
        assert w._heard_small.text() == "what time is it"
        w.close()

    def test_an_answer_beats_the_heard_beat(self, qapp):
        # If the answer lands inside the 450ms window, the settle is a no-op.
        w = _make_overlay(qapp)
        w.on_state_update("processing", "what time is it", "")
        w.on_state_update("speaking", "what time is it", "Noon.")
        w._heard_settles()
        assert w._phase == "answering"
        w.close()

    def test_pipeline_stage_words_are_a_caption_not_the_heard_line(self, qapp):
        # "Transcribing..." is the pipeline talking, not the user.
        w = _make_overlay(qapp)
        w.on_state_update("processing", "Transcribing...", "")
        assert w._heard == ""
        assert w._doing_caption.text() == "transcribing..."
        w.close()

    def test_speaking_shows_the_answer(self, qapp):
        w = _make_overlay(qapp)
        w.on_state_update("processing", "what time is it", "")
        w._heard_settles()
        w.on_state_update("speaking", "what time is it", "It is noon.")
        assert w._answer == "It is noon."
        assert w._answer_label.text() == "It is noon."
        assert w._phase == "answering"
        w.close()

    def test_one_exchange_replaces_the_previous(self, qapp):
        w = _make_overlay(qapp)
        w.on_state_update("processing", "first question", "")
        w.on_state_update("speaking", "first question", "first answer")
        w.on_state_update("listening", "", "")
        assert w._heard == ""
        assert w._answer == ""
        w.close()

    def test_error_shows_message(self, qapp):
        w = _make_overlay(qapp)
        w.on_error("Nothing heard")
        assert w._current_state == "error"
        assert w._phase == "error"
        assert "NOTHING HEARD" in w._error_label.text()
        w.close()


def _png(color="#3a7", size=48) -> bytes:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class TestStagedAction:
    def test_the_recipients_photo_sits_beside_the_message(self, qapp):
        """So Mo can see it is going to the right person before he says yes."""
        from core import staging
        staging.reset()
        w = _make_overlay(qapp)
        staging.stage(medium="whatsapp", target="Yasmeen Adam", body="on my way",
                      photo=_png())
        w._refresh_staged()
        assert w._staged_photo.isVisibleTo(w)
        assert not w._staged_photo.pixmap().isNull()
        staging.stage(medium="gmail", target="a@b.c", body="hi")
        w._refresh_staged()
        assert not w._staged_photo.isVisibleTo(w)
        staging.reset()
        w.close()

    def test_a_photo_that_wont_load_just_leaves_the_photo_out(self, qapp):
        from core import staging
        staging.reset()
        w = _make_overlay(qapp)
        staging.stage(medium="whatsapp", target="Omar", body="hi", photo=b"not a png")
        w._refresh_staged()
        assert w._staged_card.isVisibleTo(w)
        assert not w._staged_photo.isVisibleTo(w)
        staging.reset()
        w.close()

    def test_the_card_appears_when_something_is_armed(self, qapp):
        from core import staging
        staging.reset()
        w = _make_overlay(qapp)
        assert not w._staged_card.isVisibleTo(w)
        staging.stage(medium="whatsapp", target="Omar Adel", body="the meeting moved to tomorrow")
        w._refresh_staged()
        assert w._staged_card.isVisibleTo(w)
        assert w._staged_kicker.text() == "WHATSAPP →"
        assert w._staged_target.text() == "Omar Adel"
        assert w._staged_body.text() == "the meeting moved to tomorrow"
        staging.reset()
        w.close()

    def test_a_calendar_action_is_never_labelled_send(self, qapp):
        from core import staging
        staging.reset()
        w = _make_overlay(qapp)
        staging.stage(medium="calendar", target="Standup", body="Delete Standup")
        w._refresh_staged()
        assert w._staged_send.text() == "Delete"
        staging.reset()
        w.close()

    def test_an_armed_action_makes_the_horizon_glow(self, qapp):
        from core import staging
        staging.reset()
        w = _make_overlay(qapp)
        w._paint_state("idle")
        assert w._header._live is False
        staging.stage(medium="whatsapp", target="Omar", body="hi")
        w._refresh_staged()
        assert w._header._live is True
        staging.reset()
        w.close()

    def test_cancel_drops_the_card(self, qapp):
        from core import staging
        staging.reset()
        w = _make_overlay(qapp)
        staging.stage(medium="whatsapp", target="Omar", body="hi")
        w._refresh_staged()
        w._cancel_staged()
        assert staging.current() is None
        assert not w._staged_card.isVisibleTo(w)
        staging.reset()
        w.close()

    def test_receipts_show_what_left_the_machine(self, qapp):
        from core import staging
        staging.reset()
        w = _make_overlay(qapp)
        assert not w._receipts_box.isVisibleTo(w)
        staging.stage(medium="whatsapp", target="Omar", body="hi")
        staging.resolve("sent", "whatsapp → Omar · sent")
        w._refresh_staged()
        assert w._receipts_box.isVisibleTo(w)
        assert "whatsapp → Omar · sent" in w._receipt_rows[0].text()
        staging.reset()
        w.close()

    def test_a_new_exchange_never_wipes_an_armed_action(self, qapp):
        # The trust rule: starting to listen again must not disarm a send.
        from core import staging
        staging.reset()
        w = _make_overlay(qapp)
        staging.stage(medium="whatsapp", target="Omar", body="hi")
        w._refresh_staged()
        w._reset_ui()
        w.on_state_update("listening", "", "")
        assert staging.current() is not None
        assert w._staged_card.isVisibleTo(w)
        staging.reset()
        w.close()


class TestStepCaption:
    def test_the_caption_names_the_tool_at_work(self, qapp):
        from core import progress
        progress.reset()
        w = _make_overlay(qapp)
        w.on_state_update("processing", "what's on today", "")
        progress.step_started("list_events")
        w._refresh_progress()
        assert w._doing_caption.text() == "list events…"
        progress.reset()
        w.close()

    def test_the_caption_is_tinted_by_the_skill(self, qapp):
        from core import progress
        from ui import tokens
        progress.reset()
        w = _make_overlay(qapp)
        w.on_state_update("processing", "tell omar", "")
        progress.step_started("prepare_whatsapp_message")
        w._refresh_progress()
        assert tokens.SKILL_TINT["whatsapp"] in w._doing_caption.styleSheet()
        progress.reset()
        w.close()

    def test_it_stays_quiet_once_the_answer_is_speaking(self, qapp):
        from core import progress
        progress.reset()
        w = _make_overlay(qapp)
        w.on_state_update("speaking", "x", "the answer")
        progress.step_started("list_events")
        w._refresh_progress()
        assert w._phase == "answering"
        progress.reset()
        w.close()


class TestDesignRules:
    def test_state_colors_are_distinct(self):
        from ui import theme
        colors = {theme.STATE_COLORS[s] for s in
                  ("listening", "processing", "speaking", "error")}
        assert len(colors) == 4

    def test_every_state_carries_a_text_label_not_just_a_hue(self, qapp):
        w = _make_overlay(qapp)
        seen = set()
        for state in ("listening", "processing", "speaking"):
            w.on_state_update(state, "x", "y")
            assert w._state_label.text().strip()
            seen.add(w._state_label.text())
        assert len(seen) == 3
        w.close()

    def test_organic_loops_pause_when_hidden(self, qapp, monkeypatch):
        # The loops only run when the OS allows animation; CI runners turn it
        # off, which is the reduced-motion path, not the one under test.
        monkeypatch.setattr("ui.overlay.reduced_motion", lambda: False)
        w = _make_overlay(qapp)
        w._show_anchored()
        assert w._anim_timer.isActive()
        w._hide()
        assert not w._anim_timer.isActive()
        assert not w._status_poll_timer.isActive()
        w.close()

    def test_only_mic_errors_open_typing(self, qapp):
        w = _make_overlay(qapp)
        w.on_error("WhatsApp bridge timed out")
        assert not w._typed_row.isVisibleTo(w)
        w.on_error("Nothing heard — please try again")
        assert w._typed_row.isVisibleTo(w)
        w.close()

    def test_glow_means_live(self, qapp):
        # The horizon only glows while an exchange is in flight, never at rest.
        w = _make_overlay(qapp)
        w.on_state_update("listening", "", "")
        assert w._header._live is True
        w._paint_state("idle")
        assert w._header._live is False
        w.close()

    def test_card_style_has_theme_variants(self):
        from ui import theme
        dark = theme.card_style("dark")
        oled = theme.card_style("oled")
        assert dark != oled
        assert "border-radius" in dark


class TestReducedMotion:
    def test_the_flag_is_read_from_the_os(self, qapp):
        import ui.overlay as mod
        assert isinstance(mod.reduced_motion(), bool)

    def test_the_entrance_arrives_in_one_paint(self, qapp, monkeypatch):
        import ui.overlay as mod
        from PyQt6.QtCore import QPoint
        monkeypatch.setattr(mod, "reduced_motion", lambda: True)
        w = _make_overlay(qapp)
        w._enter(QPoint(120, 240))
        assert w.windowOpacity() == 1.0     # no fade left running
        assert w.pos() == QPoint(120, 240)  # already there, not sliding
        w.close()

    def test_nothing_breathes_when_motion_is_reduced(self, qapp, monkeypatch):
        import ui.overlay as mod
        monkeypatch.setattr(mod, "reduced_motion", lambda: True)
        w = _make_overlay(qapp)
        w._show_anchored()
        assert not w._anim_timer.isActive()
        assert w._status_poll_timer.isActive()   # state still updates
        w._hide()
        w.close()

    def test_motion_runs_normally_when_the_flag_is_off(self, qapp, monkeypatch):
        import ui.overlay as mod
        monkeypatch.setattr(mod, "reduced_motion", lambda: False)
        w = _make_overlay(qapp)
        w._show_anchored()
        assert w._anim_timer.isActive()
        w._hide()
        w.close()
