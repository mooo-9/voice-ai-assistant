from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from pathlib import Path

from core.career import (
    appliers, companies, pipeline, profile, scorer, sources, store, tracker,
)


@pytest.fixture(autouse=True)
def _career_dir(monkeypatch, tmp_path):
    """Also set here so these run without the suite's conftest."""
    monkeypatch.setattr(store, "DIR", tmp_path / "career")


def _job(title, company, url, tier="", score=80, **extra):
    target = companies.match(company)
    return {"title": title, "company": company, "location": "Cairo", "posted": "",
            "url": url, "source": "Wuzzuf", "tier": target["tier"] if target else tier,
            "company_key": target["name"] if target else company, "score": score,
            "fit": "fits", "missing": [], "level": "entry", "in_egypt": True, **extra}


class TestCompanies:
    @pytest.mark.parametrize("name,firm", [
        ("PwC Middle East", "PwC"), ("EY", "EY"), ("Deloitte Innovation Hub", "Deloitte"),
        ("KPMG Hazem Hassan", "KPMG"), ("_VOIS", "Vodafone / _VOIS"),
        ("Vodafone Egypt", "Vodafone / _VOIS"),
    ])
    def test_postings_land_on_their_firm(self, name, firm):
        assert companies.match(name)["name"] == firm

    @pytest.mark.parametrize("name", ["Noon Academy", "Keysight", "Heyday Studio", ""])
    def test_lookalikes_do_not(self, name):
        assert companies.match(name) is None

    def test_the_big_four(self):
        assert [c["name"] for c in companies.big4()] == ["Deloitte", "PwC", "EY", "KPMG"]


class TestSettings:
    def test_practice_mode_is_the_default(self):
        assert store.settings()["live"] is False
        assert store.settings()["daily_target"] == 20

    def test_entry_level_versions_of_his_roles_are_searched(self):
        """Most jobs the second practice run skipped asked for years of
        experience; these terms find the ones that don't."""
        terms = set(store.settings()["search_terms"])
        assert {"junior data analyst", "junior business analyst",
                "fresh graduate data analyst"} <= terms

    def test_the_search_is_aimed_at_ai_data_and_business_analysis(self):
        """Mo's CV targets AI, data analyst and business analyst roles; the
        nightly scoring budget isn't spent on the old mix."""
        terms = set(store.settings()["search_terms"])
        assert {"data analyst", "business analyst", "power bi", "machine learning",
                "artificial intelligence", "AI engineer", "sap", "erp", "sap consultant"} <= terms
        assert not {"credit risk", "financial analyst", "IT support", "ERP SAP",
                    "software developer", "audit associate", "tax associate",
                    "internship", "fresh graduate"} & terms

    def test_unknown_settings_are_ignored(self):
        s = store.update_settings(daily_target=50, nonsense=1)
        assert s["daily_target"] == 50 and "nonsense" not in s


class TestProfile:
    def test_every_answer_starts_missing(self):
        assert profile.missing_answers() == list(profile.ANSWER_KEYS)

    def test_an_answer_is_saved(self):
        assert profile.set_answer("military status", "Exempted").startswith("Saved")
        assert "military_status" not in profile.missing_answers()

    def test_any_other_question_a_form_asks_is_kept_word_for_word(self):
        """A form asking what the usual questions don't cover used to dead-end."""
        assert profile.set_answer("Do you have a valid driving licence?", "Yes") == \
            "Saved for application forms: Do you have a valid driving licence? = Yes"
        assert profile.load()["extra_answers"] == {"Do you have a valid driving licence?": "Yes"}
        assert profile.set_answer("x", " ").startswith("Error")

    def test_before_a_cv_the_profile_says_so(self):
        assert "No CV imported yet" in profile.as_text()

    def test_import_fills_empty_answers_but_keeps_mos_own(self, tmp_path):
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        profile.set_answer("phone", "+20 100 000 0000")
        fields = {"name": "Mohamed Ali", "headline": "BI student", "education": "Helwan BIS",
                  "skills": ["SQL", "Power BI"], "experience": ["Intern at X"], "projects": [],
                  "certifications": [], "languages": ["Arabic", "English"],
                  "email": "mo@x.com", "phone": "0111", "linkedin_url": "", "graduation_year": "2027",
                  "gpa": ""}
        with patch.object(profile, "_cv_text", return_value="CV text " * 50), \
             patch("core.career.claude.ask", return_value=fields):
            out = profile.import_cv(str(cv))
        p = profile.load()
        assert out.startswith("CV imported: Mohamed Ali -- 2 skills")
        assert p["answers"]["phone"] == "+20 100 000 0000"
        assert p["answers"]["email"] == "mo@x.com"
        assert p["cv_path"] == str(cv)
        assert "Skills: SQL; Power BI" in profile.as_text()

    def test_erp_roles_use_the_erp_cv_and_the_rest_the_main_one(self, tmp_path):
        main, erp = tmp_path / "main.pdf", tmp_path / "erp.pdf"
        for cv in (main, erp):
            cv.write_bytes(b"%PDF")
        fields = {"name": "Mo", "headline": "", "education": "", "skills": [],
                  "experience": [], "projects": [], "certifications": [], "languages": [],
                  "email": "", "phone": "", "linkedin_url": "", "graduation_year": "",
                  "gpa": ""}
        with patch.object(profile, "_cv_text", return_value="CV text " * 50), \
             patch("core.career.claude.ask",
                   side_effect=[{**fields, "skills": ["Python"]}, {**fields, "skills": ["SAP"]}]):
            profile.import_cv(str(main))
            # No ERP CV yet: an ERP role still goes out with the main one.
            assert profile.for_job(profile.load(), {"title": "SAP Consultant"})["cv_path"] \
                == str(main)
            assert profile.import_cv(str(erp), erp=True).startswith("ERP CV")
        p = profile.load()
        assert p["cv_path"] == str(main)                    # the main CV is untouched
        for title, cv, skill in [("Junior ERP Consultant", erp, "SAP"),
                                 ("Odoo Functional Trainee", erp, "SAP"),
                                 ("Data Analyst", main, "Python"),
                                 ("Business Analyst", main, "Python")]:
            chosen = profile.for_job(p, {"title": title})
            assert chosen["cv_path"] == str(cv), title
            assert f"Skills: {skill}" in profile.as_text(chosen), title

    def test_an_unreadable_cv_is_reported(self, tmp_path):
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        with patch.object(profile, "_cv_text", return_value=""):
            assert profile.import_cv(str(cv)).startswith("Error: couldn't read text")


