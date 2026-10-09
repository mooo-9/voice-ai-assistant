"""Tests for the Cockpit — the design's primary full-screen surface.

These never call open(): that would put a real full-screen window on the
user's desktop. What matters here is the contract around the orb — that
Chromium stays unbuilt until the cockpit is actually opened, and that state
maps onto the orb's own vocabulary.
"""
import json
import time
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    """The Cockpit remembers its window in settings; keep that off the real
    data/settings.json, which closing a window in a test would overwrite."""
    import ui.overlay as overlay_mod
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(overlay_mod, "_SETTINGS_FILE", path)
    return path


def _seed_schedules(jobs):
    """The scheduler's jobs, in the file conftest points at a temp dir."""
    from core import scheduler
    scheduler._SCHEDULES_FILE.write_text(json.dumps(jobs), encoding="utf-8")


def _make_cockpit(qapp):
    from ui.cockpit import CockpitWindow
    w = CockpitWindow(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    w.set_wake_listener(None)
    return w


class TestLazyWebEngine:
    def test_no_chromium_until_the_cockpit_is_opened(self, qapp):
        # The hard rule: QWebEngine is lazy and single-instance.
        w = _make_cockpit(qapp)
        assert w._orb is None
        assert w._orb_ready is False
        w.close()

    def test_pushing_state_before_the_orb_exists_is_harmless(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("listening", "", "")     # must not raise
        assert w._current_state == "listening"
        w.close()


class TestStateMapping:
    @pytest.mark.parametrize("pipeline_state,orb_state", [
        ("idle", "idle"),
        ("listening", "listening"),
        ("processing", "thinking"),   # the pipeline's word → the design's
        ("speaking", "speaking"),
        ("error", "error"),
    ])
    def test_pipeline_states_map_onto_the_orbs_vocabulary(self, pipeline_state, orb_state):
        from ui.cockpit import _ORB_STATE
        assert _ORB_STATE[pipeline_state] == orb_state

    def test_every_orb_state_has_a_colour_in_the_cockpit_palette(self):
        from ui import tokens
        from ui.cockpit import _ORB_STATE
        for orb_state in set(_ORB_STATE.values()):
            assert orb_state in tokens.CK_STATE

    def test_the_state_colours_are_the_spheres_own(self):
        # The chip and voice bar used the gold/orange/ash palette while the
        # sphere is blue; Mo wanted them to match. Read the sphere's table as
        # text so the two cannot drift apart again.
        import re
        from pathlib import Path
        from ui import tokens
        page = Path("ui/assets/cockpit_orb.html").read_text(encoding="utf-8")
        table = re.search(r"const COLORS\s*=\s*\{(.*?)\}", page, re.S).group(1)
        sphere = {name: "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b))
                  for name, r, g, b in re.findall(r"(\w+):\s*\[\s*(\d+),\s*(\d+),\s*(\d+)\]", table)}
        assert sphere, "no colours parsed out of the sphere page"
        assert {k: v.upper() for k, v in tokens.CK_ORB.items()} == sphere

    @pytest.mark.parametrize("state,orb_state", [
        ("idle", "idle"), ("listening", "listening"), ("processing", "thinking"), ("speaking", "speaking"),
    ])
    def test_the_state_chip_wears_the_spheres_colour(self, qapp, state, orb_state):
        from ui import tokens
        w = _make_cockpit(qapp)
        w.on_state_update(state, "", "")
        assert w._state_chip._mark.colour() == tokens.CK_ORB[orb_state]
        assert f"color: {tokens.CK_ORB[orb_state]};" in w._state_label.styleSheet()
        w.close()

    def test_state_carries_a_label_not_just_a_hue(self, qapp):
        w = _make_cockpit(qapp)
        seen = set()
        for state in ("listening", "processing", "speaking", "error"):
            w.on_state_update(state, "x", "y") if state != "error" else w.on_error("x")
            assert w._state_label.text().strip()
            seen.add(w._state_label.text())
        assert len(seen) == 4
        w.close()


class TestReadouts:
    def test_readouts_come_from_the_local_cache_never_the_network(self, tmp_path, monkeypatch):
        import ui.cockpit as mod
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"cards": {"calendar": {
            "text": "Standup at 10:00\nthen review",
            "date": date.today().isoformat()}}}), encoding="utf-8")
        monkeypatch.setattr(mod, "_CACHE", cache)
        assert mod._cached("calendar") == "Standup at 10:00"

    def test_yesterdays_card_is_not_read_as_today(self, tmp_path, monkeypatch):
        # The rails said "No events found for today (Monday, Sep 14)" two days
        # running: a card was written once and never dated, so nothing could
        # tell it was stale.
        import ui.cockpit as mod
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"cards": {"calendar": {
            "text": "No events found for today (Monday, Sep 14)",
            "date": (date.today() - timedelta(days=1)).isoformat()}}}), encoding="utf-8")
        monkeypatch.setattr(mod, "_CACHE", cache)
        assert mod._cached("calendar") == "—"
        assert mod._calendar_events() is None

    def test_a_card_from_before_dates_were_written_is_stale(self, tmp_path, monkeypatch):
        import ui.cockpit as mod
        cache = tmp_path / "cache.json"
        cache.write_text(
            '{"cards": {"calendar": {"text": "No events found", "updated": "05:53"}}}',
            encoding="utf-8")
        monkeypatch.setattr(mod, "_CACHE", cache)
        assert mod._cached("calendar") == "—"

    def test_what_the_cockpit_caches_it_can_read_back(self, tmp_path, monkeypatch):
        import ui.cockpit as mod
        cache = tmp_path / "cache.json"
        cache.write_text('{"cards": {"mail": {"text": "keep me"}}}', encoding="utf-8")
        monkeypatch.setattr(mod, "_CACHE", cache)
        mod._cache_card("calendar", "Lecture at 2")
        assert mod._cached("calendar") == "Lecture at 2"
        written = json.loads(cache.read_text(encoding="utf-8"))
        assert written["cards"]["mail"]["text"] == "keep me"   # other cards kept
        assert written["cards"]["calendar"]["date"] == date.today().isoformat()

    def test_opening_the_cockpit_refreshes_the_day_in_the_background(self, qapp, tmp_path,
                                                                     monkeypatch):
        import ui.cockpit as mod
        import ui.command_center as cc
        cache = tmp_path / "cache.json"
        cache.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mod, "_CACHE", cache)
        monkeypatch.setattr(cc, "_fetch_calendar", lambda: "Lecture at 2")
        monkeypatch.setattr(cc, "_fetch_tasks", lambda: "Finish the slides")
        w = _make_cockpit(qapp)
        w._refresh_day().join(5)
        assert mod._cached("calendar") == "Lecture at 2"
        assert mod._cached("tasks") == "Finish the slides"
        w.close()

    def test_a_missing_cache_reads_as_an_em_dash(self, tmp_path, monkeypatch):
        import ui.cockpit as mod
        monkeypatch.setattr(mod, "_CACHE", tmp_path / "nope.json")
        assert mod._cached("calendar") == "—"

    def test_the_clock_is_twelve_hour(self, qapp):
        w = _make_cockpit(qapp)
        w._tick_clock()
        assert w._status.time.text().endswith(("AM", "PM"))
        assert not w._status.time.text().startswith("0")
        w.close()


class TestStatusCard:
    """The reel's small card at the top of the left rail: the time, what El
    Fager has done today, and what is next."""

    def test_it_heads_the_left_rail_in_place_of_the_big_readouts(self, qapp):
        from PyQt6.QtWidgets import QLabel
        w = _make_cockpit(qapp)
        rail = w._status.parentWidget().layout()
        assert rail.indexOf(w._status) == 0
        kickers = [l.text() for l in w._status.parentWidget().findChildren(QLabel)
                   if l.isVisibleTo(w._status.parentWidget())]
        assert "TIME" not in kickers
        w.close()

    def test_done_today_counts_only_todays_actions(self, qapp, monkeypatch):
        from datetime import datetime, timedelta
        from core import ledger
        now = datetime.now()
        today, yesterday = now.isoformat(timespec="seconds"),             (now - timedelta(days=1)).isoformat(timespec="seconds")
        monkeypatch.setattr(ledger, "entries", lambda limit=50, category=None: [
            {"ts": today, "category": "sent", "revoked": False},
            {"ts": today, "category": "logged", "revoked": False},
            {"ts": today, "category": "sent", "revoked": True},      # walked back
            {"ts": today, "category": "revoked", "revoked": False},  # the undo itself
            {"ts": yesterday, "category": "sent", "revoked": False},
        ])
        w = _make_cockpit(qapp)
        w._refresh_readouts()
        assert w._status.done.text() == "2"
        w.close()

    def test_an_unreadable_ledger_reads_as_a_dash(self, qapp, monkeypatch):
        from core import ledger
        def broken(**_):
            raise OSError("no key")
        monkeypatch.setattr(ledger, "entries", broken)
        w = _make_cockpit(qapp)
        w._refresh_readouts()
        assert w._status.done.text() == "—"
        w.close()


