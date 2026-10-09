"""Mo's main goal is an interview at one of the biggest companies in Cairo:
graduate programme deadlines, referrals, and the job hunt raised first."""
import json
import threading
import urllib.request
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest

from core.career import companies, focus, profile, programmes, referrals, store, tracker


@pytest.fixture(autouse=True)
def _career_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DIR", tmp_path / "career")


def _in(days):
    return (date.today() + timedelta(days=days)).isoformat()


def _programme(pid, status="open", deadline="", fresh="yes", tier="big4"):
    return {"id": pid, "name": f"{pid} programme", "company": "PwC", "tier": tier,
            "url": f"https://x/{pid}", "status": status, "opens": "", "deadline": deadline,
            "fresh_grads": fresh, "eligibility": "Graduates of 2025-2026", "how_to_apply": "Online",
            "checked_at": "2026-09-25T02:00:00"}


class TestProgrammes:
    def test_the_seed_list_is_the_big4_and_top_companies(self):
        seeds = programmes.seeds()
        assert {p["company"] for p in seeds if p["tier"] == "big4"} == {"PwC", "Deloitte", "EY", "KPMG"}
        assert all(p["url"].startswith("https://") for p in seeds)

    def test_a_check_reads_each_page_and_reports_what_opened(self, monkeypatch):
        monkeypatch.setattr(programmes, "seeds", lambda: [
            {"id": "ey", "name": "EY graduates", "company": "EY", "tier": "big4", "url": "https://ey"},
            {"id": "kpmg", "name": "KPMG graduates", "company": "KPMG", "tier": "big4", "url": "https://kpmg"}])
        found = {"https://ey": {"status": "open", "opens": "", "deadline": _in(10), "fresh_grads": "yes",
                                "eligibility": "", "how_to_apply": ""}}
        with patch("tools.web_tool.fetch_page",
                   side_effect=lambda url, max_chars: "[fetch failed: blocked]" if "kpmg" in url else "page text"), \
             patch("core.career.claude.ask", side_effect=lambda prompt, **kw: found["https://ey"]):
            opened = programmes.check_all()
        assert opened == f"EY graduates is OPEN, closes {_in(10)}"
        state = programmes.state()
        assert state["kpmg"]["status"] == "unknown"
        # Open already: a second check doesn't announce it again.
        with patch("tools.web_tool.fetch_page", return_value="page text"), \
             patch("core.career.claude.ask", return_value=found["https://ey"]):
            assert "EY" not in programmes.check_all()

    def test_closing_soon_is_open_within_a_week_and_open_to_graduates(self):
        store.save("programmes.json", {
            "a": _programme("a", deadline=_in(3)),
            "b": _programme("b", deadline=_in(20)),
            "c": _programme("c", deadline=_in(2), fresh="no"),
            "d": _programme("d", status="closed", deadline=_in(1)),
            "e": _programme("e", deadline=_in(-1)),
        })
        assert [p["id"] for p in programmes.closing_soon()] == ["a"]


