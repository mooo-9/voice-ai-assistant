"""Tests for brain API metering, usage_report tool, and the budget alert."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from unittest.mock import MagicMock, patch

import pytest

import core.telemetry as tel
import tools.usage_tool as ut


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tel, "_TELEMETRY_DIR", tmp_path / "telemetry")
    yield tmp_path


def _make_brain():
    from core.brain import Brain
    return Brain(profile={})


def _end_turn_response(text="ok"):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    response.usage = MagicMock(
        input_tokens=1000, output_tokens=200,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return response


class TestBrainMetering:
    def test_chat_records_telemetry(self):
        brain = _make_brain()
        with patch.object(brain.client.messages, "create",
                          return_value=_end_turn_response()):
            brain.chat("hello")
        s = tel.summarize(days=1)
        assert s["requests"] == 1
        assert s["by_source"]["chat"]["requests"] == 1
        assert s["cost_usd"] > 0

    def test_telemetry_failure_does_not_break_chat(self):
        brain = _make_brain()
        with patch.object(brain.client.messages, "create",
                          return_value=_end_turn_response("still fine")), \
             patch("core.telemetry.record_api_usage",
                   side_effect=RuntimeError("disk full")):
            result = brain.chat("hello")
        assert result == "still fine"

    def test_usage_report_tool_dispatches(self):
        brain = _make_brain()
        out = brain._dispatch_tool("usage_report", {"days": 1})
        assert "No API usage" in out or "API calls" in out


class TestUsageReport:
    def test_empty(self):
        assert "No API usage recorded" in ut.usage_report(days=1)

    def test_report_contains_cost_and_breakdown(self):
        tel.record_api_usage("chat", "claude-opus-4-8", MagicMock(
            input_tokens=100_000, output_tokens=20_000,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ), 1500.0)
        out = ut.usage_report(days=1)
        assert "$" in out
        assert "chat" in out
        assert "1 API calls" in out

    def test_week_wording(self):
        assert "last 7 days" in ut.usage_report(days=7).lower()

    def test_job_hunt_spend_today_and_since_it_started(self, isolated_dir):
        old = isolated_dir / "telemetry" / "2026-01-01.jsonl"
        old.parent.mkdir(parents=True)
        old.write_text(json.dumps({"source": "career", "cost_usd": 0.5}) + "\n"
                       + json.dumps({"source": "chat", "cost_usd": 3.0}) + "\n",
                       encoding="utf-8")
        # Turn profiles share the folder; they are not API spend.
        (old.parent / "turns-2026-01-01.jsonl").write_text(
            json.dumps({"source": "career", "cost_usd": 7.0}) + "\n", encoding="utf-8")
        tel.record_api_usage("career", "claude-sonnet-5", MagicMock(
            input_tokens=100_000, output_tokens=20_000,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ), 1500.0)
        assert "Job hunt: $0.40 today, $0.90 since it started." in ut.usage_report(days=1)

    def test_job_hunt_total_shows_on_a_quiet_day(self, isolated_dir):
        old = isolated_dir / "telemetry" / "2026-01-01.jsonl"
        old.parent.mkdir(parents=True)
        old.write_text(json.dumps({"source": "career", "cost_usd": 0.5}) + "\n",
                       encoding="utf-8")
        out = ut.usage_report(days=1)
        assert "No API usage recorded" in out
        assert "Job hunt: $0.00 today, $0.50 since it started." in out

    def test_no_job_hunt_line_before_it_ever_ran(self):
        tel.record_api_usage("chat", "claude-sonnet-5", MagicMock(
            input_tokens=10, output_tokens=10,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ), 100.0)
        assert "Job hunt" not in ut.usage_report(days=1)

    def test_asking_what_the_job_hunt_cost_offers_the_report(self):
        from core.brain import _select_tools
        for ask in ("how much has the job hunt cost", "what did the job search cost me"):
            assert "usage_report" in {t["name"] for t in _select_tools(ask)}


def _spent_this_month(usd, source="chat"):
    """A day of this month's telemetry costing `usd`."""
    from datetime import datetime
    tel._TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    day = tel._TELEMETRY_DIR / f"{datetime.now():%Y-%m}-01.jsonl"
    with day.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"source": source, "cost_usd": usd}) + "\n")