class TestNextAndToday:
    """NEXT and the TODAY rail both read the cached calendar card, which is
    what tools/calendar_tool.list_events wrote. An empty day showed "No events
    found for today (…)" in both, and a busy one showed the card's heading as
    NEXT rather than an event."""

    DAY = ("📅 Events for today (Thursday, Sep 17):\n"
           "- 12:00 AM — Early standup (30 min)\n"
           "- All day — Mo's graduation prep\n"
           "- 12:00 AM — Midnight snack (15 min)")

    def _cockpit_with(self, qapp, tmp_path, monkeypatch, text):
        import ui.cockpit as mod
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"cards": {"calendar": {
            "text": text, "date": date.today().isoformat()}}}), encoding="utf-8")
        monkeypatch.setattr(mod, "_CACHE", cache)
        w = _make_cockpit(qapp)
        w._refresh_readouts()
        return w

    def _rail(self, w):
        return [w._today_list.itemAt(i).widget().text()
                for i in range(w._today_list.count())]

    def test_an_empty_day_says_so_once_in_its_own_words(self, qapp, tmp_path, monkeypatch):
        w = self._cockpit_with(qapp, tmp_path, monkeypatch,
                               "No events found for today (Thursday, Sep 17)")
        assert w._status.next.text() == "Free all day."
        assert self._rail(w) == ["Nothing on today."]
        w.close()

    def test_the_rail_lists_events_not_the_cards_heading(self, qapp, tmp_path, monkeypatch):
        w = self._cockpit_with(qapp, tmp_path, monkeypatch, self.DAY)
        assert self._rail(w) == ["12:00 AM — Early standup (30 min)",
                                 "All day — Mo's graduation prep",
                                 "12:00 AM — Midnight snack (15 min)"]
        assert w._status.next.text() == "Nothing else today."     # all started
        w.close()

    def test_a_failed_fetch_is_not_an_empty_day(self, qapp, tmp_path, monkeypatch):
        w = self._cockpit_with(qapp, tmp_path, monkeypatch,
                               "[Calendar auth failed — check credentials.json]")
        assert w._status.next.text() == "—"
        assert self._rail(w) == ["—"]
        w.close()

    def test_next_is_the_first_timed_event_still_to_come(self):
        from datetime import datetime
        from ui.cockpit import _next_event
        events = ["9:00 AM — Gym (1 hour)", "All day — Holiday",
                  "2:30 PM — Lecture (2 hours)", "7:00 PM — Dinner (1 hour)"]
        at_one = datetime(2026, 9, 17, 13, 0)
        assert _next_event(events, at_one) == "2:30 PM — Lecture (2 hours)"
        assert _next_event(events, datetime(2026, 9, 17, 20, 0)) == "Nothing else today."
        assert _next_event([], at_one) == "Free all day."
        assert _next_event(None, at_one) == "—"


class TestMonthArrows:
    def _title(self, w):
        return w._calendar._title.text()

    def test_the_arrows_step_through_the_months(self, qapp):
        from datetime import datetime
        w = _make_cockpit(qapp)
        now = datetime.now()
        this = now.strftime("%B %Y").upper()
        w._calendar._next.click()
        nxt = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
        assert self._title(w) == nxt.strftime("%B %Y").upper()
        w._calendar._prev.click()
        w._calendar._prev.click()
        prev = datetime(now.year - (now.month == 1), (now.month - 2) % 12 + 1, 1)
        assert self._title(w) == prev.strftime("%B %Y").upper()
        w._calendar.refresh()
        assert self._title(w) == this
        w.close()

    def test_today_is_lit_only_in_its_own_month(self, qapp):
        from ui import tokens
        w = _make_cockpit(qapp)
        def lit():
            return [c for c in w._calendar.findChildren(type(w._calendar._title))
                    if tokens.EMBER in c.styleSheet()]
        assert len(lit()) == 1
        w._calendar._next.click()
        assert lit() == []
        w.close()


class TestStagedAction:
    def test_an_armed_action_shows_on_the_stage(self, qapp):
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        assert not w._staged.isVisibleTo(w)
        staging.stage(medium="whatsapp", target="Omar", body="the meeting moved")
        w._refresh_staged()
        assert w._staged.isVisibleTo(w)
        assert "WHATSAPP → Omar" in w._staged.text()
        staging.reset()
        w.close()

    def test_the_recipients_photo_sits_beside_the_message(self, qapp):
        """So Mo can see it is going to the right person before he says yes."""
        from core import staging
        from tests.ui.test_overlay import _png
        staging.reset()
        w = _make_cockpit(qapp)
        staging.stage(medium="whatsapp", target="Yasmeen Adam", body="on my way",
                      photo=_png())
        w._refresh_staged()
        assert w._staged_photo.isVisibleTo(w)
        assert not w._staged_photo.pixmap().isNull()
        assert "Yasmeen Adam" in w._staged.text() and "on my way" in w._staged.text()
        staging.stage(medium="gmail", target="mo@x.com", body="hi")
        w._refresh_staged()
        assert w._staged.isVisibleTo(w)
        assert not w._staged_photo.isVisibleTo(w)
        staging.reset()
        w.close()

    def test_it_clears_when_the_action_resolves(self, qapp):
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        staging.stage(medium="gmail", target="mo@x.com", body="hi")
        w._refresh_staged()
        staging.resolve("sent")
        w._refresh_staged()
        assert not w._staged.isVisibleTo(w)
        staging.reset()
        w.close()


    def test_enter_sends_off_the_gui_thread_and_only_once(self, qapp):
        """A Gmail send waits on the network; on the GUI thread it froze the
        window, and a second Enter while it waited could send twice."""
        import threading
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        release, sends = threading.Event(), []

        def slow_send():
            sends.append(1)
            release.wait(5)
            return "Sent"

        staging.stage(medium="gmail", target="mo@x.com", body="hi",
                      confirm=slow_send)
        enter = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return,
                          Qt.KeyboardModifier.NoModifier)
        w.keyPressEvent(enter)          # returns while the send still waits
        w.keyPressEvent(enter)
        release.set()
        for _ in range(50):
            if not w._confirming.is_set():
                break
            threading.Event().wait(0.02)
        assert sends == [1]
        staging.reset()
        w.close()


class TestAttention:
    """ambient (orb alone) → ready (readouts up) → exchange (words own it)."""

    def test_an_exchange_dims_the_readouts(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("processing", "what's on my plate", "")
        assert w._attention == "exchange"
        assert w._status.graphicsEffect().opacity() == pytest.approx(0.12)
        w.close()

    def test_the_readouts_come_back_when_the_turn_ends(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("processing", "x", "")
        w.on_pipeline_done()
        assert w._attention == "ready"
        assert w._status.graphicsEffect().opacity() == pytest.approx(1.0)
        w.close()

    def test_silence_falls_to_ambient_but_keeps_the_transcript(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("speaking", "", "Three things today.")
        w.on_pipeline_done()
        w._go_ambient()                       # what the timer would do
        assert w._attention == "ambient"
        assert w._status.graphicsEffect().opacity() == 0.0
        from PyQt6.QtWidgets import QLabel     # kept until midnight
        assert "Three things today." in [
            label.text() for label in w._reading_box.findChildren(QLabel)]
        w.close()

    def test_a_turn_in_flight_never_falls_to_ambient(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("listening", "", "")
        w._go_ambient()
        assert w._attention == "exchange"
        w.close()

    def test_anything_you_do_wakes_it_back_up(self, qapp):
        w = _make_cockpit(qapp)
        w._go_ambient()
        w._wake_attention()
        assert w._attention == "ready"
        assert w._ambient_timer.isActive()
        w.close()

    @pytest.mark.parametrize("setting,seconds", [
        ("30s", 30), ("60s", 60), ("2m", 120), (45, 45), ("90", 90),
        ("nonsense", 60), (None, 60),
    ])
    def test_the_delay_comes_from_settings(self, qapp, tmp_path, monkeypatch,
                                           setting, seconds):
        import json
        import ui.overlay as overlay_mod
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({} if setting is None
                                   else {"ambient_delay": setting}),
                        encoding="utf-8")
        monkeypatch.setattr(overlay_mod, "_SETTINGS_FILE", path)
        w = _make_cockpit(qapp)
        assert w._ambient_delay_ms() == seconds * 1000
        w.close()


class TestKeyboardMap:
    def test_the_map_lists_only_keys_the_cockpit_honours(self, qapp):
        from ui.cockpit import _KEYS
        keys = {key for key, _ in _KEYS}
        assert keys == {"SPACE", "ENTER", "ESC", "L", "?"}

    def test_it_toggles(self, qapp):
        w = _make_cockpit(qapp)
        assert w._keymap.isHidden()
        w.toggle_keymap()
        assert not w._keymap.isHidden()
        w.toggle_keymap()
        assert w._keymap.isHidden()
        w.close()

    def test_escape_closes_the_map_before_anything_else(self, qapp):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent
        w = _make_cockpit(qapp)
        w.toggle_keymap()
        w.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                  Qt.KeyboardModifier.NoModifier))
        assert w._keymap.isHidden()
        assert w.isHidden()          # never opened, so still hidden
        w.close()

    def test_escape_cancels_a_staged_action_before_closing(self, qapp):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        staging.stage(medium="whatsapp", target="Omar", body="hi")
        w.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                  Qt.KeyboardModifier.NoModifier))
        assert staging.current() is None
        staging.reset()
        w.close()


