"""Unit tests for core/proactive.py — cooldowns, scheduling windows, and
autonomous task execution. No threads are started; checks are called directly.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import core.proactive as pa
from core.proactive import ProactiveEngine, _fmt12


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_STATE_FILE", tmp_path / "proactive_state.json")
    e = ProactiveEngine(speak_fn=None)
    e._deliver = MagicMock()
    return e


class TestFmt12:
    def test_afternoon(self):
        assert _fmt12("13:05") == "1:05 PM"

    def test_midnight(self):
        assert _fmt12("00:30") == "12:30 AM"

    def test_noon(self):
        assert _fmt12("12:00") == "12:00 PM"

    def test_garbage_passthrough(self):
        assert _fmt12("not a time") == "not a time"


class TestCooldown:
    def test_first_call_not_in_cooldown_and_stamps(self, engine, tmp_path):
        assert engine._cooldown("k", 1) is False
        saved = json.loads((tmp_path / "proactive_state.json").read_text(encoding="utf-8"))
        assert "k" in saved

    def test_second_call_within_window_is_blocked(self, engine):
        engine._cooldown("k", 1)
        assert engine._cooldown("k", 1) is True

    def test_expired_cooldown_runs_again(self, engine):
        engine._state["k"] = (datetime.now() - timedelta(hours=2)).isoformat()
        assert engine._cooldown("k", 1) is False

    def test_reset_cooldown_persists(self, engine, tmp_path):
        engine._cooldown("k", 1)
        engine._reset_cooldown("k")
        assert "k" not in engine._state
        saved = json.loads((tmp_path / "proactive_state.json").read_text(encoding="utf-8"))
        assert "k" not in saved

    def test_corrupt_timestamp_treated_as_expired(self, engine):
        engine._state["k"] = "not-a-timestamp"
        assert engine._cooldown("k", 1) is False

    def test_state_survives_reload(self, engine, tmp_path):
        engine._cooldown("k", 5)
        fresh = ProactiveEngine(speak_fn=None)
        assert fresh._cooldown("k", 5) is True


def _patch_all_checks(engine):
    """Replace every _check_* method with a MagicMock; return dict of mocks."""
    mocks = {}
    for name in dir(engine):
        if name.startswith("_check_"):
            m = MagicMock()
            setattr(engine, name, m)
            mocks[name] = m
    return mocks


def _run_checks_at(engine, dt):
    fake = MagicMock(wraps=datetime)
    fake.now = MagicMock(return_value=dt)
    with patch.object(pa, "datetime", fake):
        engine._run_checks()


class TestWatchesCatalogue:
    """WATCHES is what the Cockpit lists as El Fager's background checks. It
    is written by hand beside the checks, so a new check must be added to it
    — that is what this test guards."""

    def test_every_check_is_described_and_nothing_extra(self):
        methods = {n for n in dir(ProactiveEngine) if n.startswith("_check_")}
        assert {w["check"] for w in pa.WATCHES} == methods

    def test_each_entry_reads_as_a_row(self):
        for watch in pa.WATCHES:
            assert watch["name"] and watch["when"] and watch["detail"]
            assert watch["name"][0].isupper()


class TestRunChecksWindows:
    def test_sleep_hours_run_nothing_but_a_waiting_whatsapp_answer(self, engine):
        """Whatever waits on Mo's WhatsApp reply goes on as soon as he replies,
        night or day; everything else sleeps."""
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 3, 0))  # 3 AM
        for name, m in mocks.items():
            if name == "_check_whatsapp_replies":
                m.assert_called_once()
            else:
                m.assert_not_called()

    def test_always_on_checks_run_midday(self, engine):
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 14, 0))  # Wed 2 PM
        for name in ("_check_battery", "_check_prayer_times", "_check_upcoming_events",
                     "_check_autonomous_tasks"):
            mocks[name].assert_called_once()
        mocks["_check_journal"].assert_not_called()
        mocks["_check_weather"].assert_not_called()

    def test_morning_checks_run_in_morning(self, engine):
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 8, 0))  # Wed 8 AM
        mocks["_check_deadlines"].assert_called_once()
        mocks["_check_weather"].assert_called_once()
        mocks["_check_rest_day"].assert_called_once()
        mocks["_check_journal"].assert_not_called()

    def test_evening_checks_run_in_evening(self, engine):
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 20, 0))  # Wed 8 PM
        mocks["_check_journal"].assert_called_once()
        mocks["_check_expenses"].assert_called_once()
        mocks["_check_budget_exceeded"].assert_called_once()
        mocks["_check_gym_session"].assert_called_once()
        mocks["_check_weather"].assert_not_called()

    def test_weekly_review_only_on_fri_sat_evening(self, engine):
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 3, 18, 0))  # Friday 6 PM
        mocks["_check_weekly_review"].assert_called_once()

        mocks2 = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 18, 0))  # Wednesday 6 PM
        mocks2["_check_weekly_review"].assert_not_called()


class TestAutonomousTasks:
    def _mgr(self, due):
        mgr = MagicMock()
        mgr.get_due.return_value = due
        return mgr

    def test_no_brain_fn_does_nothing(self, engine):
        engine._brain_fn = None
        with patch("core.autonomous_tasks.AutonomousTaskManager") as mgr_cls:
            engine._check_autonomous_tasks()
        mgr_cls.assert_not_called()

    def test_due_task_executed_and_completed(self, engine):
        brain = MagicMock(return_value="NVDA RSI is 38.")
        engine._brain_fn = brain
        mgr = self._mgr([{"id": "t1", "description": "check NVDA RSI"}])
        with patch("core.autonomous_tasks.AutonomousTaskManager", return_value=mgr):
            engine._check_autonomous_tasks()
        mgr.mark_running.assert_called_once_with("t1")
        brain.assert_called_once_with("check NVDA RSI")
        mgr.complete.assert_called_once_with("t1", "NVDA RSI is 38.")
        engine._deliver.assert_called_once()
        assert engine._deliver.call_args.kwargs.get("remote") is True

    def test_max_two_tasks_per_cycle(self, engine):
        engine._brain_fn = MagicMock(return_value="ok")
        due = [{"id": f"t{i}", "description": f"task {i}"} for i in range(4)]
        mgr = self._mgr(due)
        with patch("core.autonomous_tasks.AutonomousTaskManager", return_value=mgr):
            engine._check_autonomous_tasks()
        assert mgr.mark_running.call_count == 2
        assert mgr.complete.call_count == 2

    def test_brain_failure_marks_task_failed_not_completed(self, engine):
        engine._brain_fn = MagicMock(side_effect=RuntimeError("API down"))
        mgr = self._mgr([{"id": "t1", "description": "doomed task"}])
        with patch("core.autonomous_tasks.AutonomousTaskManager", return_value=mgr):
            engine._check_autonomous_tasks()
        mgr.fail.assert_called_once()
        assert mgr.fail.call_args.args[0] == "t1"
        mgr.complete.assert_not_called()
        engine._deliver.assert_not_called()

    def test_one_failed_task_does_not_block_the_next(self, engine):
        engine._brain_fn = MagicMock(side_effect=[RuntimeError("boom"), "second ok"])
        due = [
            {"id": "t1", "description": "fails"},
            {"id": "t2", "description": "succeeds"},
        ]
        mgr = self._mgr(due)
        with patch("core.autonomous_tasks.AutonomousTaskManager", return_value=mgr):
            engine._check_autonomous_tasks()
        mgr.fail.assert_called_once()
        mgr.complete.assert_called_once_with("t2", "second ok")

    def test_empty_brain_result_records_done(self, engine):
        engine._brain_fn = MagicMock(return_value="")
        mgr = self._mgr([{"id": "t1", "description": "quiet task"}])
        with patch("core.autonomous_tasks.AutonomousTaskManager", return_value=mgr):
            engine._check_autonomous_tasks()
        mgr.complete.assert_called_once_with("t1", "Done.")


class TestOAuthTokenCheck:
    def test_stale_token_triggers_warning(self, engine, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        token = tmp_path / "data" / "token_gmail.json"
        token.write_text("{}", encoding="utf-8")
        old = datetime.now().timestamp() - 6.5 * 86400
        os.utime(token, (old, old))
        engine._check_oauth_tokens()
        engine._deliver.assert_called_once()
        assert "token_gmail.json" in engine._deliver.call_args.args[0]

    def test_fresh_token_stays_quiet_and_resets_cooldown(self, engine, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "token_gmail.json").write_text("{}", encoding="utf-8")
        engine._check_oauth_tokens()
        engine._deliver.assert_not_called()
        assert "oauth_tokens" not in engine._state

    def test_missing_token_files_stay_quiet(self, engine, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        engine._check_oauth_tokens()
        engine._deliver.assert_not_called()


class TestSkillProposalCheck:
    def _miner(self, pending):
        miner = MagicMock()
        miner.pending.return_value = pending
        miner.mine.return_value = []
        return miner

    def test_one_pending_proposal_is_delivered(self, engine):
        p = {"id": "ab12", "example": "check nvda rsi", "count": 5, "days_seen": 4}
        with patch("core.skills.miner.HabitMiner", return_value=self._miner([p])):
            engine._check_skill_proposals()
        engine._deliver.assert_called_once()
        msg = engine._deliver.call_args.args[0]
        assert "check nvda rsi" in msg and "skill" in msg.lower()

    def test_only_first_proposal_delivered(self, engine):
        pending = [
            {"id": "a", "example": "first ask", "count": 3, "days_seen": 3},
            {"id": "b", "example": "second ask", "count": 3, "days_seen": 3},
        ]
        with patch("core.skills.miner.HabitMiner", return_value=self._miner(pending)):
            engine._check_skill_proposals()
        assert engine._deliver.call_count == 1
        assert "second ask" not in engine._deliver.call_args.args[0]

    def test_no_pending_stays_quiet_and_resets_cooldown(self, engine):
        with patch("core.skills.miner.HabitMiner", return_value=self._miner([])):
            engine._check_skill_proposals()
        engine._deliver.assert_not_called()
        assert "skill_proposals" not in engine._state

    def test_cooldown_blocks_second_delivery_same_day(self, engine):
        p = {"id": "ab12", "example": "check nvda rsi", "count": 5, "days_seen": 4}
        with patch("core.skills.miner.HabitMiner", return_value=self._miner([p])):
            engine._check_skill_proposals()
            engine._check_skill_proposals()
        assert engine._deliver.call_count == 1

    def test_miner_error_does_not_raise(self, engine):
        with patch("core.skills.miner.HabitMiner", side_effect=RuntimeError("io")):
            engine._check_skill_proposals()  # must not raise
        engine._deliver.assert_not_called()

    def test_runs_in_morning_window(self, engine):
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 9, 0))
        mocks["_check_skill_proposals"].assert_called_once()


class TestNewCheckWindows:
    def test_oauth_check_runs_in_morning_window(self, engine):
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 8, 0))
        mocks["_check_oauth_tokens"].assert_called_once()


class TestHudNotify:
    def test_no_hud_fn_is_silent(self, engine):
        engine._hud_notify(5, "a", "b", "c", "TAG")  # must not raise

    def test_hud_fn_receives_args(self, engine):
        fn = MagicMock()
        engine.set_hud_notify(fn)
        engine._hud_notify(5, "pre", "hl", "suf", "TAG")
        fn.assert_called_once_with(5, "pre", "hl", "suf", "TAG")

    def test_hud_fn_exception_swallowed(self, engine):
        engine.set_hud_notify(MagicMock(side_effect=RuntimeError("js bridge gone")))
        engine._hud_notify(5, "a", "b", "c", "TAG")  # must not raise


class TestTranscriptionQuality:
    def _write_day(self, tmp_path, contents):
        convo_dir = tmp_path / "data" / "conversations"
        convo_dir.mkdir(parents=True)
        from datetime import date
        fpath = convo_dir / f"{date.today().isoformat()}.jsonl"
        lines = [json.dumps({"role": "user", "content": c}, ensure_ascii=False)
                 for c in contents]
        fpath.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_high_garbage_rate_alerts(self, engine, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # 2 of 6 turns (33%) in scripts Mo doesn't speak
        self._write_day(tmp_path, [
            "what is the weather", "hello", "sabah el kheir",
            "check my email", "여러분들과의 바세사", "Það er um þig",
        ])
        engine._check_transcription_quality()
        assert engine._deliver.call_count == 1
        assert "gibberish" in engine._deliver.call_args.args[0]

    def test_clean_day_stays_quiet_and_resets(self, engine, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_day(tmp_path, [
            "what is the weather", "hello", "sabah el kheir",
            "check my email", "what is on my calendar",
        ])
        engine._check_transcription_quality()
        engine._deliver.assert_not_called()
        assert "transcription_quality" not in engine._state

    def test_too_few_turns_stays_quiet(self, engine, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_day(tmp_path, ["Það er um þig", "여러분들과의"])  # 100% but n=2
        engine._check_transcription_quality()
        engine._deliver.assert_not_called()

    def test_missing_log_file_stays_quiet(self, engine, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        engine._check_transcription_quality()
        engine._deliver.assert_not_called()

    def test_cooldown_blocks_second_alert(self, engine, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_day(tmp_path, ["Það", "Það", "Það", "Það", "Það", "ok"])
        engine._check_transcription_quality()
        engine._check_transcription_quality()
        assert engine._deliver.call_count == 1

    def test_runs_in_evening_window(self, engine):
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 20, 0))
        mocks["_check_transcription_quality"].assert_called_once()

    def test_not_run_midday(self, engine):
        mocks = _patch_all_checks(engine)
        _run_checks_at(engine, datetime(2026, 7, 1, 12, 0))
        mocks["_check_transcription_quality"].assert_not_called()