class TestReferrals:
    def _results(self):
        return [
            {"title": "Ahmed Hassan - Senior Associate - PwC Middle East | LinkedIn",
             "href": "https://eg.linkedin.com/in/ahmed-hassan-123", "body": "Helwan University. PwC."},
            {"title": "Sara Ali - Data Analyst - Fawry | LinkedIn",
             "href": "https://eg.linkedin.com/in/sara-ali", "body": "Ex-PwC intern"},
            {"title": "Mona Adel - Consultant at Deloitte, ex-PwC | LinkedIn",
             "href": "https://www.linkedin.com/in/mona-adel", "body": "Cairo"},
            {"title": "PwC Middle East | LinkedIn", "href": "https://www.linkedin.com/company/pwc"},
        ]

    def test_finds_people_at_the_firm_and_flags_alumni(self):
        with patch("ddgs.DDGS") as DDGS:
            DDGS.return_value.__enter__.return_value.text.return_value = iter(self._results())
            people = referrals.search_people("PwC", "Helwan University")
        assert [p["name"] for p in people] == ["Ahmed Hassan", "Sara Ali", "Mona Adel"]
        assert people[0]["alumni"] is True and people[1]["alumni"] is False
        assert people[0]["url"] == "https://www.linkedin.com/in/ahmed-hassan-123"
        query = DDGS.return_value.__enter__.return_value.text.call_args.args[0]
        assert query == 'site:linkedin.com/in "pwc" Cairo "Helwan University"'

    def test_find_drafts_new_people_once_with_the_role_in_play(self):
        tracker.add({"url": "https://pwc/1", "title": "Technology Consulting Graduate",
                     "company": "PwC Middle East", "company_key": "PwC", "tier": "big4",
                     "status": "ready"})
        person = {"name": "Ahmed Hassan", "headline": "Associate", "url": "https://www.linkedin.com/in/a",
                  "company": "PwC", "alumni": True}
        with patch.object(referrals, "search_people", return_value=[person]), \
             patch("core.career.claude.ask", return_value={"note": "N" * 400, "message": "M"}) as ask:
            out = referrals.find("PwC", count=5)
            again = referrals.find("PwC", count=5)
        assert out.startswith("1 people to ask for a referral: Ahmed Hassan (PwC)")
        assert again == "No new people found to ask for a referral."
        (ref,) = referrals.to_send()
        assert ref["role"] == "Technology Consulting Graduate"
        assert len(ref["note"]) == 200                   # LinkedIn's free-account cap
        assert "went to the same university" in ask.call_args.args[0]

    def test_companies_with_applications_in_play_come_first(self):
        tracker.add({"url": "https://v/1", "title": "Analyst", "company_key": "Valeo", "tier": "top",
                     "status": "applied"})
        order = referrals._companies_in_play()
        assert order[0] == "Valeo"
        assert order[1:5] == ["Deloitte", "PwC", "EY", "KPMG"]

    def test_connections_export_keeps_only_target_companies(self, tmp_path):
        csv_file = tmp_path / "Connections.csv"
        csv_file.write_text(
            "Notes:\n\"When exporting your connection data, you may notice...\"\n\n"
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            "Sara,Ali,https://www.linkedin.com/in/sara,,PwC Middle East,Associate,01 Sep 2026\n"
            "Omar,Adel,https://www.linkedin.com/in/omar,,Some Startup,CEO,02 Sep 2026\n",
            encoding="utf-8")
        out = referrals.import_connections(str(csv_file))
        assert out.startswith("1 of your connections work at target companies: PwC.")
        assert [p["name"] for p in referrals.connections_at("PwC")] == ["Sara Ali"]
        assert referrals.import_connections(str(tmp_path / "nope.csv")).startswith("Error")

    def test_without_a_path_the_export_in_downloads_is_found_even_zipped(
            self, monkeypatch, tmp_path):
        """Mo says 'import my LinkedIn connections'; the file LinkedIn emailed
        him is a zip in Downloads."""
        import zipfile
        monkeypatch.setattr(referrals, "DOWNLOADS", tmp_path)
        assert referrals.import_connections().startswith("No LinkedIn export in Downloads")
        with zipfile.ZipFile(tmp_path / "Basic_LinkedInDataExport_09-30-2026.zip", "w") as z:
            z.writestr("Connections.csv",
                       "Notes:\n\nFirst Name,Last Name,URL,Email Address,Company,Position,"
                       "Connected On\nSara,Ali,https://www.linkedin.com/in/sara,,KPMG Egypt,"
                       "Auditor,01 Sep 2026\n")
        from tools import career_tool
        assert career_tool.import_linkedin_connections().startswith(
            "1 of your connections work at target companies: KPMG.")

    def test_a_connection_is_asked_before_strangers_and_needs_no_note(self):
        store.save("connections.json", [{"name": "Sara Ali", "headline": "Associate",
                                          "url": "https://www.linkedin.com/in/sara",
                                          "company": "PwC", "alumni": False, "connected": True}])
        stranger = {"name": "Ahmed Hassan", "headline": "Associate",
                    "url": "https://www.linkedin.com/in/a", "company": "PwC", "alumni": True}
        with patch.object(referrals, "search_people", return_value=[stranger]), \
             patch("core.career.claude.ask", return_value={"note": "N", "message": "M"}) as ask:
            out = referrals.find("PwC", count=1)
        assert out.startswith("1 people to ask for a referral: Sara Ali (PwC)")
        (ref,) = referrals.to_send()
        assert ref["note"] == "" and ref["connected"]
        assert "already one of his connections" in ask.call_args.args[0]

    def test_mark(self):
        store.save("referrals.json", {"r1": {"id": "r1", "name": "Ahmed", "company": "PwC",
                                              "status": "to_send", "found_at": "x"}})
        assert referrals.mark("r1", "sent") == "Ahmed (PwC): sent."
        assert referrals.to_send() == []
        assert referrals.mark("r1", "hired").startswith("Error")