class TestReceiptLedger:
    def test_a_send_writes_one_green_line(self, qapp):
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        staging.stage(medium="whatsapp", target="Omar", body="hi")
        staging.resolve("sent", "whatsapp → Omar · sent")
        w._refresh_receipts()
        assert w._receipts_layout.count() == 1
        assert "Omar" in w._receipts_layout.itemAt(0).widget().text()
        staging.reset()
        w.close()

    def test_it_keeps_three_at_most(self, qapp):
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        for i in range(5):
            staging.stage(medium="gmail", target=f"person{i}", body="hi")
            staging.resolve("sent", f"gmail → person{i} · sent")
        w._refresh_receipts()
        assert w._receipts_layout.count() == 3
        staging.reset()
        w.close()

    def test_ambient_takes_the_receipts_too(self, qapp):
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        staging.stage(medium="gmail", target="a@b.c", body="hi")
        staging.resolve("sent", "gmail → a@b.c · sent")
        w._refresh_receipts()
        w._go_ambient()
        assert w._receipts_box.isHidden()
        staging.reset()
        w.close()


class TestDataMoments:
    def _turn(self, tool):
        from core import progress
        progress.begin_turn("show me")
        progress.step_finished(progress.step_started(tool), True)

    def test_a_mail_turn_materialises_mail_rows(self, qapp, tmp_path, monkeypatch):
        import ui.cockpit as mod
        cache = tmp_path / "cache.json"
        cache.write_text('{"cards": {"mail": {"text": "Stripe — payout failed"}}}',
                         encoding="utf-8")
        monkeypatch.setattr(mod, "_CACHE", cache)
        w = _make_cockpit(qapp)
        self._turn("list_emails")
        w._show_data_moment()
        assert w._moment_layout.count() > 0
        w.close()

    def test_a_turn_that_touched_nothing_shows_nothing(self, qapp):
        from core import progress
        w = _make_cockpit(qapp)
        progress.begin_turn("what's the weather")
        progress.step_finished(progress.step_started("get_weather"), True)
        w._show_data_moment()
        assert w._moment.isHidden()
        w.close()

    def test_the_next_turn_clears_it(self, qapp, tmp_path, monkeypatch):
        import ui.cockpit as mod
        cache = tmp_path / "cache.json"
        cache.write_text('{"cards": {"mail": {"text": "Stripe — payout failed"}}}',
                         encoding="utf-8")
        monkeypatch.setattr(mod, "_CACHE", cache)
        w = _make_cockpit(qapp)
        self._turn("list_emails")
        w._show_data_moment()
        w.on_state_update("listening", "", "")
        assert w._moment_layout.count() == 0
        w.close()


class TestAutomationRows:
    """The AUTOMATIONS panel lists what El Fager runs without being asked —
    the scheduler's jobs and any skill put on a timer — never the skills that
    only run on request."""

    def _now(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 9, 15, 20, 55, tzinfo=ZoneInfo("Africa/Cairo"))

    def _jobs(self):
        return [
            {"id": "morning", "name": "Morning Briefing", "enabled": True,
             "trigger": {"type": "cron", "hour": 7, "minute": 30}},
            {"id": "evening", "name": "Evening Wind-Down", "enabled": True,
             "trigger": {"type": "cron", "hour": 21, "minute": 30}},
            {"id": "weekly", "name": "Weekly Report", "enabled": True,
             "trigger": {"type": "cron", "day_of_week": "fri", "hour": 18, "minute": 0}},
        ]

    def _rows(self, schedules=(), history=(), tasks=(), skills=(), hunting=False):
        from ui.cockpit import _automation_rows
        return _automation_rows(list(schedules), list(history), list(tasks),
                                list(skills), self._now(), hunting=hunting)

    def _scheduled(self, *a, **kw):
        return [r for r in self._rows(*a, **kw) if r["kind"] == "scheduled"]

    def test_scheduled_jobs_come_soonest_first_with_their_next_run(self):
        rows = self._scheduled(self._jobs())
        assert [(r["name"], r["when"]) for r in rows] == [
            ("Evening Wind-Down", "9:30 PM"),
            ("Morning Briefing", "7:30 AM"),
            ("Weekly Report", "FRI 6 PM"),
        ]

    def test_the_detail_says_how_often_and_when_it_last_ran(self):
        history = [
            {"job_id": "morning", "fired_at": "2026-09-07T07:30:00", "status": "ok"},
            {"job_id": "morning", "fired_at": "2026-09-08T07:31:38", "status": "ok"},
        ]
        rows = {r["name"]: r for r in self._scheduled(self._jobs(), history)}
        assert rows["Morning Briefing"]["detail"] == "Daily · last ran Sep 8"
        assert rows["Morning Briefing"]["state"] == "ok"
        assert rows["Weekly Report"]["detail"] == "Weekly · never ran"

    def test_a_failed_last_run_says_so(self):
        history = [{"job_id": "evening", "fired_at": "2026-09-14T21:30:00", "status": "error"}]
        rows = {r["name"]: r for r in self._scheduled(self._jobs(), history)}
        assert rows["Evening Wind-Down"]["state"] == "failed"
        assert rows["Evening Wind-Down"]["detail"] == "Daily · failed Sep 14"

    def test_a_paused_job_goes_last_and_says_paused(self):
        jobs = self._jobs()
        jobs[1]["enabled"] = False
        rows = self._scheduled(jobs)
        assert rows[-1]["name"] == "Evening Wind-Down"
        assert rows[-1]["when"] == "PAUSED"
        assert rows[-1]["state"] == "paused"

    def test_skills_that_only_run_on_request_are_not_listed(self):
        skills = [{"name": "morning routine", "scheduled_task_id": None}]
        assert self._scheduled(skills=skills) == []

    def test_a_scheduled_skill_is_listed_by_its_timer(self):
        skills = [{"name": "gym mode", "scheduled_task_id": "t1"}]
        tasks = [{"id": "t1", "status": "pending", "recurring_hours": 24,
                  "run_at": "2026-09-16T06:00:00", "completed_at": "2026-09-15T06:00:04"}]
        assert self._scheduled(tasks=tasks, skills=skills) == [{
            "name": "Gym mode", "when": "6 AM", "detail": "Daily · last ran today",
            "state": "ok", "run": ("skill", "gym mode"), "kind": "scheduled"}]

    def test_a_job_row_knows_which_job_to_run(self):
        rows = {r["name"]: r for r in self._scheduled(self._jobs())}
        assert rows["Morning Briefing"]["run"] == ("job", "morning")

    def test_one_off_tasks_that_are_done_are_not_automations(self):
        tasks = [{"id": "x", "description": "say hello", "status": "done",
                  "recurring_hours": 0.0, "run_at": None}]
        assert self._scheduled(tasks=tasks) == []

    def test_the_watching_checks_come_after_what_is_scheduled(self):
        import core.proactive as pa
        rows = self._rows(self._jobs())
        assert [r["kind"] for r in rows[:3]] == ["scheduled"] * 3
        watching = [r for r in rows if r["kind"] == "watching"]
        assert len(watching) == len(pa.WATCHES)
        first = watching[0]
        assert first["state"] == "watching" and first["run"] is None
        assert first["name"] == pa.WATCHES[0]["name"]
        assert first["when"] == pa.WATCHES[0]["when"]
        assert first["detail"] == pa.WATCHES[0]["detail"]

    def _hunt(self, **kw):
        [hunt] = [r for r in self._rows(**kw) if r["kind"] == "on_request"]
        return hunt

    def test_the_job_hunt_waits_for_mo_to_press_run(self):
        hunt = self._hunt(schedules=self._jobs())
        assert hunt["name"] == "Job hunt" and hunt["run"] == ("job_hunt", "job_hunt")
        assert hunt["when"] == "" and "tonight at 2 AM" in hunt["detail"]

    def test_armed_it_shows_tonight_and_offers_cancel_not_a_second_row(self):
        from tools.career_tool import HUNT_JOB_ID
        armed = {"id": HUNT_JOB_ID, "name": "Job hunt", "enabled": True,
                 "trigger": {"type": "date", "run_date": "2026-09-16T02:00:00"},
                 "action": {"type": "tool", "tool": "run_job_hunt", "args": {}}}
        rows = self._rows(schedules=self._jobs() + [armed])
        assert [r["name"] for r in rows if r["kind"] == "scheduled"].count("Job hunt") == 0
        hunt = self._hunt(schedules=[armed])
        assert hunt["when"] == "2 AM" and hunt["action"] == "CANCEL"
        # A night El Fager slept through (now is Sep 15 noon) reads as not armed.
        armed["trigger"]["run_date"] = "2026-09-14T02:00:00"
        assert self._hunt(schedules=[armed])["when"] == ""

    def test_while_it_runs_there_is_nothing_to_press(self):
        hunt = self._hunt(schedules=[], hunting=True)
        assert hunt["when"] == "RUNNING" and hunt["run"] is None

    def test_run_arms_tonight_and_says_why_when_it_cannot(self, qapp, monkeypatch):
        from tools import career_tool
        import core.notifier as notifier
        sent, pressed = [], []
        monkeypatch.setattr(career_tool, "hunt_tonight",
                            lambda: pressed.append(1) or "Not starting the job hunt: budget.")
        monkeypatch.setattr(notifier, "get_notifier",
                            lambda: type("N", (), {"send": lambda self, t: sent.append(t)})())
        w = _make_cockpit(qapp)
        w._run_automation({"run": ("job_hunt", "job_hunt")})
        assert pressed == [1]
        assert sent == ["Not starting the job hunt: budget."]
        w.close()

    def test_a_check_row_offers_no_run_button(self, qapp):
        from ui.cockpit import _AutoRow
        watch = {"name": "Battery low", "when": "6 AM–12 AM", "detail": "Every minute",
                 "state": "watching", "run": None, "kind": "watching"}
        row = _AutoRow(watch, on_run=lambda a: None)
        row.enterEvent(None)
        assert row._run_btn.isHidden()
        row.deleteLater()

    def test_the_panel_counts_what_is_on_and_never_says_manual(self, qapp, monkeypatch):
        from PyQt6.QtWidgets import QLabel
        import ui.cockpit as mod
        rows = self._scheduled(self._jobs())
        monkeypatch.setattr(mod.CockpitWindow, "_automations", lambda self: rows)
        w = _make_cockpit(qapp)
        w._refresh_rails()
        texts = [label.text() for label in w.findChildren(QLabel)]
        assert w._auto_count.text() == "3 ON"
        assert "Morning Briefing" in texts and "FRI 6 PM" in texts
        assert "MANUAL" not in texts
        w.close()

    def test_every_automation_is_listed_under_its_own_heading(self, qapp):
        import core.proactive as pa
        from PyQt6.QtWidgets import QLabel
        _seed_schedules(self._jobs())
        w = _make_cockpit(qapp)
        w._refresh_rails()
        rows = w._auto_list.count()
        texts = [label.text() for label in w._auto_scroll.findChildren(QLabel)]
        assert rows == len(w._automations()) + 3       # a heading over each group
        assert f"SCHEDULED  ·  3" in texts
        assert "ON REQUEST  ·  1" in texts
        assert f"WATCHING  ·  {len(pa.WATCHES)}" in texts
        assert "Prayer heads-up" in texts
        w.close()

    def test_the_list_scrolls_instead_of_growing_down_the_rail(self, qapp):
        w = _make_cockpit(qapp)
        w._refresh_readouts()
        w._show_window()
        for _ in range(40):
            qapp.processEvents()
        inner = w._auto_scroll.widget().height()
        assert w._auto_scroll.height() < inner, "the list is not scrolling"
        assert w._auto_scroll.verticalScrollBar().maximum() > 0
        # The conversation still gets the taller half of the rail.
        assert w._reading_panel.height() > w._skills_btn.parentWidget().height()
        w.close()