class TestMonthlyBudget:
    def test_default_is_ten_dollars(self):
        assert tel.monthly_budget() == 10.0

    def test_only_this_month_counts(self):
        tel._TELEMETRY_DIR.mkdir(parents=True)
        (tel._TELEMETRY_DIR / "2000-01-31.jsonl").write_text(
            json.dumps({"source": "chat", "cost_usd": 50.0}) + "\n", encoding="utf-8")
        _spent_this_month(1.25)
        assert tel.cost_this_month() == 1.25
        tel.check_budget()                       # $1.25 of $10: carries on

    def test_stops_once_the_budget_is_used_up(self):
        _spent_this_month(10.0)
        with pytest.raises(tel.BudgetExceeded):
            tel.check_budget()

    def test_budget_from_settings_and_zero_turns_it_off(self):
        _spent_this_month(3.0)
        tel._SETTINGS_FILE.write_text(json.dumps({"api_monthly_budget_usd": 2.0}),
                                      encoding="utf-8")
        with pytest.raises(tel.BudgetExceeded):
            tel.check_budget()
        tel._SETTINGS_FILE.write_text(json.dumps({"api_monthly_budget_usd": 0}),
                                      encoding="utf-8")
        tel.check_budget()

    def test_agents_and_job_hunt_send_nothing_over_budget(self):
        from types import SimpleNamespace
        create = MagicMock()
        client = tel.instrument_client(
            SimpleNamespace(messages=SimpleNamespace(create=create)), "career")
        _spent_this_month(10.0)
        with pytest.raises(tel.BudgetExceeded):
            client.messages.create(model="claude-sonnet-5", messages=[])
        create.assert_not_called()

    def test_voice_chat_moves_to_the_local_model_over_budget(self):
        brain = _make_brain()
        _spent_this_month(10.0)
        with patch.object(brain.client.messages, "create") as create, \
             patch("core.local_llm.local_chat", return_value="local answer"):
            assert brain.chat("hello") == "local answer"
        create.assert_not_called()

    def test_voice_chat_uses_claude_under_budget(self):
        brain = _make_brain()
        _spent_this_month(4.0)
        with patch.object(brain.client.messages, "create",
                          return_value=_end_turn_response("from claude")):
            assert brain.chat("hello") == "from claude"

    def test_report_shows_the_month_against_the_budget(self):
        _spent_this_month(4.2)
        assert "This month: $4.20 of the $10.00 budget used." in ut.usage_report(days=1)


class TestBudgetAlert:
    def _engine(self, tmp_path, monkeypatch):
        import core.proactive as pa
        from core.proactive import ProactiveEngine
        monkeypatch.setattr(pa, "_STATE_FILE", tmp_path / "state.json")
        monkeypatch.chdir(tmp_path)
        e = ProactiveEngine(speak_fn=None)
        e._deliver = MagicMock()
        return e

    def test_under_eighty_percent_silent(self, tmp_path, monkeypatch):
        engine = self._engine(tmp_path, monkeypatch)
        _spent_this_month(7.99)
        engine._check_api_budget()
        engine._deliver.assert_not_called()

    def test_eighty_percent_warns_once_a_month(self, tmp_path, monkeypatch):
        engine = self._engine(tmp_path, monkeypatch)
        _spent_this_month(8.5)
        engine._check_api_budget()
        engine._check_api_budget()
        engine._deliver.assert_called_once()
        assert "$8.50 of this month's $10.00" in engine._deliver.call_args.args[0]

    def test_used_up_says_so_once(self, tmp_path, monkeypatch):
        engine = self._engine(tmp_path, monkeypatch)
        _spent_this_month(8.5)
        engine._check_api_budget()               # the 80% warning
        _spent_this_month(1.6)
        engine._check_api_budget()
        engine._check_api_budget()
        assert engine._deliver.call_count == 2
        said = engine._deliver.call_args.args[0]
        assert "used up" in said and "local model" in said

    def test_zero_budget_disables(self, tmp_path, monkeypatch):
        engine = self._engine(tmp_path, monkeypatch)
        tel._SETTINGS_FILE.write_text(json.dumps({"api_monthly_budget_usd": 0}),
                                      encoding="utf-8")
        _spent_this_month(99.0)
        engine._check_api_budget()
        engine._deliver.assert_not_called()

    def test_checked_all_day_not_just_evenings(self, tmp_path, monkeypatch):
        engine = self._engine(tmp_path, monkeypatch)
        from datetime import datetime
        import core.proactive as pa
        morning = datetime(2026, 9, 27, 9, 0)
        monkeypatch.setattr(pa, "datetime", MagicMock(now=MagicMock(return_value=morning)))
        with patch.object(engine, "_check_api_budget") as check:
            for name in [n for n in dir(engine) if n.startswith("_check_") and n != "_check_api_budget"]:
                monkeypatch.setattr(engine, name, MagicMock())
            engine._run_checks()
        check.assert_called_once()