class TestScorer:
    def test_finds_the_hr_address(self):
        text = "Apply on wuzzuf. Send your CV to careers@valeo.com. (noreply@x.com)"
        assert scorer.hr_email(text) == "careers@valeo.com"

    def test_board_and_noreply_addresses_are_not_hr(self):
        assert scorer.hr_email("support@wuzzuf.net noreply@company.com") == ""

    def test_score_is_clamped(self):
        with patch("core.career.claude.ask_batch", return_value=[{
                "score": 140, "fit": "x", "missing": [], "level": "entry", "in_egypt": True}]):
            assert scorer.score_all([{"title": "Analyst"}], "profile")[0]["score"] == 100

    def test_a_graduate_in_a_few_weeks_is_scored_as_a_fresh_graduate(self):
        """The first practice run marked Mo down on almost every job as
        "still a student until Oct 2026" -- eight days before he graduated.
        The scorer is given today's date and told how to treat that."""
        with patch("core.career.claude.ask_batch", return_value=[{
                "score": 70, "fit": "x", "missing": [], "level": "entry",
                "in_egypt": True}]) as ask:
            scorer.score_all([{"title": "Analyst"}], "profile")
        from datetime import date
        [asked] = ask.call_args.args[0]
        assert f"Today is {date.today().isoformat()}" in asked["prompt"]
        system = asked["system"]
        assert "within the next 3 months" in system and "fresh graduate" in system


class TestRubric:
    def test_career_ops_rules_reach_every_score(self):
        """The condensed career-ops guide rides along with the scoring prompt."""
        assert "Only **stated** and **structural** gaps" in scorer._SYSTEM
        assert "1 year or 1.5 years is entry, never mid" in scorer._SYSTEM
        # An SAP/ERP role is on target, not a "function mismatch".
        assert "AI engineering and AI roles, data analyst, business analyst, and SAP/ERP" \
            in scorer._SYSTEM
        assert len(scorer._SYSTEM) < 8000      # ~1.1k tokens, not career-ops' 27k


class TestLetters:
    """The first practice run's letters stated real facts more strongly than
    the CV ("used Python" became "built pipelines", "Excellent or Above
    Average" became "excellent") and added "comfortable presenting findings",
    which nothing in the CV says."""

    def _ask(self, channel="form"):
        from core.career import tailor
        with patch("core.career.claude.ask_batch",
                   return_value=[{"subject": "S", "body": "B"}]) as ask:
            tailor.draft_all([{"title": "Data Analyst", "company": "Valeo", "channel": channel}],
                             "profile")
        [writer] = ask.call_args_list[0].args[0]
        return writer["system"], writer["prompt"]

    def test_every_letter_is_checked_against_the_profile(self):
        """Live, one letter in four still invented a phrase ("REST
        API-adjacent work") and kept a stray "//". A second pass reads the
        letter against the profile and returns the corrected one."""
        from core.career import tailor
        answers = [{"subject": "S", "body": "Dear Hiring Team, I built REST APIs. //"},
                   {"subject": "S", "body": "Dear Hiring Team, I coded in C#."}]
        with patch("core.career.claude.ask_batch", side_effect=[[a] for a in answers]) as ask:
            [out] = tailor.draft_all([{"title": "Data Analyst", "company": "Valeo",
                                       "channel": "form"}], "MY PROFILE")
        assert out == {"subject": "S", "body": "Dear Hiring Team, I coded in C#."}
        [check] = ask.call_args_list[1].args[0]
        assert "MY PROFILE" in check["prompt"] and "I built REST APIs. //" in check["prompt"]
        assert "doesn't support" in check["system"]
        assert "180 words" in check["system"]

    def test_letters_and_checks_take_their_time(self):
        """Mo reviews them in the morning and wants them right, not fast."""
        from core.career import tailor
        with patch("core.career.claude.ask_batch",
                   side_effect=[[{"subject": "S", "body": "B"}]] * 2) as ask:
            tailor.draft_all([{"title": "Data Analyst", "channel": "form"}], "profile")
        [writer], [check] = (c.args[0] for c in ask.call_args_list)
        assert writer["effort"] == check["effort"] == "high"
        with patch("core.career.claude.ask_batch", return_value=[None]) as ask:
            scorer.score_all([{"title": "Analyst"}], "profile")
        assert ask.call_args.args[0][0]["effort"] == "medium"

    def test_a_letter_that_couldnt_be_checked_is_not_kept(self):
        from core.career import tailor
        with patch("core.career.claude.ask_batch",
                   side_effect=[[{"subject": "S", "body": "B"}], [None]]):
            assert tailor.draft_all([{"title": "Data Analyst", "channel": "form"}],
                                    "profile") == [None]

    def test_no_claim_is_stronger_than_the_profile_states_it(self):
        system, _ = self._ask()
        assert "no stronger than the profile" in system
        assert "verbs" in system and "ratings" in system

    def test_no_soft_skill_or_trait_the_profile_doesnt_state(self):
        system, _ = self._ask()
        assert "soft skills" in system

    def test_a_form_letter_opens_with_a_greeting(self):
        _, prompt = self._ask("form")
        assert "Dear Hiring Team," in prompt


class TestChannel:
    @pytest.mark.parametrize("job,channel", [
        ({"url": "https://wuzzuf.net/jobs/p/1", "hr_email": "hr@a.com"}, "email"),
        ({"url": "https://wuzzuf.net/jobs/p/1"}, "wuzzuf"),
        ({"url": "https://eg.linkedin.com/jobs/view/1"}, "linkedin"),
        ({"url": "https://pwc.wd3.myworkdayjobs.com/x"}, "site"),
    ])
    def test_channel(self, job, channel):
        assert appliers.channel_for(job) == channel


def _seed(*apps):
    for a in apps:
        a.setdefault("status", "ready")
        a.setdefault("channel", appliers.channel_for(a))
        a.setdefault("draft", {"subject": "S", "body": "Dear team, ..."})
        tracker.add(a)
    return apps