class TestSkillRows:
    """The SKILLS tab: what El Fager can do when you ask — taught skills,
    routines, and the apps it is connected to."""

    def _rows(self, skills=None, macros=None, disabled=()):
        from ui.cockpit import _skill_rows
        return _skill_rows(
            skills if skills is not None else [
                {"name": "gym mode", "trigger_phrases": ["gym mode", "workout music"],
                 "run_count": 3, "last_run_at": "2026-09-14T18:00:00"},
                {"name": "deep dive", "trigger_phrases": [], "run_count": 0,
                 "last_run_at": None}],
            macros if macros is not None else [
                {"name": "study_mode", "description": "Focus session — Pomodoro, lights, study music"}],
            set(disabled))

    def test_a_taught_skill_shows_what_to_say_and_when_it_last_ran(self):
        row = self._rows()[0]
        assert row["name"] == "Gym mode"
        assert row["detail"] == 'Say "gym mode" · ran 3 times, last Sep 14'
        assert row["run"] == ("skill", "gym mode")
        assert row["kind"] == "taught"

    def test_a_skill_without_a_phrase_says_how_to_run_it(self):
        assert self._rows()[1]["detail"] == "Never ran · ask for it by name"

    def test_a_routine_shows_what_it_does_and_runs_as_a_macro(self):
        row = [r for r in self._rows() if r["kind"] == "routine"][0]
        assert row["name"] == "Study mode"
        assert row["detail"] == "Pomodoro, lights, study music"
        assert row["run"] == ("macro", "study_mode")

    def test_a_long_routine_description_is_cut_not_clipped(self):
        macros = [{"name": "morning_routine", "description":
                   "Full morning ritual — greeting, weather, prayer, calendar, emails, headlines"}]
        row = [r for r in self._rows(macros=macros) if r["kind"] == "routine"][0]
        assert row["detail"] == "greeting, weather, prayer, calendar, emails…"

    def test_connected_apps_come_last_and_say_on_or_off(self):
        import core.brain as brain
        rows = [r for r in self._rows(disabled=["gmail"]) if r["kind"] == "app"]
        assert len(rows) == len(brain.SKILL_TOOLS)
        apps = {r["name"]: r for r in rows}
        assert apps["Gmail"]["when"] == "OFF" and apps["Gmail"]["state"] == "paused"
        assert apps["WhatsApp"]["when"] == "ON" and apps["WhatsApp"]["run"] is None

    def test_the_groups_keep_their_order(self):
        kinds = [r["kind"] for r in self._rows()]
        assert kinds == ["taught"] * 2 + ["routine"] + ["app"] * 6


class TestPanelTabs:
    """One panel, two lists: what runs on its own, and what runs when asked."""

    def _texts(self, w):
        from PyQt6.QtWidgets import QLabel
        return [label.text() for label in w._auto_scroll.findChildren(QLabel)]

    def test_both_tabs_are_labelled_with_their_count(self, qapp):
        w = _make_cockpit(qapp)
        w._refresh_readouts()
        assert w._tab_autos.text().startswith("AUTOMATIONS")
        assert w._tab_skills.text() == f"SKILLS  {len(w._skills())}"
        w.close()

    def test_the_skills_tab_swaps_the_list(self, qapp):
        _seed_schedules([{"id": "morning", "name": "Morning Briefing", "enabled": True,
                          "trigger": {"type": "cron", "hour": 7, "minute": 30}}])
        w = _make_cockpit(qapp)
        w._refresh_readouts()
        assert "Morning Briefing" in self._texts(w)
        w._tab_skills.click()
        texts = self._texts(w)
        assert "Morning Briefing" not in texts
        assert "TAUGHT SKILLS  ·  9" in texts and "Morning routine" in texts
        assert w._auto_count.isHidden()      # "24 ON" belongs to the other tab
        w._tab_autos.click()
        assert "Morning Briefing" in self._texts(w)
        assert not w._auto_count.isHidden()
        w.close()

    def test_the_record_keeps_a_button_of_its_own(self, qapp):
        w = _make_cockpit(qapp)
        assert w._skills_btn.text() == "LEDGER"
        w.close()


class TestRunAutomation:
    """Hovering a row offers RUN: a job runs through the scheduler off the GUI
    thread, a skill goes to El Fager as a request."""

    _JOB = {"name": "Morning Briefing", "when": "7:30 AM", "detail": "Daily · never ran",
            "state": "ok", "run": ("job", "morning"), "kind": "scheduled"}

    def test_run_shows_only_while_the_row_is_hovered(self, qapp):
        from ui.cockpit import _AutoRow
        clicked = []
        row = _AutoRow(self._JOB, on_run=clicked.append)
        assert row._run_btn.isHidden()
        row.enterEvent(None)
        assert not row._run_btn.isHidden() and row._when.isHidden()
        row._run_btn.click()
        assert clicked == [self._JOB]
        row.leaveEvent(None)
        assert row._run_btn.isHidden() and not row._when.isHidden()
        row.deleteLater()

    def test_a_running_row_says_so_and_offers_no_button(self, qapp):
        from ui.cockpit import _AutoRow
        row = _AutoRow(self._JOB, on_run=lambda a: None, running=True)
        row.enterEvent(None)
        assert row._when.text() == "RUNNING"
        assert row._run_btn.isHidden()
        row.deleteLater()

    def test_a_job_runs_once_in_the_background_then_the_row_refreshes(self, qapp, monkeypatch):
        import threading
        import time
        from PyQt6.QtWidgets import QLabel
        import core.scheduler as scheduler
        import ui.cockpit as mod

        release, calls = threading.Event(), []

        class FakeScheduler:
            def run_now(self, job_id):
                calls.append((job_id, threading.current_thread() is threading.main_thread()))
                release.wait(5)
                return "done"

        monkeypatch.setattr(scheduler, "get_instance", lambda: FakeScheduler())
        job = self._JOB
        monkeypatch.setattr(mod.CockpitWindow, "_automations", lambda self: [dict(job)])
        w = _make_cockpit(qapp)
        w._refresh_rails()

        w._run_automation(self._JOB)
        w._run_automation(self._JOB)          # a second click while it runs
        assert "RUNNING" in [l.text() for l in w._auto_list.parentWidget().findChildren(QLabel)]

        release.set()
        deadline = time.time() + 5
        while "morning" in w._running and time.time() < deadline:
            qapp.processEvents()
        assert calls == [("morning", False)]
        assert "RUNNING" not in [l.text() for l in w.findChildren(QLabel) if not l.isHidden()]
        w.close()

    def test_a_routine_runs_its_macro_in_the_background(self, qapp, monkeypatch):
        import threading
        import time
        import tools.macro_tool as macro_tool
        calls = []
        monkeypatch.setattr(macro_tool, "run_macro",
                            lambda name: calls.append((name, threading.current_thread()
                                                       is threading.main_thread())) or "ok")
        w = _make_cockpit(qapp)
        w._run_automation({"name": "Study mode", "run": ("macro", "study_mode"),
                           "kind": "routine"})
        deadline = time.time() + 5
        while "study_mode" in w._running and time.time() < deadline:
            qapp.processEvents()
        assert calls == [("study_mode", False)]
        w.close()

    def test_a_skill_is_asked_for_through_the_conversation(self, qapp, monkeypatch):
        w = _make_cockpit(qapp)
        asked = []
        monkeypatch.setattr(w, "_start_pipeline", lambda text_input=None: asked.append(text_input))
        w._run_automation({"name": "Gym mode", "run": ("skill", "gym mode"),
                           "kind": "scheduled"})
        assert asked == ["Run my skill 'gym mode'."]
        w.close()