class TestFocus:
    def test_raises_what_is_waiting_on_mo(self):
        tracker.add({"url": "u1", "title": "A", "company": "EY", "status": "interview"})
        tracker.add({"url": "u2", "title": "B", "company": "Valeo", "status": "ready"})
        store.save("programmes.json", {"a": _programme("a", deadline=_in(4))})
        line = focus.status_line()
        assert line.startswith("JOB HUNT -- Mo's main goal, raise it first: 1 interview(s): EY; "
                               "1 applications waiting for his review; a programme closes in 4 days")
        assert "his CV isn't imported yet" in line

    def test_it_reaches_every_brain_turn(self):
        from core.brain import Brain
        text = Brain(profile={})._build_system()[-1]["text"]
        assert "JOB HUNT -- Mo's main goal" in text

    def test_the_persona_knows_he_graduated_and_the_goal(self):
        from core.brain import SYSTEM_PROMPT
        assert "Business Informatics graduate" in SYSTEM_PROMPT
        assert "interview at one of the biggest companies in Cairo" in SYSTEM_PROMPT
        assert "student" not in SYSTEM_PROMPT.split("Personality:")[0]


class TestFollowUps:
    """A sent application with no answer is followed up a week later, once
    more a week after that, then let go (career-ops' cadence)."""
    def _sent(self, url, days_ago, **fields):
        at = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
        return tracker.add({"url": url, "title": "Analyst", "company": "Valeo",
                            "status": "applied", "applied_at": at, **fields})

    def test_due_a_week_after_sending(self):
        old = self._sent("u1", 8)
        self._sent("u2", 3)
        tracker.add({"url": "u3", "title": "B", "company": "EY", "status": "interview",
                     "applied_at": "2026-01-01T00:00:00"})
        assert [a["id"] for a in tracker.follow_ups_due()] == [old["id"]]

    def test_once_more_a_week_later_then_never(self):
        app = self._sent("u1", 30)
        tracker.mark_followed_up(app["id"])
        assert tracker.follow_ups_due() == []
        later = datetime.now() + timedelta(days=8)
        assert [a["id"] for a in tracker.follow_ups_due(later)] == [app["id"]]
        tracker.mark_followed_up(app["id"])
        assert tracker.follow_ups_due(datetime.now() + timedelta(days=60)) == []

    def test_raised_in_status_and_every_turn(self):
        app = self._sent("u1", 8, hr_email="hr@valeo.com")
        from core.career import pipeline
        assert f"{app['id']}: Analyst at Valeo (email hr@valeo.com)" in pipeline.status_text()
        assert "1 applications due a follow-up" in focus.status_line()

    def test_mark_tool(self):
        from tools import career_tool
        app = self._sent("u1", 8)
        assert career_tool.mark_followed_up(app["id"]).endswith("due in 7 days.")
        assert career_tool.mark_followed_up("nope").startswith("Error")


class TestMorningNudge:
    def _engine(self, monkeypatch, tmp_path):
        import core.proactive as pro
        monkeypatch.setattr(pro, "_STATE_FILE", tmp_path / "state.json")
        said, remote = [], []
        engine = pro.ProactiveEngine(speak_fn=said.append)
        engine._deliver = lambda text, remote_=False, **kw: (said.append(text),
                                                              remote.append(kw.get("remote", remote_)))
        return engine, said, remote

    def test_says_what_is_waiting_once_a_morning(self, monkeypatch, tmp_path):
        engine, said, remote = self._engine(monkeypatch, tmp_path)
        tracker.add({"url": "u", "title": "A"})
        tracker.update(tracker.job_id("u"), "ready", "drafted")
        # One he left unsent days ago stays saved, not nagged about.
        tracker.add({"url": "old", "title": "B", "status": "ready", "events": [
            {"at": "2026-01-01T02:00:00", "status": "ready", "note": "drafted"}]})
        store.save("programmes.json", {"a": _programme("a", deadline=_in(2))})
        engine._check_job_hunt()
        engine._check_job_hunt()
        assert said == ["Mo, 1 new job applications are ready for your review; "
                        "a programme closes in 2 days."]
        assert remote == [True]

    def test_follow_ups_due_are_said_too(self, monkeypatch, tmp_path):
        engine, said, _ = self._engine(monkeypatch, tmp_path)
        tracker.add({"url": "u", "title": "A", "company": "Valeo", "status": "applied",
                     "applied_at": (datetime.now() - timedelta(days=8)).isoformat()})
        engine._check_job_hunt()
        assert said == ["Mo, 1 applications are due a follow-up: Valeo."]

    def test_quiet_when_nothing_waits(self, monkeypatch, tmp_path):
        engine, said, _ = self._engine(monkeypatch, tmp_path)
        engine._check_job_hunt()
        assert said == []