class TestPrepareBatch:
    @pytest.fixture(autouse=True)
    def _cv(self, tmp_path):
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        profile.save({"cv_path": str(cv)})

    def _run(self, jobs, **settings):
        if settings:
            store.update_settings(**settings)
        scores = {j["url"]: j.pop("score") for j in jobs}
        scored = []

        def score_all(batch, p):
            scored.extend(job["url"] for job in batch)
            return [{"score": scores[job["url"]], "fit": "fits", "missing": [],
                     "level": "entry", "in_egypt": True} for job in batch]

        with patch.object(sources, "gather", return_value=jobs), \
             patch.object(scorer, "read_description", return_value="Posting text"), \
             patch.object(scorer, "score_all", side_effect=score_all), \
             patch("core.career.tailor.draft_all",
                   side_effect=lambda batch, p: [{"subject": "S", "body": "B"}] * len(batch)), \
             patch.object(pipeline, "_nightly_extras", return_value=""), \
             patch.object(pipeline, "_notify"):
            out = pipeline.prepare_batch()
        return out, scored

    def test_drafts_the_good_fits_and_skips_the_rest(self):
        out, _ = self._run([_job("Analyst", "Fawry", "https://wuzzuf.net/jobs/p/1", score=85),
                            _job("Analyst", "Nobody", "https://wuzzuf.net/jobs/p/2", score=30)])
        assert sorted(a["status"] for a in tracker.all_apps().values()) == ["ready", "skipped"]
        assert out.startswith("1 applications ready for your review (practice mode")

    def test_jobs_in_mos_field_are_scored_before_unrelated_top_firm_jobs(self):
        """Top firms' careers sites list every Egypt job they have. Sorted by
        tier alone, their sales and plant roles took every scoring slot before
        a single data analyst job was reached."""
        unrelated = [_job(t, "PepsiCo", f"https://www.pepsicojobs.com/main/jobs/{i}", score=70)
                     for i, t in enumerate(["Sales Supervisor", "Maintenance Engineer",
                                            "Warehouse Coordinator"])]
        relevant = [_job("Data Analyst", "Valeo", "https://valeo.x/1", score=70),
                    _job("Junior Business Analyst", "Some Startup",
                         "https://wuzzuf.net/jobs/p/2", score=70)]
        _, scored = self._run(unrelated + relevant, daily_target=1)
        assert scored == [
            "https://valeo.x/1", "https://wuzzuf.net/jobs/p/2"]

    @pytest.mark.parametrize("text,years", [
        ("Requires 3+ years analytics experience in e-commerce.", 3),
        ("3-7 years experience with SQL and Python.", 3),
        ("A minimum of 5 years of experience in data analysis.", 5),
        ("5+ years of professional experience building models.", 5),
        ("2+ years of experience with Power BI.", 2),
        ("0-2 years of experience; fresh graduates welcome.", 0),
        ("Founded 25 years ago, we are a leading company.", 0),
        ("", 0)])
    def test_years_of_experience_a_posting_asks_for(self, text, years):
        assert pipeline._years_asked(text) == years

    def test_postings_asking_3_or_more_years_are_skipped_before_scoring(self):
        """In the second practice run most of the 36 skips asked for years Mo
        doesn't have; each cost a scoring call and a slot a reachable job
        could have had."""
        jobs = [_job("Data Analyst", "Co", f"https://wuzzuf.net/jobs/p/s{i}", score=70,
                     description="We need 5+ years of experience in analytics.")
                for i in range(2)]
        jobs += [_job("Data Analyst", "Co", f"https://wuzzuf.net/jobs/p/f{i}", score=70,
                      description="Fresh graduates welcome. 0-1 years of experience.")
                 for i in range(2)]
        _, scored = self._run(jobs, daily_target=1)
        assert sorted(scored) == [
            "https://wuzzuf.net/jobs/p/f0", "https://wuzzuf.net/jobs/p/f1"]
        skipped = tracker.with_status("skipped")
        assert len(skipped) == 2
        assert skipped[0]["events"][-1]["note"] == "asks for 5+ years"

    def test_closed_postings_are_skipped_before_scoring(self):
        """A closed job costs a scoring call and a slot, and can't be applied to."""
        jobs = [_job("Data Analyst", "Co", "https://www.linkedin.com/jobs/view/1", score=70,
                     description="No longer accepting applications. Build SQL reports."),
                _job("Data Analyst", "Co", "https://www.linkedin.com/jobs/view/2", score=70,
                     description="Build SQL reports. Fresh graduates welcome.")]
        _, scored = self._run(jobs, daily_target=1)
        assert scored == ["https://www.linkedin.com/jobs/view/2"]
        (skipped,) = tracker.with_status("skipped")
        assert skipped["events"][-1]["note"] == "posting closed"

    def test_target_firms_and_everyone_else_share_the_scoring_slots(self):
        """Top firms went first, so in the first practice run not one Wuzzuf
        or LinkedIn data analyst job was scored. They take turns now."""
        firms = [_job(f"Data Analyst {i}", "Valeo", f"https://valeo.x/{i}", score=70)
                 for i in range(30)]
        boards = [_job(f"Data Analyst {i}", "Some Startup", f"https://wuzzuf.net/jobs/p/{i}",
                       score=70) for i in range(30)]
        _, scored = self._run(firms + boards)
        assert len(scored) == 40
        assert sum("valeo" in u for u in scored) == 20 and sum("wuzzuf" in u for u in scored) == 20

    @pytest.mark.parametrize("title,wanted", [
        ("Data Analyst", True), ("Business Intelligence Developer", True),
        ("Power BI Specialist", True), ("Machine Learning Engineer", True),
        ("AI Developer", True), ("Data Scientist", True), ("Business Analyst - MENA", True),
        ("People Analytics Specialist", True), ("NLP Engineer", True),
        ("Sales District Leader Designate", False),
        ("Electrical Maintenance Engineer", False), ("Chef de Partie", False),
        # "analyst" or "graduate" alone isn't his field: the first practice run
        # spent 17 of 40 slots on PepsiCo supply-chain and HR analysts.
        ("SC Planning Associate Analyst", False), ("People Operations Assoc Analyst", False),
        ("ETIC, Tax Operations Analyst - Associate", False),
        ("ETIC, Cybersecurity Graduate Program", False)])
    def test_what_counts_as_mos_field(self, title, wanted):
        assert pipeline._in_field({"title": title}) is wanted

    def test_big4_comes_first_with_no_cap_per_firm(self):
        """Mo wants every Big 4 opening he fits, not three a month."""
        jobs = [_job(f"Graduate {i}", "PwC Middle East", f"https://pwc.x/{i}", score=70)
                for i in range(5)]
        jobs.append(_job("Analyst", "Fawry", "https://wuzzuf.net/jobs/p/9", score=95))
        self._run(jobs)
        ready = pipeline.ready_batch()
        assert [a["company_key"] for a in ready] == ["PwC"] * 5 + ["Fawry"]
        assert tracker.with_status("skipped") == []

    def test_over_the_target_waits_for_tomorrow_without_rescoring(self):
        jobs = [_job(f"Analyst {i}", "Co", f"https://wuzzuf.net/jobs/p/{i}", score=90 - i)
                for i in range(3)]
        self._run(jobs, daily_target=2)
        assert len(tracker.with_status("ready")) == 2
        assert len(tracker.with_status("waiting")) == 1
        _, scored = self._run([])
        assert scored == []
        assert len(tracker.with_status("ready")) == 3

    def test_a_job_already_tracked_is_not_scored_again(self):
        _seed(_job("Analyst", "Co", "https://wuzzuf.net/jobs/p/1"))
        _, scored = self._run([_job("Analyst", "Co", "https://wuzzuf.net/jobs/p/1")])
        assert scored == []