class TestStepMark:
    @pytest.mark.parametrize("status", ["active", "done", "failed"])
    def test_each_mark_paints_without_a_font_glyph(self, qapp, status):
        # No bundled face carries U+2713, so the ledger's marks are painted.
        from ui.widgets import StepMark
        mark = StepMark(status, "#56C99C")
        assert not mark.grab().isNull()
        mark.deleteLater()


class TestConversation:
    """The right rail's TRANSCRIPT panel is one scrolling conversation: every
    exchange today stacked in order, kept until midnight.
    The words live only there — none of them is painted over the sphere."""

    def _exchange(self, w, heard, answer):
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", heard, "")
        w.on_state_update("speaking", heard, answer)

    def _panel(self, w):
        from PyQt6.QtWidgets import QLabel
        return [label.text() for label in w._reading_box.findChildren(QLabel)]

    def test_answer_shows_in_its_tab_while_it_is_still_being_spoken(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", "plan my day", "")
        w.on_answer_text("Gym at seven.")
        assert "Gym at seven." in self._panel(w)
        w.on_answer_text("Gym at seven. Lecture at noon.")
        assert "Gym at seven. Lecture at noon." in self._panel(w)
        assert len(w._exchanges) == 1
        w.close()

    def test_automations_sit_above_the_conversation_which_takes_the_height(self, qapp):
        w = _make_cockpit(qapp)
        rail = w._reading_panel.parentWidget().layout()
        assert rail.indexOf(w._reading_panel) == 1
        assert rail.stretch(1) == 1
        w.close()

    def test_no_words_are_painted_over_the_sphere(self, qapp):
        from PyQt6.QtWidgets import QLabel
        w = _make_cockpit(qapp)
        self._exchange(w, "what's on my calendar", "Gym at seven.")
        outside_panel = [label.text() for label in w.findChildren(QLabel)
                         if not w._reading_panel.isAncestorOf(label)]
        assert "what's on my calendar" not in outside_panel
        assert "Gym at seven." not in outside_panel
        w.close()


    def test_a_short_answer_is_in_the_panel_too(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "thanks", "Any time.")
        assert "Any time." in self._panel(w)
        w.close()

    def test_asking_again_adds_the_question_before_the_answer(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "first question", "first answer")
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", "second question", "")
        assert "second question" in self._panel(w)
        assert "first answer" in self._panel(w)
        w.close()

    def test_the_same_words_twice_are_two_exchanges(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "Send it.", "Sent.")
        self._exchange(w, "Send it.", "Already sent.")
        assert self._panel(w).count("Send it.") == 2
        w.close()

    def test_every_exchange_is_kept_not_just_the_last_twenty(self, qapp):
        w = _make_cockpit(qapp)
        for n in range(30):
            self._exchange(w, f"question {n}", f"answer {n}")
        assert "answer 0" in self._panel(w)
        assert "answer 29" in self._panel(w)
        w.close()

    def test_each_exchange_carries_its_time(self, qapp):
        import re
        w = _make_cockpit(qapp)
        self._exchange(w, "first question", "first answer")
        self._exchange(w, "second question", "second answer")
        stamps = [t for t in self._panel(w) if re.fullmatch(r"\d{1,2}:\d{2} [AP]M", t)]
        assert len(stamps) == 2
        w.close()

    def test_a_long_answer_keeps_its_sections(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "summary please", "**Academic:** " + "two left. " * 30)
        assert "ACADEMIC" in self._panel(w)
        assert not any("**" in t for t in self._panel(w))
        w.close()

    def test_listening_again_keeps_the_conversation(self, qapp):
        # The mic reopens a beat after every answer; that must not wipe what
        # Mo is still reading.
        w = _make_cockpit(qapp)
        self._exchange(w, "old question", "old answer")
        w.on_state_update("listening", "", "")
        assert "old answer" in self._panel(w)
        w.close()

    def test_an_interrupted_answer_says_so_and_only_that_one(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "first question", "first answer")
        self._exchange(w, "tell me a story", "Once upon a time")
        w.on_state_update("interrupted", "tell me a story", "Once upon a time")
        panel = self._panel(w)
        assert panel.count("INTERRUPTED") == 1
        assert panel.index("INTERRUPTED") > panel.index("Once upon a time")
        w.close()

    # Esc, closing, and the view pill all used to wipe the conversation, so
    # nothing said earlier in the day could be read again. It is kept until
    # midnight now.

    def test_escape_keeps_todays_conversation(self, qapp):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        self._exchange(w, "first question", "first answer")
        w.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                  Qt.KeyboardModifier.NoModifier))
        assert "first answer" in self._panel(w)
        w.close()

    def test_closing_and_the_view_pill_keep_it_the_same_day(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "morning question", "morning answer")
        w._close()
        self._exchange(w, "noon question", "noon answer")
        w._open_knowledge()
        assert len(w._exchanges) == 2
        assert [b.toolTip() for b in w._tab_buttons] == ["morning question", "noon question"]
        w.close()

    def test_the_first_question_after_midnight_starts_empty(self, qapp):
        from datetime import date, timedelta
        w = _make_cockpit(qapp)
        self._exchange(w, "yesterday", "old")
        w._transcript_day = date.today() - timedelta(days=1)
        self._exchange(w, "today", "new")
        assert "yesterday" not in self._panel(w)
        assert "today" in self._panel(w)
        assert len(w._tab_buttons) == 1
        w.close()

    def test_opening_after_midnight_starts_empty(self, qapp):
        from datetime import date, timedelta
        w = _make_cockpit(qapp)
        self._exchange(w, "yesterday", "old")
        w._transcript_day = date.today() - timedelta(days=1)
        w._start_new_day_if_needed()          # what open() does first
        assert self._panel(w) == []
        assert w._scrub_value.text() == "—"
        w.close()

    def test_the_pipeline_caption_is_not_taken_for_what_mo_said(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", "Transcribing...", "")
        w.on_error("Nothing heard — please try again")
        assert self._panel(w) == []
        assert w._notice.text() == "Nothing heard — please try again"
        w.close()

    def test_the_voice_bar_names_who_you_are_talking_to(self, qapp):
        w = _make_cockpit(qapp)
        assert "talking to El Fager through voice" in w._voice_bar.text()
        w.on_state_update("listening", "", "")
        assert w._voice_bar.text().strip().startswith("LISTENING")
        w.on_state_update("speaking", "hi", "Hello.")
        assert w._voice_bar.text().strip().startswith("SPEAKING")
        w.on_pipeline_done()
        assert "talking to El Fager through voice" in w._voice_bar.text()
        w.close()

    def _icon_inks(self, w):
        """(hue, saturation) of every solid pixel in the voice bar's icon.

        Plain numbers only, and the Qt images freed here on the main thread:
        left for Python's collector, a pixmap can be freed from whatever
        thread next allocates — a Command Center worker, in this suite — and
        that crashed a later test outright."""
        import gc
        pixmap = w._voice_bar.icon().pixmap(w._voice_bar.iconSize())
        image = pixmap.toImage()
        inks = []
        for x in range(image.width()):
            for y in range(image.height()):
                colour = image.pixelColor(x, y)
                if colour.alpha() > 200:
                    inks.append((colour.hue(), colour.saturation()))
        del colour, image, pixmap
        gc.collect()
        return inks

    def test_the_voice_bar_leads_with_a_painted_microphone(self, qapp):
        w = _make_cockpit(qapp)
        assert not w._voice_bar.icon().isNull()
        assert len(self._icon_inks(w)) > 20, "the microphone painted nothing"
        w.close()

    def test_the_microphone_takes_the_colour_of_what_it_is_doing(self, qapp):
        from PyQt6.QtGui import QColor
        from ui import tokens
        w = _make_cockpit(qapp)
        w.on_state_update("listening", "", "")
        listening = QColor(tokens.CK_STATE["listening"]).hue()
        inks = self._icon_inks(w)
        assert inks and all(abs(hue - listening) <= 12 for hue, saturation in inks
                            if saturation > 60), "listening mic is not the listening colour"
        w.close()

    @pytest.mark.parametrize("state,orb_state", [("processing", "thinking"), ("speaking", "speaking")])
    def test_the_microphone_matches_the_sphere_not_the_old_gold(self, qapp, state, orb_state):
        from PyQt6.QtGui import QColor
        from ui import tokens
        w = _make_cockpit(qapp)
        w.on_state_update(state, "", "")
        want = QColor(tokens.CK_ORB[orb_state]).hue()
        inks = self._icon_inks(w)
        assert inks and all(abs(hue - want) <= 12 for hue, saturation in inks
                            if saturation > 60), f"{state} mic is not the sphere's colour"
        assert tokens.rgba(tokens.CK_ORB[orb_state], 0.45) in w._voice_bar.styleSheet()
        w.close()

    def test_the_voice_bar_starts_a_turn(self, qapp):
        from unittest.mock import patch
        w = _make_cockpit(qapp)
        with patch("core.pipeline.PipelineWorker") as worker:
            w._voice_bar.click()
        assert worker.called
        w.close()


class TestTranscriptTabs:
    """One tab per question along the top of the TRANSCRIPT panel, as the
    reel's panel has them; the selected tab's exchange is the one on show."""

    def _exchange(self, w, heard, answer):
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", heard, "")
        w.on_state_update("speaking", heard, answer)

    def _showing(self, w):
        from PyQt6.QtWidgets import QLabel
        return [label.text() for label in w._reading_box.findChildren(QLabel)
                if label.isVisibleTo(w._reading_box)]

    def test_the_tabs_head_the_transcript_panel(self, qapp):
        w = _make_cockpit(qapp)
        assert w._reading_panel.isAncestorOf(w._tabs_scroll)
        assert w._reading_panel.column.indexOf(w._tabs_row) == 0
        w.close()

    def test_each_question_gets_a_tab_named_for_its_topic_in_order(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "give me my daily briefing", "…")
        self._exchange(w, "what's the weather tomorrow?", "…")
        self._exchange(w, "Thanks.", "Any time.")
        assert [b.text() for b in w._tab_buttons] == [
            "Daily briefing", "Weather tomorrow", "Thanks"]
        w.close()

    def test_the_mouse_wheel_scrolls_the_tabs_sideways(self, qapp):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QWheelEvent
        w = _make_cockpit(qapp)
        w.resize(1536, 816)
        w.show()
        for n in range(12):
            self._exchange(w, f"tell me about topic number {n}", "…")
        for _ in range(20):
            qapp.processEvents()
        bar = w._tabs_scroll.horizontalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum())
        wheel = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        w._tabs_scroll.wheelEvent(wheel)
        assert bar.value() < bar.maximum(), "wheel up should move toward the first tab"
        w.close()

    def test_hovering_a_tab_shows_the_whole_question(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "give me my daily briefing", "…")
        assert w._tab_buttons[0].toolTip() == "give me my daily briefing"
        w.close()

    def test_a_tab_is_wide_enough_for_its_name(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "play Estanna by Fares Sokkar on Spotify", "Playing.")
        tab = w._tab_buttons[0]
        assert tab.width() >= tab.fontMetrics().horizontalAdvance(tab.text()) + 16
        w.close()

    def test_a_turn_with_nothing_heard_falls_back_to_its_number(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("speaking", "", "Good morning.")   # no question came first
        assert [b.text() for b in w._tab_buttons] == ["1"]
        w.close()


    def test_only_the_selected_exchange_is_on_show(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "first question", "first answer")
        self._exchange(w, "second question", "second answer")
        assert "second answer" in self._showing(w)
        assert "first answer" not in self._showing(w)

        w._tab_buttons[0].click()
        assert "first question" in self._showing(w)
        assert "first answer" in self._showing(w)
        assert "second answer" not in self._showing(w)
        assert [b.isChecked() for b in w._tab_buttons] == [True, False]
        w.close()

    def test_asking_again_opens_the_next_tab_before_the_answer(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "first question", "first answer")
        w._tab_buttons[0].click()
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", "second question", "")
        assert len(w._tab_buttons) == 2
        assert w._tab_buttons[1].isChecked()
        assert "second question" in self._showing(w)
        w.close()

    def test_the_arrows_and_the_tabs_agree(self, qapp):
        w = _make_cockpit(qapp)
        for n in range(3):
            self._exchange(w, f"question {n}", f"answer {n}")
        w._scrub_prev.click()
        assert w._tab_buttons[1].isChecked()
        w._tab_buttons[0].click()
        assert w._scrub_value.text() == "1 / 3"
        w.close()

    def test_a_long_answer_scrolls_inside_its_tab_from_the_top(self, qapp):
        w = _make_cockpit(qapp)
        w.resize(1536, 816)
        w.show()
        self._exchange(w, "brief me", "**Mail:** " + "five unread from LinkedIn. " * 80)
        self._exchange(w, "thanks", "Any time.")
        w._tab_buttons[0].click()
        for _ in range(20):
            qapp.processEvents()
        bar = w._reading_scroll.verticalScrollBar()
        assert bar.maximum() > 0, "a long answer should scroll"
        assert bar.value() == 0, "it should open at its first line"
        w.close()

    def _wrapped_on_show(self, w):
        from PyQt6.QtWidgets import QLabel
        return [label for label in w._reading_box.findChildren(QLabel)
                if label.isVisibleTo(w._reading_box) and label.wordWrap()]

    def test_every_word_of_a_long_exchange_can_be_scrolled_to(self, qapp):
        # 2026-09-13: Mo's question and the answer were both cut off mid-line
        # at 93px each when they needed ~155, and the panel scrolled 12px.
        w = _make_cockpit(qapp)
        w.setGeometry(0, 0, 1536, 816)
        w.show()
        heard = ("Ok, let's go one by one. First one, the October deadline was my "
                 "graduation, which is on the 4th of October. That's why I was talking "
                 "about October and you wanted the deadline. Class schedule, I don't "
                 "need it anymore because I finished. Todoist, keep it off for now.")
        answer = ("Locked in — October 4th for graduation, no class schedule needed "
                  "anymore since you're done, and Todoist stays off the list till your "
                  "days actually have a shape to track. Notion can wait as well, till "
                  "you need somewhere to put the bigger projects. Anything else?")
        self._exchange(w, heard, answer)
        for _ in range(60):
            qapp.processEvents()
        for label in self._wrapped_on_show(w):
            assert label.height() >= label.heightForWidth(label.width()),                 f"clipped: {label.text()[:30]!r}"
        bar = w._reading_scroll.verticalScrollBar()
        viewport = w._reading_scroll.viewport().height()
        assert bar.maximum() + viewport >= sum(
            label.heightForWidth(label.width()) for label in self._wrapped_on_show(w))
        w.close()

    def test_a_short_answer_sits_right_under_the_question(self, qapp):
        w = _make_cockpit(qapp)
        w.setGeometry(0, 0, 1536, 816)
        w.show()
        self._exchange(w, "thanks", "Any time, Mo.")
        for _ in range(60):
            qapp.processEvents()
        said, reply = self._wrapped_on_show(w)[:2]
        gap = reply.mapTo(w, reply.rect().topLeft()).y() -             said.mapTo(w, said.rect().bottomLeft()).y()
        assert gap < 30, f"the answer floats {gap}px below the question"
        w.close()

    def test_escape_keeps_the_tabs(self, qapp):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent
        from core import staging
        staging.reset()
        w = _make_cockpit(qapp)
        self._exchange(w, "first question", "first answer")
        w.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                  Qt.KeyboardModifier.NoModifier))
        assert len(w._tab_buttons) == 1
        assert w._tabs_layout.count() == 1
        w.close()


