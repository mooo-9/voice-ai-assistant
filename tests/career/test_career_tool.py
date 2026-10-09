from unittest.mock import patch

import pytest

from core.career import pipeline, profile, store
from tools import career_tool

_TOOLS = ["prepare_applications", "review_applications", "approve_applications",
          "application_status", "check_application_replies", "import_cv",
          "set_application_answer", "application_settings", "interview_prep"]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    import core.autonomous_tasks as at
    monkeypatch.setattr(store, "DIR", tmp_path / "career")
    monkeypatch.setattr(at, "_TASKS_PATH", tmp_path / "tasks.json")


class TestSettings:
    def test_live_mode_needs_a_cv(self):
        assert career_tool.application_settings(live=True).startswith("Error: import your CV")
        assert store.settings()["live"] is False

    def test_live_mode_with_a_cv(self, tmp_path):
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        profile.save({"cv_path": str(cv)})
        assert career_tool.application_settings(live=True).startswith("Mode: LIVE")

    def test_with_no_input_it_only_shows(self):
        out = career_tool.application_settings()
        assert out.startswith("Mode: practice")

    def test_the_job_hunt_never_schedules_itself(self):
        """Mo starts each hunt himself, from the AUTOMATIONS panel or by asking."""
        from core.brain import TOOLS
        [spec] = [t for t in TOOLS if t["name"] == "application_settings"]
        assert "nightly" not in spec["input_schema"]["properties"]


class TestTonight:
    """The AUTOMATIONS button arms one hunt for 02:00; pressed again, it's off."""

    def test_arms_one_hunt_at_2am_and_a_second_press_cancels_it(self):
        from core import scheduler
        assert career_tool.hunt_tonight().startswith("Job hunt set for tonight at 2 AM")
        [job] = scheduler._load_schedules()
        assert job["id"] == career_tool.HUNT_JOB_ID
        assert job["trigger"]["type"] == "date"               # once, never a timer
        assert job["trigger"]["run_date"][11:] == "02:00:00"
        assert career_tool.hunt_armed()
        assert career_tool.hunt_tonight() == "Tonight's job hunt is off."
        assert scheduler._load_schedules() == [] and not career_tool.hunt_armed()

    def test_a_night_el_fager_slept_through_does_not_stay_armed(self):
        from core import scheduler
        scheduler._save_schedules([{"id": career_tool.HUNT_JOB_ID, "enabled": True,
                                    "trigger": {"type": "date", "run_date": "2026-01-01T02:00:00"}}])
        assert not career_tool.hunt_armed()
        assert career_tool.hunt_tonight().startswith("Job hunt set")     # press arms again
        assert career_tool.hunt_armed()

    def test_a_hunt_the_budget_cant_cover_is_not_armed(self):
        from core import scheduler
        TestPrepare._spent_this_month(10)
        assert career_tool.hunt_tonight().startswith("Not starting the job hunt")
        assert scheduler._load_schedules() == []

    def test_at_2am_it_checks_replies_then_hunts_and_says_nothing(self):
        from core import scheduler
        calls = []
        with patch.object(career_tool, "check_application_replies",
                          side_effect=lambda: calls.append("replies")),              patch.object(career_tool, "prepare_applications",
                          side_effect=lambda: calls.append("hunt") or "Preparing today's batch"),              patch.object(pipeline, "_notify") as notify:
            # Through the scheduler, as the armed job fires.
            assert scheduler._dispatch_scheduled_tool("run_job_hunt", {}) == ""
        assert calls == ["replies", "hunt"]
        notify.assert_not_called()

    def test_a_hunt_that_cannot_start_at_2am_tells_his_phone(self):
        with patch.object(career_tool, "check_application_replies", side_effect=RuntimeError),              patch.object(career_tool, "prepare_applications", return_value="Not starting: budget."),              patch.object(pipeline, "_notify") as notify:
            assert career_tool.run_job_hunt() == ""
        notify.assert_called_once_with("Not starting: budget.")