class TestEvaluateOne:
    """A job link Mo found himself, judged on the spot (career-ops' "paste a job")."""
    URL = "https://www.linkedin.com/jobs/view/data-analyst-at-valeo-77"

    @pytest.fixture(autouse=True)
    def _cv(self, tmp_path):
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        profile.save({"cv_path": str(cv)})

    def _fit(self, score):
        return {"score": score, "fit": "SQL and Power BI match.", "missing": ["Tableau"],
                "level": "entry", "in_egypt": True, "title": "Data Analyst",
                "company": "Valeo Egypt"}

    def test_a_fit_waits_for_the_next_job_hunt(self):
        with patch.object(scorer, "read_description", return_value="Build SQL reports."), \
             patch.object(scorer, "score_link", return_value=self._fit(80)):
            out = pipeline.evaluate_one(self.URL)
        assert out.startswith("Data Analyst at Valeo Egypt: 80/100.")
        assert "Tableau" in out and "Worth applying" in out
        (app,) = tracker.with_status("waiting")
        assert app["company_key"] == "Valeo" and app["tier"] == "top"

    def test_a_weak_fit_is_not_worth_sending(self):
        with patch.object(scorer, "read_description", return_value="Build SQL reports."), \
             patch.object(scorer, "score_link", return_value=self._fit(30)):
            out = pipeline.evaluate_one(self.URL)
        assert "Not worth sending: score 30 below 60." in out
        assert tracker.with_status("skipped")

    def test_a_closed_posting_is_not_scored(self):
        with patch.object(scorer, "read_description",
                          return_value="No longer accepting applications"), \
             patch.object(scorer, "score_link") as score:
            assert "closed" in pipeline.evaluate_one(self.URL)
        score.assert_not_called()

    def test_a_job_already_judged_is_not_scored_again(self):
        tracker.add({"url": self.URL, "title": "Data Analyst", "company": "Valeo",
                     "score": 75, "fit": "Good.", "status": "ready"})
        with patch.object(scorer, "score_link") as score:
            assert pipeline.evaluate_one(self.URL).startswith("Already judged")
        score.assert_not_called()


class TestApprove:
    def test_approve_all_but_the_unticked(self):
        a, b = _seed(_job("A", "Co", "https://wuzzuf.net/jobs/p/1"),
                     _job("B", "Co", "https://wuzzuf.net/jobs/p/2"))
        with patch.object(pipeline, "start_in_background", return_value=True):
            out = pipeline.approve(skip=[b["id"]])
        assert out.startswith("Approved 1, skipped 1.")
        assert tracker.all_apps()[a["id"]]["status"] == "approved"
        assert tracker.all_apps()[b["id"]]["status"] == "skipped"

    def test_approve_only_some(self):
        a, b = _seed(_job("A", "Co", "https://wuzzuf.net/jobs/p/1"),
                     _job("B", "Co", "https://wuzzuf.net/jobs/p/2"))
        with patch.object(pipeline, "start_in_background", return_value=True):
            out = pipeline.approve(only=[b["id"]])
        assert out.startswith("Approved 1, skipped 0, 1 saved for later.")
        # What Mo didn't tick stays, letter and all, for him to send later.
        kept = tracker.all_apps()[a["id"]]
        assert kept["status"] == "ready" and kept["draft"]
        with patch.object(pipeline, "start_in_background", return_value=True):
            pipeline.approve(only=[a["id"]])
        assert tracker.all_apps()[a["id"]]["status"] == "approved"


class TestRunApproved:
    def test_practice_mode_sends_nothing(self):
        (a,) = _seed(_job("A", "Co", "https://wuzzuf.net/jobs/p/1", status="approved"))
        with patch.object(appliers, "apply") as apply, patch.object(pipeline, "_notify"):
            out = pipeline.run_approved(pause=False)
        apply.assert_not_called()
        assert tracker.all_apps()[a["id"]]["status"] == "practice"
        assert out == "1 practice runs (not sent)"

    def test_live_without_a_cv_is_still_practice(self):
        store.update_settings(live=True)
        _seed(_job("A", "Co", "https://wuzzuf.net/jobs/p/1", status="approved"))
        with patch.object(appliers, "apply") as apply, patch.object(pipeline, "_notify"):
            pipeline.run_approved(pause=False)
        apply.assert_not_called()

    def test_live_sends_and_keeps_linkedin_under_its_cap(self, tmp_path):
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        profile.save({"cv_path": str(cv)})
        store.update_settings(live=True, linkedin_daily_cap=1)
        _seed(_job("A", "Co", "https://eg.linkedin.com/jobs/view/1", status="approved"),
              _job("B", "Co", "https://eg.linkedin.com/jobs/view/2", status="approved"),
              _job("C", "Co", "https://wuzzuf.net/jobs/p/3", status="approved"))
        with patch.object(appliers, "apply", return_value=("applied", "ok")) as apply, \
             patch.object(pipeline, "_notify"):
            pipeline.run_approved(pause=False)
        assert apply.call_count == 2
        assert len(tracker.with_status("approved")) == 1      # the second LinkedIn one waits