class TestModelNamedTabs:
    """A tab is named from the words heard, so garbled speech gave tabs like
    "End day able". Once the answer is in, the fast model names the exchange
    from the answer too, and the tab takes that name."""

    def _answered(self, qapp, reply, heard="End day able",
                  answer="Here's your evening shutdown: journal, then lights off."):
        w = _make_cockpit(qapp)
        w.brain.synthesize = MagicMock(side_effect=reply) if isinstance(reply, Exception) \
            else MagicMock(return_value=reply)
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", heard, "")
        w.on_state_update("speaking", heard, answer)
        w._naming.join(5)
        qapp.processEvents()
        return w

    def test_the_models_name_replaces_the_one_from_the_words_heard(self, qapp):
        w = self._answered(qapp, "Evening shutdown")
        tab = w._tab_buttons[0]
        assert tab.text() == "Evening shutdown"
        assert tab.toolTip() == "End day able"         # what was heard stays
        assert tab.width() >= tab.fontMetrics().horizontalAdvance("Evening shutdown")
        w.close()

    def test_the_model_is_shown_the_answer_not_only_the_garbled_words(self, qapp):
        w = self._answered(qapp, "Evening shutdown")
        prompt = w.brain.synthesize.call_args.args[0]
        assert "End day able" in prompt and "evening shutdown" in prompt
        w.close()

    @pytest.mark.parametrize("reply", [
        "", "   ", "A name that is far too long for a tab label",
        "Evening shutdown\nBecause the answer was about it",
        RuntimeError("API down"),
    ])
    def test_anything_but_a_short_name_keeps_the_first_one(self, qapp, reply):
        w = self._answered(qapp, reply)
        assert w._tab_buttons[0].text() == "End day able"
        w.close()

    def test_a_four_word_phrase_is_a_name(self, qapp):
        # The live model's name for "What the fuck was that? I'm telling you,
        # send a WhatsApp…" — rejected at three words, so that tab stayed
        # "Fuck telling send".
        w = self._answered(qapp, "Sending message to Ziad")
        assert w._tab_buttons[0].text() == "Sending message to Ziad"
        w.close()

    def test_quotes_and_a_full_stop_are_trimmed(self, qapp):
        w = self._answered(qapp, '"Evening shutdown."')
        assert w._tab_buttons[0].text() == "Evening shutdown"
        w.close()

    def test_each_exchange_is_named_once(self, qapp):
        w = self._answered(qapp, "Evening shutdown")
        w.on_state_update("speaking", "End day able", "Here's your evening shutdown.")
        w._naming.join(5)
        assert w.brain.synthesize.call_count == 1
        w.close()

    def test_a_name_for_a_conversation_already_cleared_goes_nowhere(self, qapp):
        from datetime import date, timedelta
        w = self._answered(qapp, "Evening shutdown")
        w._transcript_day = date.today() - timedelta(days=1)
        w.on_state_update("processing", "weather", "")      # a new day's first
        w._on_topic_named(0, "End day able", "Evening shutdown")   # late arrival
        assert w._tab_buttons[0].text() == "Weather"
        w.close()