class TestTools:
    def test_programmes_tool_and_referral_tools_are_wired(self):
        from core.brain import _SLIM_TOOLS, _TOOL_GROUP_NAMES, _select_tools
        new = {"graduate_programmes", "find_referrals", "referral_list", "mark_referral",
               "import_linkedin_connections", "mark_followed_up", "evaluate_job",
               "skill_gaps", "retry_applications"}
        assert new <= {t["name"] for t in _SLIM_TOOLS}
        assert new <= _TOOL_GROUP_NAMES["jobs"]
        assert "application_status" in {t["name"] for t in _select_tools("daily briefing please")}
        assert new <= {t["name"] for t in _select_tools("who can give me a referral at PwC")}

    @pytest.mark.parametrize("message", [
        "is this job worth it https://www.linkedin.com/jobs/view/1", "what should I learn",
        "what skills am I missing", "import my linkedin connections from my downloads",
        "I followed up with Valeo", "who can refer me at KPMG", "any AI engineer openings",
        "find me SAP jobs"])
    def test_how_mo_asks_reaches_the_job_tools(self, message):
        """Live, 'what should I learn' and 'I followed up with Valeo' loaded no
        job tool, so El Fager couldn't act on them."""
        from core.brain import _select_tools
        assert "evaluate_job" in {t["name"] for t in _select_tools(message)}

    def test_check_the_job_i_copied(self):
        """Mo can't say a link aloud: the one he copied is judged."""
        from tools import career_tool
        with patch("tools.clipboard_tool.get_clipboard_text",
                   return_value="look https://www.linkedin.com/jobs/view/42 thanks"), \
             patch("core.career.pipeline.evaluate_one", return_value="ok") as ev:
            assert career_tool.evaluate_job() == "ok"
        ev.assert_called_once_with("https://www.linkedin.com/jobs/view/42")
        with patch("tools.clipboard_tool.get_clipboard_text", return_value="[Clipboard is empty]"):
            assert career_tool.evaluate_job().startswith("No job link given or copied")

    def test_mark_referral_dispatch(self):
        from core.brain import Brain
        from tools import career_tool
        with patch.object(career_tool, "mark_referral", return_value="ok") as fn:
            Brain(profile={})._dispatch_tool("mark_referral", {"referral_id": "r1", "status": "sent"})
        fn.assert_called_once_with(referral_id="r1", status="sent")


class TestReviewPageReferrals:
    @pytest.fixture
    def server(self, tmp_path, monkeypatch):
        import core.dashboard as db
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "settings.json").write_text(json.dumps({"dashboard_token": "k"}))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), db._Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{srv.server_address[1]}"
        srv.shutdown()

    def test_referrals_and_deadlines_reach_the_page_and_can_be_marked(self, server):
        store.save("referrals.json", {"r1": {
            "id": "r1", "name": "Ahmed", "headline": "Associate", "company": "PwC",
            "url": "https://www.linkedin.com/in/a", "alumni": True, "role": "", "note": "Hi",
            "message": "Could you refer me", "status": "to_send", "found_at": "x"}})
        store.save("programmes.json", {"a": _programme("a", deadline=_in(5))})
        req = urllib.request.Request(f"{server}/api/jobs", headers={"Authorization": "Bearer k"})
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert data["referrals"][0]["name"] == "Ahmed"
        assert data["closing"] == [{"name": "a programme", "days": 5, "url": "https://x/a"}]

        req = urllib.request.Request(f"{server}/api/referral_mark", method="POST",
                                     data=json.dumps({"id": "r1", "status": "sent"}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer k"})
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())["ok"] is True
        assert referrals.to_send() == []

    def test_a_form_question_is_answered_on_the_page_and_sent_again(self, server):
        """Mo answers from his phone; the application that stopped goes again."""
        from core.career import pipeline
        tracker.add({"url": "https://v.com/1", "title": "Analyst", "company": "Valeo",
                     "status": "approved", "channel": "site"})
        tracker.update(tracker.job_id("https://v.com/1"), "needs_you", "BLOCKED: Driving licence?")
        req = urllib.request.Request(f"{server}/api/jobs", headers={"Authorization": "Bearer k"})
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert data["waiting"] == [{"id": tracker.job_id("https://v.com/1"), "title": "Analyst",
                                    "company": "Valeo", "question": "Driving licence?"}]
        req = urllib.request.Request(f"{server}/api/jobs_answer", method="POST",
                                     data=json.dumps({"question": "Driving licence?",
                                                      "answer": "Yes"}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer k"})
        with patch.object(pipeline, "start_in_background", return_value=True):
            out = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert out["ok"] and out["result"].endswith("Retrying 1 application(s) now.")
        assert profile.load()["extra_answers"] == {"Driving licence?": "Yes"}