class TestPrepare:
    def test_runs_in_the_background_once(self):
        with patch.object(pipeline, "start_in_background", side_effect=[True, False]):
            assert career_tool.prepare_applications().startswith("Preparing today's batch")
            assert career_tool.prepare_applications().startswith("The pipeline is already running")

    @staticmethod
    def _spent_this_month(usd):
        import json
        from datetime import datetime
        from core import telemetry
        telemetry._TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        (telemetry._TELEMETRY_DIR / f"{datetime.now():%Y-%m}-01.jsonl").write_text(
            json.dumps({"source": "chat", "cost_usd": usd}) + "\n", encoding="utf-8")

    def test_a_run_the_budget_cant_cover_does_not_start(self):
        run = pipeline.run_estimate()
        self._spent_this_month(10 - run / 2)     # half a run left
        with patch.object(pipeline, "start_in_background") as start:
            out = career_tool.prepare_applications()
        start.assert_not_called()
        assert out.startswith("Not starting the job hunt")
        assert f"${10 - run / 2:.2f} of the $10.00" in out and f"about ${run:.2f}" in out

    def test_a_lower_daily_target_fits_what_is_left(self):
        self._spent_this_month(10 - pipeline.run_estimate() / 2)
        store.update_settings(daily_target=store.settings()["daily_target"] // 4)
        with patch.object(pipeline, "start_in_background", return_value=True) as start:
            assert career_tool.prepare_applications().startswith("Preparing today's batch")
        start.assert_called_once()


class TestInterviewPrep:
    def test_uses_the_tracked_posting_and_research(self):
        from core.career import tracker
        tracker.add({"url": "https://x/1", "title": "Technology Consultant", "company": "EY",
                     "company_key": "EY", "status": "interview", "description": "SQL, Excel"})
        with patch("core.agents.research_agent.ResearchAgent.run", return_value="EY uses HireVue"), \
             patch("core.career.claude.ask", return_value="PREP SHEET") as ask:
            assert career_tool.interview_prep("EY") == "PREP SHEET"
        prompt = ask.call_args.args[0]
        assert "Role: Technology Consultant" in prompt
        assert "SQL, Excel" in prompt and "EY uses HireVue" in prompt


class TestBrainWiring:
    def test_every_tool_is_defined_and_in_the_jobs_group(self):
        from core.brain import _SLIM_TOOLS, _TOOL_GROUP_NAMES
        names = {t["name"] for t in _SLIM_TOOLS}
        assert set(_TOOLS) <= names
        assert set(_TOOLS) <= _TOOL_GROUP_NAMES["jobs"]

    @pytest.mark.parametrize("message", [
        "approve my applications", "how are my job applications going",
        "import my cv from the desktop", "prepare me for my interview at PwC",
        "my military status is exempted", "run the job hunt",
    ])
    def test_career_talk_offers_the_tools(self, message):
        from core.brain import _select_tools
        assert set(_TOOLS) <= {t["name"] for t in _select_tools(message)}

    @pytest.mark.parametrize("name,args", [
        ("application_status", {}),
        ("approve_applications", {"skip": ["a1"]}),
        ("set_application_answer", {"question": "gpa", "answer": "3.4"}),
        ("interview_prep", {"company": "PwC", "role": "Graduate"}),
    ])
    def test_dispatch_reaches_the_tool(self, name, args):
        from core.brain import Brain
        with patch.object(career_tool, name, return_value="ok") as fn:
            assert Brain(profile={})._dispatch_tool(name, args) == "ok"
        fn.assert_called_once_with(**args)


class TestOnlyMoApproves:
    """Approving sends a whole batch under Mo's name. A job hunt may run as a
    background brain turn: it may prepare the batch, never approve it."""

    def _turn(self, history):
        from tests.test_staged_confirm_by_voice import _end, _run_turn, _tool_use
        with patch.object(career_tool, "approve_applications", return_value="Approved 3") as fn:
            results, _ = _run_turn(__import__("core.brain", fromlist=["Brain"]).Brain(profile={}),
                                   "approve them", [_tool_use("approve_applications"), _end()],
                                   history=history)
        return fn, results

    def test_a_background_turn_cannot_approve(self):
        fn, results = self._turn(history=[])
        fn.assert_not_called()
        assert "NOT APPROVED" in results[-1]

    def test_mos_own_turn_can(self):
        fn, results = self._turn(history=None)
        fn.assert_called_once()
        assert results[-1] == "Approved 3"