class TestTopic:
    @pytest.mark.parametrize("heard,topic", [
        ("give me my daily briefing", "Daily briefing"),
        ("what's the weather tomorrow?", "Weather tomorrow"),
        ("Hey Fager, what's on my calendar today?", "Calendar today"),
        ("Can you send an email to Ahmed please", "Send email Ahmed"),
        ("play Estanna by Fares Sokkar on Spotify", "Play Estanna Fares"),
        ("Thanks.", "Thanks"),
        ("I want you to open YouTube and play the first video", "Open YouTube play"),
    ])
    def test_it_names_the_question_in_a_few_words(self, heard, topic):
        from ui.cockpit import _topic
        assert _topic(heard) == topic

    @pytest.mark.parametrize("heard,topic", [
        # Mo's own words from the logs, where filler used to crowd out the topic
        ("Now I want you to open YouTube and play the first video", "Open YouTube play"),
        ("and tell me what's on my calendar and the review", "Calendar review"),
        ("No, it's by Ferris Sokhar and someone else", "Ferris Sokhar someone"),
        ("I'm trying the new transcript, tell me how it works", "Trying new transcript"),
    ])
    def test_everyday_filler_does_not_crowd_out_the_topic(self, heard, topic):
        from ui.cockpit import _topic
        assert _topic(heard) == topic

    def test_a_long_name_drops_whole_words_not_half_of_one(self):
        from ui.cockpit import _topic
        assert _topic("Is everything working alright?") == "Everything working"

    def test_one_overlong_word_is_cut_short(self):
        from ui.cockpit import _topic
        from ui.cockpit import _TOPIC_MAX
        topic = _topic("supercalifragilisticexpialidocious")
        assert len(topic) <= _TOPIC_MAX and topic.endswith("…")

    def test_a_repeated_word_is_named_once(self):
        from ui.cockpit import _topic
        assert _topic("Yes, I help! Yes, I help!") == "Help"

    def test_nothing_but_filler_names_nothing(self):
        from ui.cockpit import _topic
        assert _topic("hey fager, can you please") == ""