class TestAppliers:
    def _app(self, channel, **extra):
        return {"id": "x", "title": "Data Analyst", "company": "Valeo", "url": "https://v.com/j",
                "channel": channel, "draft": {"subject": "Application", "body": "COVER LETTER"},
                **extra}

    def test_an_approved_site_form_is_filled_and_submitted(self):
        """Mo approving it in the review is the go-ahead: El Fager does the rest."""
        task = appliers.browser_task(self._app("site"), {
            "answers": {"military_status": "Exempted"},
            "extra_answers": {"Do you have a driving licence?": "Yes"}})
        assert "and submit" in task and "do NOT press" not in task
        assert "Military status (exempted / completed / postponed): Exempted" in task
        assert "- Do you have a driving licence?: Yes" in task
        assert "COVER LETTER" in task

    def test_linkedin_without_easy_apply_goes_on_to_the_employers_form(self):
        """Most LinkedIn jobs only link out; those used to stop as 'no Easy Apply'."""
        task = appliers.browser_task(self._app("linkedin"), {"answers": {}})
        assert "complete the application on the employer's site" in task
        assert "BLOCKED: no Easy Apply" not in task

    def test_browser_results_map_to_statuses(self, tmp_path):
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        prof = {"cv_path": str(cv), "answers": {}}
        for said, status in [("SUBMITTED - done", "applied"),
                             ("Application already submitted successfully as confirmed", "applied"),
                             ("The application was not submitted successfully", "failed"),
                             ("Application confirmed received.", "applied"),
                             ("BLOCKED: expected salary", "needs_you"),
                             ("Browser task completed (reached max steps).", "failed")]:
            with patch("core.agents.browser_agent.BrowserAgent.run", return_value=said) as run, \
                 patch.object(appliers, "_ledger"):
                assert appliers.apply_in_browser(self._app("wuzzuf"), prof)[0] == status
            assert run.call_args.kwargs["upload_path"] == str(cv)
            assert run.call_args.kwargs["close_tab"] is True
            assert run.call_args.kwargs["telemetry_source"] == "career"

    def test_the_form_steps_go_to_claude_code_and_the_tab_closes(self, tmp_path):
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        with patch("core.agents.browser_agent.BrowserAgent.run", return_value="SUBMITTED") as run:
            appliers.apply_in_browser(self._app("site"), {"cv_path": str(cv), "answers": {}})
        kw = run.call_args.kwargs
        assert kw["close_tab"] is True and kw["telemetry_source"] == "career"
        with patch("core.career.claude.ask", return_value='{"status": "done"}') as ask:
            assert kw["ask_fn"]("SYSTEM", "PROMPT", "PNG") == '{"status": "done"}'
        assert ask.call_args.kwargs["image"] == "PNG" and ask.call_args.args == ("PROMPT",)

    def test_email_goes_with_the_cv_attached(self, tmp_path):
        cv = tmp_path / "Mohamed CV.pdf"
        cv.write_bytes(b"%PDF-1.4 cv")
        service = MagicMock()
        with patch("tools.gmail_tool.GMAIL_AVAILABLE", True), \
             patch("tools.gmail_tool.get_gmail_service", return_value=service), \
             patch.object(appliers, "_ledger") as ledger:
            status, note = appliers.send_email(self._app("email", hr_email="hr@valeo.com"),
                                               {"cv_path": str(cv)})
        assert status == "applied"
        raw = service.users().messages().send.call_args.kwargs["body"]["raw"]
        import base64
        mime = base64.urlsafe_b64decode(raw).decode()
        assert "hr@valeo.com" in mime and 'filename="Mohamed CV.pdf"' in mime
        ledger.assert_called_once()

    @pytest.mark.parametrize("name,content_type", [
        ("Mohamed CV.pdf", "application/pdf"),
        ("Mohamed CV.docx",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
    def test_the_cv_goes_as_its_own_file_type(self, tmp_path, name, content_type):
        """The live test to Mo's inbox sent the PDF as application/octet-stream,
        which some recruiters' mail apps and hiring systems won't preview."""
        cv = tmp_path / name
        cv.write_bytes(b"cv")
        service = MagicMock()
        with patch("tools.gmail_tool.GMAIL_AVAILABLE", True), \
             patch("tools.gmail_tool.get_gmail_service", return_value=service), \
             patch.object(appliers, "_ledger"):
            appliers.send_email(self._app("email", hr_email="hr@valeo.com"), {"cv_path": str(cv)})
        import base64
        import email
        raw = service.users().messages().send.call_args.kwargs["body"]["raw"]
        msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
        (cv_part,) = [p for p in msg.walk() if p.get_filename() == name]
        assert cv_part.get_content_type() == content_type

    def test_email_without_gmail_fails_cleanly(self):
        with patch("tools.gmail_tool.GMAIL_AVAILABLE", False):
            assert appliers.send_email(self._app("email", hr_email="a@b.com"), {})[0] == "failed"


class TestReplies:
    def _service(self, messages):
        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": m["id"]} for m in messages]}
        by_id = {m["id"]: m for m in messages}
        service.users().messages().get.side_effect = lambda userId, id, **kw: MagicMock(
            execute=MagicMock(return_value={
                "snippet": by_id[id]["snippet"],
                "payload": {"headers": [{"name": "From", "value": by_id[id]["from"]},
                                        {"name": "Subject", "value": by_id[id]["subject"]}]}}))
        return service

    def test_interviews_and_rejections_are_recorded_once(self):
        a, b = _seed(_job("Analyst", "Valeo", "https://wuzzuf.net/jobs/p/1", status="applied"),
                     _job("Analyst", "Fawry", "https://wuzzuf.net/jobs/p/2", status="applied"))
        service = self._service([
            {"id": "m1", "from": "Valeo HR <hr@valeo.com>", "subject": "Interview invitation",
             "snippet": "We'd like to invite you"},
            {"id": "m2", "from": "Fawry Careers", "subject": "Your application",
             "snippet": "Unfortunately we have decided"},
        ])
        with patch("tools.gmail_tool.GMAIL_AVAILABLE", True), \
             patch("tools.gmail_tool.get_gmail_service", return_value=service), \
             patch.object(pipeline, "_notify") as notify:
            first = pipeline.check_replies()
            second = pipeline.check_replies()
        assert tracker.all_apps()[a["id"]]["status"] == "interview"
        assert tracker.all_apps()[b["id"]]["status"] == "rejected"
        assert "INTERVIEW: Analyst at Valeo" in first
        assert second == "No new replies from companies you applied to."
        notify.assert_called_once()


class TestSmoothness:
    def test_status_says_whether_everything_the_hunt_needs_is_there(self):
        from core.career import claude
        with patch("tools.comet_tool.cdp_alive", return_value=False):
            line = pipeline.readiness()
        assert "Claude Code: not installed" in line   # the suite never finds claude.exe
        assert "Comet: will close and reopen once" in line
        claude.last_code_error = "Not logged in"
        try:
            with patch.object(claude, "_code_exe", return_value=Path("C:/x/claude.exe")), \
                 patch("tools.comet_tool.cdp_alive", return_value=True):
                assert "last call failed (Not logged in" in pipeline.readiness()
        finally:
            claude.last_code_error = ""
        assert "Claude Code:" in pipeline.status_text()

    def test_mo_hears_before_comet_restarts(self):
        (a,) = _seed(_job("Analyst", "Valeo", "https://v.com/1"))
        store.update_settings(live=True)
        with patch.object(profile, "has_cv", return_value=True), \
             patch.object(pipeline, "start_in_background", return_value=True), \
             patch("tools.comet_tool.cdp_alive", return_value=False):
            assert pipeline.approve().endswith("Comet will close and reopen once to connect; "
                                               "your tabs come back.")


class TestBlockedApplications:
    """A form question El Fager can't answer, or a site wanting an account,
    stops one application; Mo's answer or sign-in sends it again."""
    def _blocked(self, url, note):
        (a,) = _seed(_job("Analyst", "Valeo", url, status="approved"))
        tracker.update(a["id"], "needs_you", note)
        return a

    def test_answering_the_question_saves_it_and_retries(self):
        from tools import career_tool
        a = self._blocked("https://v.com/1", "BLOCKED: Do you have a driving licence?")
        b = self._blocked("https://v.com/2", "BLOCKED: needs an account on Workday")
        assert "Do you have a driving licence?" in focus_line()
        with patch.object(pipeline, "start_in_background", return_value=True) as start:
            out = career_tool.set_application_answer("Do you have a driving licence?", "Yes")
        assert out.endswith("Retrying 1 application(s) now.")
        start.assert_called_once_with(pipeline.run_approved)
        assert tracker.all_apps()[a["id"]]["status"] == "approved"
        assert tracker.all_apps()[b["id"]]["status"] == "needs_you"

    def test_the_phone_alert_says_the_question(self):
        """'1 waiting on you' told Mo nothing he could answer from his phone."""
        (a,) = _seed(_job("Analyst", "Valeo", "https://v.com/1", status="approved"))
        store.update_settings(live=True)
        with patch.object(profile, "has_cv", return_value=True), \
             patch.object(appliers, "apply", return_value=("needs_you", "BLOCKED: Driving licence?")), \
             patch.object(pipeline, "_notify"), \
             patch.object(pipeline, "_ask_mo_what_stopped") as ask:
            out = pipeline.run_approved(pause=False)
        assert "A form asks: 'Driving licence?' (Valeo) -- tell El Fager the answer" in out
        ask.assert_called_once()        # and on WhatsApp, where he can reply

    def test_retry_after_signing_in_sends_them_all_again(self):
        from tools import career_tool
        self._blocked("https://v.com/2", "BLOCKED: needs an account on Workday")
        assert "retry_applications" in focus_line()
        with patch.object(pipeline, "start_in_background", return_value=True):
            assert career_tool.retry_applications() == "Retrying 1 application(s) now."
        assert career_tool.retry_applications() == "Nothing is waiting to be retried."


def focus_line():
    from core.career import focus
    return focus.status_line()


class TestReplyStatus:
    """career-ops' order: rejection, then automatic receipt, then interview."""
    @pytest.mark.parametrize("text,status", [
        ("Interview invitation: we'd like to invite you", "interview"),
        ("Unfortunately we won't invite you to interview", "rejected"),
        ("We will not be moving forward with your application", "rejected"),
        ("Thank you for applying! If shortlisted we'll invite you to interview.", ""),
        ("We received your application for Data Analyst", ""),
        ("Quick question about your availability", "replied"),
    ])
    def test_reply_status(self, text, status):
        assert pipeline._reply_status(text) == status

    def test_a_receipt_keeps_the_application_waiting_for_its_follow_up(self):
        (a,) = _seed(_job("Analyst", "Valeo", "https://wuzzuf.net/jobs/p/1", status="applied"))
        service = TestReplies()._service([
            {"id": "m9", "from": "Valeo Careers", "subject": "Thank you for applying",
             "snippet": "Our team will review it and contact you for an interview"}])
        with patch("tools.gmail_tool.GMAIL_AVAILABLE", True), \
             patch("tools.gmail_tool.get_gmail_service", return_value=service), \
             patch.object(pipeline, "_notify") as notify:
            out = pipeline.check_replies()
        assert out == "No new replies from companies you applied to."
        assert tracker.all_apps()[a["id"]]["status"] == "applied"
        assert tracker.all_apps()[a["id"]]["reply_ids"] == ["m9"]
        notify.assert_not_called()


class TestStatus:
    def test_says_practice_mode_and_whats_missing(self):
        _seed(_job("A", "Co", "https://wuzzuf.net/jobs/p/1"))
        out = pipeline.status_text()
        assert out.startswith("Mode: PRACTICE (nothing is sent).")
        assert "1 ready" in out
        assert "military_status" in out


class TestSources:
    def test_gathers_tags_tiers_and_drops_senior_roles(self, monkeypatch):
        monkeypatch.setattr(companies, "premium", lambda: [])
        monkeypatch.setattr(companies, "with_career_sites", lambda: [])
        found = {
            ("LinkedIn", "data analyst"): [
                {"title": "Data Analyst", "company": "PwC Middle East", "location": "", "posted": "",
                 "url": "https://www.linkedin.com/jobs/view/1", "source": "LinkedIn"},
                {"title": "Senior Data Analyst", "company": "Fawry", "location": "", "posted": "",
                 "url": "https://www.linkedin.com/jobs/view/2", "source": "LinkedIn"}],
        }
        with patch("core.agents.job_search_agent.JobSearchAgent._from_source",
                   lambda self, src, term: found.get((src, term), [])):
            jobs = sources.gather({"search_terms": ["data analyst"]})
        assert [(j["title"], j["tier"], j["company_key"]) for j in jobs] == [
            ("Data Analyst", "big4", "PwC")]

    def test_bank_jobs_never_reach_the_batch(self, monkeypatch):
        monkeypatch.setattr(companies, "premium", lambda: [])
        monkeypatch.setattr(companies, "with_career_sites", lambda: [])
        found = [{"title": "Credit Risk Analyst", "company": "CIB Egypt", "location": "",
                  "posted": "", "url": "https://www.linkedin.com/jobs/view/1", "source": "LinkedIn"},
                 {"title": "Credit Risk Analyst", "company": "Tamweely", "location": "",
                  "posted": "", "url": "https://www.linkedin.com/jobs/view/2", "source": "LinkedIn"}]
        with patch("core.agents.job_search_agent.JobSearchAgent._from_source",
                   lambda self, src, term: found if src == "LinkedIn" else []):
            jobs = sources.gather({"search_terms": ["credit risk"]})
        assert [j["company"] for j in jobs] == ["Tamweely"]

    def test_wuzzuf_and_linkedin_are_the_boards_searched(self, monkeypatch):
        """Mo dropped Bayt and Forasna; he has a Wuzzuf account."""
        monkeypatch.setattr(companies, "with_career_sites", lambda: [])
        asked = []
        with patch("core.agents.job_search_agent.JobSearchAgent._from_source",
                   lambda self, src, term: asked.append(src) or []):
            sources.gather({"search_terms": ["data analyst", "business analyst"]})
        assert set(asked) == {"Wuzzuf", "LinkedIn"}

    def test_no_bank_is_a_target(self):
        names = {c["name"] for c in companies.all_companies()}
        assert not names & {"CIB", "QNB", "National Bank of Egypt", "Banque Misr", "HSBC"}
        from core.career import programmes
        assert not [p for p in programmes.seeds() if p["company"] == "CIB"]

    def test_one_job_listed_twice_is_scored_once(self, monkeypatch):
        """The second practice run spent two slots on repeats: BCG X's job on
        LinkedIn and BCG's own site, and Amazon's reposted by ACCA Careers."""
        monkeypatch.setattr(companies, "premium", lambda: [])
        monkeypatch.setattr(companies, "with_career_sites", lambda: [])
        def job(title, company, url):
            return {"title": title, "company": company, "location": "", "posted": "",
                    "url": url, "source": "LinkedIn"}
        found = [job("Forward Deployed AI Engineer, Egypt - BCG X", "BCG X", "u1"),
                 job("Forward Deployed AI Engineer, Egypt - BCG X", "BCG", "u2"),
                 job("Business Analyst - MENA, A-Now", "Amazon", "u3"),
                 job("Business Analyst - MENA, A-Now", "ACCA Careers", "u4"),
                 job("Data Analyst", "EGEC", "u5"),
                 job("Data Analyst", "dubizzle Egypt", "u6")]
        with patch("core.agents.job_search_agent.JobSearchAgent._from_source",
                   lambda self, src, term: found if src == "LinkedIn" else []):
            jobs = sources.gather({"search_terms": ["x"]})
        assert [j["url"] for j in jobs] == ["u1", "u3", "u5", "u6"]

    def test_a_firms_name_search_keeps_only_its_own_postings(self):
        agent = MagicMock()
        agent._from_source.return_value = [
            {"title": "Auditor", "company": "KPMG Egypt", "url": "u1"},
            {"title": "Accountant", "company": "Some firm hiring ex-KPMG", "url": "u2"},
            {"title": "Clerk", "company": "Other", "url": "u3"}]
        kpmg = next(c for c in companies.big4() if c["name"] == "KPMG")
        out = sources._board_by_name(agent, "Wuzzuf", kpmg)
        assert [j["url"] for j in out] == ["u1", "u2"]

    def test_workday_keeps_egypt_postings(self):
        page = {"total": 2, "jobPostings": [
            {"title": "ETIC Graduate Program", "locationsText": "Cairo",
             "externalPath": "/job/Cairo/ETIC_1", "postedOn": "Posted Today"},
            {"title": "Audit Associate", "locationsText": "Dubai", "externalPath": "/job/Dubai/A_2"}]}
        wd = {"host": "pwc.wd3.myworkdayjobs.com", "tenant": "pwc", "site": "Global_Campus_Careers"}
        firm = next(c for c in companies.big4() if c["name"] == "PwC")
        with patch.object(sources, "_post_json", return_value=page) as post:
            jobs = sources._workday(wd, firm)
        assert post.call_args.args[0] ==             "https://pwc.wd3.myworkdayjobs.com/wday/cxs/pwc/Global_Campus_Careers/jobs"
        assert [j["url"] for j in jobs] == [
            "https://pwc.wd3.myworkdayjobs.com/en-US/Global_Campus_Careers/job/Cairo/ETIC_1"]

    def test_workday_reads_past_its_first_twenty(self):
        """Workday answers 20 at a time, and says the total only on the first
        page: Valeo's 32 Egypt postings came back as 20."""
        def posting(i):
            return {"title": f"Engineer {i}", "locationsText": "Cairo, Egypt",
                    "externalPath": f"/job/Cairo/E_{i}"}
        pages = [{"total": 32, "jobPostings": [posting(i) for i in range(20)]},
                 {"total": 0, "jobPostings": [posting(i) for i in range(20, 32)]}]
        wd = {"host": "valeo.wd3.myworkdayjobs.com", "tenant": "valeo", "site": "valeo_jobs"}
        firm = companies.match("Valeo")
        with patch.object(sources, "_post_json", side_effect=pages) as post:
            jobs = sources._workday(wd, firm)
        assert len(jobs) == 32 and post.call_count == 2
        assert post.call_args.args[1]["offset"] == 20

    def test_smartrecruiters_postings_in_egypt(self):
        page = {"content": [{"id": "744000151793248", "name": "Delivery Support",
                             "location": {"city": "El Katameya", "country": "eg"},
                             "releasedDate": "2026-09-22T10:00:00.000Z"}]}
        firm = companies.match("talabat")
        with patch.object(sources, "_get_json", return_value=page) as get:
            jobs = sources._smartrecruiters({"company": "DeliveryHero"}, firm)
        assert get.call_args.args[0] ==             "https://api.smartrecruiters.com/v1/companies/DeliveryHero/postings"
        assert get.call_args.args[1]["country"] == "eg"
        assert jobs == [{"title": "Delivery Support", "company": "Talabat",
                         "location": "El Katameya", "posted": "2026-09-22",
                         "url": "https://jobs.smartrecruiters.com/DeliveryHero/744000151793248",
                         "source": "Talabat careers"}]

    def test_amazon_jobs_in_egypt(self):
        page = {"jobs": [{"title": "Business Analyst - MENA", "city": "Cairo",
                          "posted_date": "September 25, 2026",
                          "job_path": "/en/jobs/10560210/business-analyst-mena"}]}
        firm = companies.match("Amazon")
        with patch.object(sources, "_get_json", return_value=page) as get:
            jobs = sources._amazon({"country": "EGY"}, firm)
        assert get.call_args.args[1]["normalized_country_code[]"] == "EGY"
        assert jobs == [{"title": "Business Analyst - MENA", "company": "Amazon",
                         "location": "Cairo", "posted": "September 25, 2026",
                         "url": "https://www.amazon.jobs/en/jobs/10560210/business-analyst-mena",
                         "source": "Amazon careers"}]

    def test_every_firm_with_a_career_site_is_read_not_only_premium(self, monkeypatch):
        """Valeo isn't premium, but its own Workday board is read."""
        monkeypatch.setattr(companies, "premium", lambda: [])
        monkeypatch.setattr(companies, "with_career_sites", lambda: [companies.match("Valeo")])
        read = []
        with patch.object(sources, "_workday", side_effect=lambda wd, firm: read.append(
                (firm["name"], wd["tenant"])) or []),              patch("core.agents.job_search_agent.JobSearchAgent._from_source", return_value=[]):
            sources.gather({"search_terms": []})
        assert read == [("Valeo", "valeo")]

    def test_the_new_boards_are_in_the_list(self):
        boards = {c["name"]: c for c in companies.with_career_sites()}
        for firm in ("Valeo", "Mondelez", "Mastercard", "Sanofi", "Unilever", "GSK",
                     "Novartis", "Visa", "Pfizer"):
            assert boards[firm]["workday"], firm
        assert boards["Talabat"]["smartrecruiters"] == [{"company": "DeliveryHero"}]
        assert boards["Amazon"]["amazon_jobs"] == [{"country": "EGY"}]

    def test_the_global_firms_are_read_too(self):
        boards = {c["name"]: c for c in companies.with_career_sites()}
        assert boards["Oracle"]["oracle_cloud"] and boards["Dell Technologies"]["oracle_cloud"]
        assert boards["Ericsson"]["eightfold"] and boards["PepsiCo"]["jibe"]
        assert boards["BCG"]["phenom"] and boards["Majid Al Futtaim"]["phenom"]
        assert boards["L'Oréal"]["pages"]

    def test_every_kind_of_career_site_has_a_reader(self):
        assert set(sources.READERS) == set(companies.CAREER_SITE_KEYS)

    def test_oracle_cloud_in_egypt(self):
        page = {"items": [{"requisitionList": [
            {"Id": "344587", "Title": "SaaS Consultant", "PrimaryLocation": "CAIRO, Egypt",
             "PostedDate": "2026-09-06"}]}]}
        oc = {"host": "eeho.fa.us2.oraclecloud.com", "site": "CX_45001",
              "job_url": "https://careers.oracle.com/en/sites/jobsearch/job/"}
        with patch.object(sources, "_get_json", return_value=page) as get:
            jobs = sources._oracle_cloud(oc, companies.match("Oracle"))
        # The finder lives in the query string; any params, even {}, make httpx drop it.
        (url,) = get.call_args.args
        assert not get.call_args.kwargs
        assert url.startswith("https://eeho.fa.us2.oraclecloud.com/hcmRestApi/")
        assert "siteNumber=CX_45001" in url and "location=Egypt" in url
        assert jobs == [{"title": "SaaS Consultant", "company": "Oracle",
                         "location": "CAIRO, Egypt", "posted": "2026-09-06",
                         "url": "https://careers.oracle.com/en/sites/jobsearch/job/344587",
                         "source": "Oracle careers"}]

    def test_eightfold_in_egypt(self):
        page = {"data": {"count": 1, "positions": [
            {"name": "Automation Engineer", "positionUrl": "/careers/job/563121777194376",
             "locations": ["Lisbon,Lisboa,Portugal", "Smart Village,Cairo,Egypt"],
             "postedTs": 1789468172}]}}
        ef = {"host": "jobs.ericsson.com", "domain": "ericsson.com"}
        with patch.object(sources, "_get_json", return_value=page) as get:
            jobs = sources._eightfold(ef, companies.match("Ericsson"))
        assert get.call_args.args[1]["location"] == "Egypt"
        assert jobs[0]["title"] == "Automation Engineer"
        assert jobs[0]["location"] == "Smart Village,Cairo,Egypt"
        assert jobs[0]["url"] == "https://jobs.ericsson.com/careers/job/563121777194376"
        assert jobs[0]["posted"].startswith("2026-")

    def test_jibe_reads_every_page_and_keeps_egypt(self):
        def job(slug, country="Egypt"):
            return {"data": {"slug": slug, "title": f"Engineer {slug}", "city": "Giza",
                             "country": country, "posted_date": "2026-09-07T00:00:00+0000"}}
        pages = [{"totalCount": 3, "jobs": [job("1"), job("2", "Morocco")]},
                 {"totalCount": 3, "jobs": [job("3")]}]
        with patch.object(sources, "_get_json", side_effect=pages) as get:
            jobs = sources._jibe({"host": "www.pepsicojobs.com"}, companies.match("PepsiCo"))
        assert get.call_count == 2 and get.call_args.args[1] == {"location": "Egypt", "page": 2}
        assert [j["url"] for j in jobs] == ["https://www.pepsicojobs.com/main/jobs/1",
                                           "https://www.pepsicojobs.com/main/jobs/3"]
        assert jobs[0]["posted"] == "2026-09-07"

    def test_phenom_in_egypt(self):
        found = {"refineSearch": {"data": {"jobs": [
            {"jobId": "58478", "title": "Finance Co-Op/Intern", "country": "Egypt",
             "cityStateCountry": "Cairo, Cairo, Egypt", "postedDate": "2026-06-22T00:00:00.000+0000"}]}}}
        ph = {"host": "careers.bcg.com", "ref": "BCG1US"}
        with patch.object(sources, "_post_json", return_value=found) as post:
            jobs = sources._phenom(ph, companies.match("BCG"))
        assert post.call_args.args[0] == "https://careers.bcg.com/widgets"
        assert post.call_args.args[1]["selected_fields"] == {"country": ["Egypt"]}
        assert jobs == [{"title": "Finance Co-Op/Intern", "company": "BCG",
                         "location": "Cairo, Cairo, Egypt", "posted": "2026-06-22",
                         "url": "https://careers.bcg.com/global/en/job/58478",
                         "source": "BCG careers"}]

    def test_a_careers_page_that_lists_its_jobs(self):
        html = """<a href="/en_US/jobs/JobDetail/Plant-Safety-Manager/234267">Plant Safety Manager</a>
                  <a href="/en_US/jobs/JobDetail/Plant-Safety-Manager/234267">Apply Now</a>
                  <a href="/en_US/jobs/SearchJobs/">Search</a>"""
        pg = {"url": "https://careers.loreal.com/en_US/jobs/SearchJobs/Egypt",
              "job_path": "/en_US/jobs/JobDetail/"}
        with patch.object(sources, "_get_text", return_value=html):
            jobs = sources._page(pg, companies.match("L'Oréal"))
        assert [(j["title"], j["url"]) for j in jobs] == [(
            "Plant Safety Manager",
            "https://careers.loreal.com/en_US/jobs/JobDetail/Plant-Safety-Manager/234267")]


class TestRepliesAfterAnInterview:
    def test_a_later_plain_reply_keeps_the_interview(self):
        (a,) = _seed(_job("Analyst", "Valeo", "https://wuzzuf.net/jobs/p/1", status="interview"))
        service = TestReplies()._service([
            {"id": "m3", "from": "Valeo HR", "subject": "Directions to our office",
             "snippet": "Our address is"}])
        with patch("tools.gmail_tool.GMAIL_AVAILABLE", True), \
             patch("tools.gmail_tool.get_gmail_service", return_value=service), \
             patch.object(pipeline, "_notify"):
            pipeline.check_replies()
        app = tracker.all_apps()[a["id"]]
        assert app["status"] == "interview" and app["reply_ids"] == ["m3"]


class TestAtsCheck:
    """What a hiring system reading the CV's text would miss (career-ops' ATS check)."""
    GOOD = ("Mohamed Ali\nmo@example.com\nEDUCATION\nGUC, Business Informatics\n"
            "Work Experience\nQuadraTech intern\nTechnical Skills:\nSQL, Power BI\n")

    def test_a_readable_cv_passes(self):
        assert profile.ats_problems(self.GOOD) == []

    def test_missing_headings_and_email_are_named(self):
        text = "Mohamed Ali\nI studied at GUC and know SQL.\nMy experience is at QuadraTech."
        assert profile.ats_problems(text) == [
            "no 'Experience' heading", "no 'Education' heading", "no 'Skills' heading",
            "no email address in its text"]