class TestNeverWiderThanTheScreen:
    """Mo's screen is 1536 wide. The old stage's heard line never wrapped, so
    one long sentence made the window 2007px wide and the whole Cockpit slid
    over until a restart. Nothing said, answered or reported may do that."""

    URL = ("https://www.youtube.com/watch?v=dQw4w9WgXcQ"
           "&list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG&index=4")
    LOOP = ("I'm going to get a check on my calendar, and I'm going to get a check "
            "on my calendar, and I'm going to get a check on my calendar today")

    def _min_width(self, qapp, w):
        for _ in range(20):
            qapp.processEvents()
        return w.minimumSizeHint().width()

    def test_a_long_sentence_does_not_widen_the_window(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("processing", self.LOOP, "")
        w.on_state_update("speaking", self.LOOP, self.LOOP)
        assert self._min_width(qapp, w) <= 1280
        w.close()

    def test_a_long_unbroken_error_does_not_widen_the_window(self, qapp):
        w = _make_cockpit(qapp)
        w.on_error("Pipeline error: " + "x" * 240)
        assert self._min_width(qapp, w) <= 1280
        w.close()

    def test_a_link_in_an_answer_wraps_inside_the_panel(self, qapp):
        w = _make_cockpit(qapp)
        w.setGeometry(0, 0, 1536, 816)
        w.show()
        w.on_state_update("processing", "open the video", "")
        w.on_state_update("speaking", "open the video", f"Here it is: {self.URL}")
        self._min_width(qapp, w)
        assert w._reading_box.minimumSizeHint().width() <= w._reading_scroll.viewport().width()
        w.close()


class TestReadableTranscript:
    """The transcript is the thing to read on this surface, so it is set
    large and heavy enough to read from a normal sitting distance."""

    def _exchange_labels(self, w):
        from PyQt6.QtWidgets import QLabel
        w.on_state_update("processing", "what's on my calendar", "")
        w.on_state_update("speaking", "what's on my calendar", "Gym at seven.")
        return {label.text(): label.styleSheet()
                for label in w._reading_box.findChildren(QLabel)}

    @staticmethod
    def _px(style):
        import re
        return int(re.search(r"font-size:\s*(\d+)px", style).group(1))

    def test_what_mo_said_is_bold_and_large(self, qapp):
        import re
        w = _make_cockpit(qapp)
        style = self._exchange_labels(w)["what's on my calendar"]
        assert self._px(style) >= 15
        assert int(re.search(r"font-weight:\s*(\d+)", style).group(1)) >= 600
        w.close()

    def test_the_answer_is_large(self, qapp):
        import re
        w = _make_cockpit(qapp)
        style = self._exchange_labels(w)["Gym at seven."]
        assert self._px(style) >= 16
        assert int(re.search(r"font-weight:\s*(\d+)", style).group(1)) >= 500
        w.close()

    def test_the_link_text_itself_is_unchanged_for_copying(self, qapp):
        # Break points are zero-width: the words read and copy as written.
        w = _make_cockpit(qapp)
        w.on_state_update("processing", "q", "")
        w.on_state_update("speaking", "q", "see https://a.example.com/very/long/path_name")
        from PyQt6.QtWidgets import QLabel
        texts = [label.text().replace("​", "")
                 for label in w._reading_box.findChildren(QLabel)]
        assert "see https://a.example.com/very/long/path_name" in texts
        w.close()


class TestArrows:
    """‹ › under the sphere step through the conversation in the rail."""

    def _exchange(self, w, heard, answer):
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", heard, "")
        w.on_state_update("speaking", heard, answer)

    def _focused(self, w):
        return [button.isChecked() for button in w._tab_buttons]

    def test_they_sit_under_the_sphere_not_in_the_rail(self, qapp):
        w = _make_cockpit(qapp)
        for widget in (w._scrub_prev, w._scrub_value, w._scrub_next):
            assert not w._reading_panel.isAncestorOf(widget)
        w.close()

    def test_with_nothing_said_they_are_harmless(self, qapp):
        w = _make_cockpit(qapp)
        assert w._scrub_value.text() == "—"
        w._scrub_prev.click()
        w._scrub_next.click()
        assert w._scrub_value.text() == "—"
        w.close()

    def test_they_step_through_the_exchanges_and_stop_at_the_ends(self, qapp):
        w = _make_cockpit(qapp)
        for n in range(3):
            self._exchange(w, f"question {n}", f"answer {n}")
        assert w._scrub_value.text() == "3 / 3"
        w._scrub_prev.click()
        assert w._scrub_value.text() == "2 / 3"
        for _ in range(5):
            w._scrub_prev.click()
        assert w._scrub_value.text() == "1 / 3"
        for _ in range(5):
            w._scrub_next.click()
        assert w._scrub_value.text() == "3 / 3"
        w.close()

    def test_the_exchange_they_point_at_is_marked_in_the_rail(self, qapp):
        w = _make_cockpit(qapp)
        for n in range(3):
            self._exchange(w, f"question {n}", f"answer {n}")
        assert self._focused(w) == [False, False, True]
        w._scrub_prev.click()
        assert self._focused(w) == [False, True, False]
        w.close()

    def test_a_new_question_takes_the_focus_to_itself(self, qapp):
        w = _make_cockpit(qapp)
        for n in range(3):
            self._exchange(w, f"question {n}", f"answer {n}")
        w._scrub_prev.click()
        w._scrub_prev.click()
        w.on_state_update("listening", "", "")
        w.on_state_update("processing", "question 3", "")
        assert w._scrub_value.text() == "4 / 4"
        assert self._focused(w) == [False, False, False, True]
        w.close()

    def test_an_answer_landing_keeps_the_mark(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "question", "answer")
        w.on_state_update("interrupted", "question", "answer")
        assert self._focused(w) == [True]
        w.close()

    def test_closing_keeps_their_place(self, qapp):
        w = _make_cockpit(qapp)
        self._exchange(w, "question", "answer")
        before = w._scrub_value.text()
        w._close()
        assert before != "—"
        assert w._scrub_value.text() == before
        w.close()

    def test_under_the_sphere_only_the_arrows_and_the_pill(self, qapp):
        # The reel has ‹ › and the view pill there and nothing else; the
        # ledger stays on L and on its button in the rail, the keys on ?.
        from PyQt6.QtWidgets import QLabel, QPushButton
        w = _make_cockpit(qapp)
        texts = [x.text() for x in w.findChildren((QLabel, QPushButton))]
        assert w._skills_btn.parentWidget() is w._auto_scroll.parentWidget()
        assert not any("ESC CLOSE" in t for t in texts)
        w.close()

    def test_the_view_pill_asks_for_the_command_center(self, qapp):
        w = _make_cockpit(qapp)
        seen = []
        w.knowledge_requested.connect(lambda: seen.append(True))
        w._view_pill.click()
        assert seen == [True]
        w.close()


class TestRails:
    def test_the_automations_panel_ends_at_its_buttons(self, qapp):
        w = _make_cockpit(qapp)
        w._refresh_readouts()
        w._show_window()
        for _ in range(40):
            qapp.processEvents()
        panel = w._skills_btn.parentWidget()
        bottom_of_buttons = w._skills_btn.mapTo(panel, w._skills_btn.rect().bottomLeft()).y()
        assert panel.height() - bottom_of_buttons <= 24,             f"{panel.height() - bottom_of_buttons}px of panel below the buttons"
        w.close()

    def test_automations_never_render_an_empty_rail(self, qapp):
        w = _make_cockpit(qapp)
        w._refresh_rails()
        assert w._auto_list.count() >= 1
        w.close()


class TestSphereStage:
    def _stage(self, call):
        return [float(v) for v in call.split("(", 1)[1].rstrip(")").split(",")]

    def _host_rect(self, w, widget):
        from PyQt6.QtCore import QPoint
        top_left = widget.mapToGlobal(QPoint(0, 0)) - w._orb_host.mapToGlobal(QPoint(0, 0))
        return top_left.x(), top_left.y(), widget.width(), widget.height()

    def test_the_page_is_told_the_free_space_in_the_middle_column(self, qapp):
        # The sphere belongs between the state label and the arrows, centred
        # on the column — not on the window, whose side panels differ in width.
        w = _make_cockpit(qapp)
        for size in ((1280, 760), (1536, 816)):
            w._show_window()
            w.resize(*size)
            for _ in range(40):
                qapp.processEvents()
            sent = []
            w._orb_js = sent.append
            w._push_stage()
            assert sent and sent[-1].startswith("window.orb && window.orb.setStage(")
            x, y, width, height = self._stage(sent[-1])
            cx, _, cw, _ = self._host_rect(w, w._centre)
            _, chip_y, _, chip_h = self._host_rect(w, w._state_chip)
            _, arrows_y, _, _ = self._host_rect(w, w._scrub_prev)
            assert abs(x - (cx + cw / 2)) <= 1 and width == cw
            assert abs((y - height / 2) - (chip_y + chip_h)) <= 1, "top of the space is not under the label"
            assert abs((y + height / 2) - arrows_y) <= 1, "bottom of the space is not the arrows"
        w.close()

    def test_a_resize_moves_the_sphere_with_the_column(self, qapp):
        w = _make_cockpit(qapp)
        w._show_window()
        sent = []
        w._orb_js = sent.append
        w.resize(w.width() + 200, w.height())
        for _ in range(40):
            qapp.processEvents()
        assert any(call.startswith("window.orb && window.orb.setStage(") for call in sent)
        w.close()


class TestTheLiveMark:
    """Mo chose a mark that moves with the state: it breathes at rest, bounces
    like a level meter while listening, spins while working, and waves while
    speaking. A still dot said nothing the word beside it did not."""

    def _ink(self, mark):
        """Every lit pixel of the mark, as (x, y, hue, saturation).

        Qt images are freed here on the main thread: a pixmap left to the
        collector has crashed this suite from a worker thread before."""
        import gc
        pixmap = mark.grab()
        image = pixmap.toImage()
        ink = []
        for x in range(image.width()):
            for y in range(image.height()):
                colour = image.pixelColor(x, y)
                if colour.alpha() > 40 and colour.lightness() > 40:
                    ink.append((x, y, colour.hue(), colour.saturation()))
        del colour, image, pixmap
        gc.collect()
        return ink

    def test_the_mark_paints_in_every_state(self, qapp):
        w = _make_cockpit(qapp)
        for state in ("idle", "listening", "processing", "speaking"):
            w.on_state_update(state, "", "")
            assert len(self._ink(w._state_chip._mark)) > 5, f"{state} painted almost nothing"
        w.close()

    def test_each_state_has_its_own_shape(self, qapp):
        # Bars, a ring and a dot cover the mark differently; if two states
        # painted the same pixels, the mark would say nothing.
        w = _make_cockpit(qapp)
        shapes = {}
        for state in ("idle", "listening", "processing", "speaking"):
            w.on_state_update(state, "", "")
            shapes[state] = {(x, y) for x, y, _, _ in self._ink(w._state_chip._mark)}
        pairs = [(a, b) for a in shapes for b in shapes if a < b]
        for a, b in pairs:
            same = len(shapes[a] & shapes[b]) / max(1, len(shapes[a] | shapes[b]))
            assert same < 0.8, f"{a} and {b} paint nearly the same mark"
        w.close()

    def test_it_moves_on_its_own(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("listening", "", "")
        first = {(x, y) for x, y, _, _ in self._ink(w._state_chip._mark)}
        for _ in range(12):
            w._state_chip._mark._tick()
        later = {(x, y) for x, y, _, _ in self._ink(w._state_chip._mark)}
        assert first != later, "the mark is not animating"
        w.close()

    def test_it_takes_the_state_colour(self, qapp):
        from PyQt6.QtGui import QColor
        from ui import tokens
        w = _make_cockpit(qapp)
        w.on_state_update("listening", "", "")
        want = QColor(tokens.CK_ORB["listening"]).hue()
        hues = [hue for _, _, hue, sat in self._ink(w._state_chip._mark) if sat > 60]
        assert hues and all(abs(hue - want) <= 12 for hue in hues)
        w.close()

    def test_a_hidden_cockpit_costs_nothing(self, qapp):
        # The Cockpit spends most of its life hidden in the tray; a 20 fps
        # repaint there would be pure waste.
        w = _make_cockpit(qapp)
        w._show_window()
        assert w._state_chip._mark._timer.isActive()
        w.close()
        assert not w._state_chip._mark._timer.isActive()


class TestSphereHearsYou:
    """While it listens, the sphere swells with how loud Mo is."""

    def test_the_loudness_reaches_the_sphere_while_listening(self, qapp):
        w = _make_cockpit(qapp)
        w.on_state_update("listening", "", "")
        sent = []
        w._orb_js = sent.append
        w.on_mic_level(0.42)
        assert sent == ["window.orb && window.orb.setLevel(0.420)"]
        w.close()

    @pytest.mark.parametrize("state", ["idle", "processing", "speaking"])
    def test_a_late_level_does_not_move_it_once_listening_is_over(self, qapp, state):
        # Signals from the recorder's thread can land after the state moved on.
        w = _make_cockpit(qapp)
        w.on_state_update(state, "", "")
        sent = []
        w._orb_js = sent.append
        w.on_mic_level(0.9)
        assert sent == []
        w.close()

    def test_each_turn_wires_the_recorder_to_the_sphere(self, qapp):
        from unittest.mock import patch
        w = _make_cockpit(qapp)
        with patch("core.pipeline.PipelineWorker") as worker:
            w._start_pipeline()
        worker.return_value.mic_level.connect.assert_called_once_with(w.on_mic_level)
        w.close()


class TestNormalWindow:
    """A normal window, not full screen: it sits above the taskbar, has a dark
    title bar of its own, and remembers where it was left."""

    def test_it_opens_smaller_than_the_screen_and_above_the_taskbar(self, qapp):
        from PyQt6.QtWidgets import QApplication
        w = _make_cockpit(qapp)
        w._show_window()
        area = QApplication.primaryScreen().availableGeometry()
        assert not w.isFullScreen()
        assert area.contains(w.frameGeometry())
        assert w.width() < area.width() or w.height() < area.height()
        w.close()

    def test_it_can_never_be_shrunk_past_what_the_layout_needs(self, qapp):
        w = _make_cockpit(qapp)
        w._show_window()
        assert w.minimumWidth() >= w.minimumSizeHint().width()
        assert w.minimumHeight() >= w.minimumSizeHint().height()
        w.close()

    def test_the_title_bar_has_minimise_maximise_and_close(self, qapp):
        w = _make_cockpit(qapp)
        assert {w._btn_min.toolTip(), w._btn_max.toolTip(), w._btn_close.toolTip()}             == {"Minimise", "Maximise", "Close"}
        assert w._title_bar.isAncestorOf(w._btn_close)
        w.close()

    def test_the_title_bar_carries_the_clock_before_the_buttons(self, qapp):
        w = _make_cockpit(qapp)
        w._tick_clock()
        assert w._title_bar.isAncestorOf(w._title_clock)
        assert w._title_clock.text() == w._status.time.text()
        row = w._title_bar.layout()
        assert row.indexOf(w._title_clock) < row.indexOf(w._btn_min)
        w.close()

    def test_close_hides_it_but_el_fager_keeps_running(self, qapp):
        w = _make_cockpit(qapp)
        w._show_window()
        w._btn_close.click()
        assert w.isHidden()
        w.close()

    def test_minimise_keeps_the_conversation(self, qapp):
        w = _make_cockpit(qapp)
        w._show_window()
        w.on_state_update("processing", "what's the weather", "")
        w.on_state_update("speaking", "what's the weather", "Sunny.")
        w._btn_min.click()
        for _ in range(20):
            qapp.processEvents()
        assert w.isMinimized()
        assert len(w._tab_buttons) == 1
        w.close()

    def test_maximise_toggles(self, qapp):
        w = _make_cockpit(qapp)
        w._show_window()
        w._btn_max.click()
        for _ in range(20):
            qapp.processEvents()
        assert w.isMaximized()
        w._btn_max.click()
        for _ in range(20):
            qapp.processEvents()
        assert not w.isMaximized()
        w.close()

    def test_it_reopens_where_it_was_left(self, qapp, settings_file):
        import json
        w = _make_cockpit(qapp)
        w._show_window()
        w.setGeometry(120, 90, 1200, 720)
        w._btn_close.click()
        saved = json.loads(settings_file.read_text(encoding="utf-8"))["cockpit_geometry"]
        assert saved == [120, 90, 1200, 720]
        w.close()

        again = _make_cockpit(qapp)
        again._show_window()
        assert [again.x(), again.y(), again.width(), again.height()] == [120, 90, 1200, 720]
        again.close()

    def test_a_saved_spot_off_screen_is_ignored(self, qapp, settings_file):
        import json
        from PyQt6.QtWidgets import QApplication
        settings_file.write_text(json.dumps({"cockpit_geometry": [9000, 9000, 1200, 720]}),
                                 encoding="utf-8")
        w = _make_cockpit(qapp)
        w._show_window()
        assert QApplication.primaryScreen().availableGeometry().contains(w.frameGeometry())
        w.close()
