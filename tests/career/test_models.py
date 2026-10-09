"""Sonnet 5 for the job hunt, Opus 5 for anything aimed at the Big 4 -- the
applications that matter most -- and nothing spent before Mo's CV is in."""
from unittest.mock import MagicMock, patch

import pytest

from core.career import claude, interview, pipeline, profile, referrals, scorer, store, tailor


@pytest.fixture(autouse=True)
def _career_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DIR", tmp_path / "career")


def _model_used(fn):
    with patch.object(claude, "_get_client") as client:
        client.return_value.messages.create.return_value = MagicMock(
            stop_reason="end_turn",
            content=[MagicMock(type="text", text='{"score": 70, "fit": "", "missing": [], '
                                                 '"level": "entry", "in_egypt": true, '
                                                 '"subject": "S", "body": "B", "note": "N", '
                                                 '"message": "M"}')])
        fn()
    return client.return_value.messages.create.call_args.kwargs["model"]


def _batch_models_used(fn):
    """The models of the requests fn sent as a Message Batch."""
    with patch.object(claude, "_get_client") as client:
        batches = client.return_value.messages.batches
        batches.create.return_value = MagicMock(id="b", processing_status="ended")
        batches.results.return_value = []
        fn()
    return {r["params"]["model"] for c in batches.create.call_args_list
            for r in c.kwargs["requests"]}


class TestModels:
    @pytest.mark.parametrize("company,model", [
        ("PwC Middle East", "claude-opus-5"), ("KPMG Hazem Hassan", "claude-opus-5"),
        ("IBM Egypt", "claude-opus-5"), ("Accenture", "claude-opus-5"),
        ("Schneider Electric Egypt", "claude-opus-5"), ("Procter & Gamble", "claude-opus-5"),
        ("Siemens Egypt", "claude-opus-5"), ("Microsoft", "claude-opus-5"),
        ("Nestlé Egypt", "claude-opus-5"),
        ("Fawry", "claude-sonnet-5"), ("Vodafone Egypt", "claude-sonnet-5"),
        ("Some Startup", "claude-sonnet-5"), ("", "claude-sonnet-5")])
    def test_scoring(self, company, model):
        job = {"title": "Analyst", "company": company}
        assert _batch_models_used(lambda: scorer.score_all([job], "profile")) == {model}

    @pytest.mark.parametrize("tier,model", [
        ("big4", "claude-opus-5"), ("top", "claude-sonnet-5"), ("", "claude-sonnet-5")])
    def test_only_the_big4_get_their_letters_from_opus(self, tier, model):
        job = {"title": "Analyst", "company": "IBM Egypt", "tier": tier, "channel": "email"}
        assert _batch_models_used(lambda: tailor.draft_all([job], "profile")) == {model}

    def test_interview_prep_for_a_big4_firm_uses_opus(self):
        with patch("core.agents.research_agent.ResearchAgent.run", return_value=""):
            assert _model_used(lambda: interview.prep("PwC")) == "claude-opus-5"
            assert _model_used(lambda: interview.prep("Fawry")) == "claude-sonnet-5"

    def test_referral_notes_follow_the_persons_firm(self):
        person = {"name": "A", "headline": "", "company": "KPMG", "url": "u"}
        assert _model_used(lambda: referrals.draft(person)) == "claude-opus-5"
        assert _model_used(lambda: referrals.draft({**person, "company": "Valeo"})) == "claude-sonnet-5"


class TestDefaults:
    def test_twenty_a_day(self):
        assert store.settings()["daily_target"] == 20


class TestNothingSpentBeforeTheCv:
    def test_no_jobs_are_scored_or_drafted(self):
        with patch("core.career.sources.gather") as gather, \
             patch.object(scorer, "score_all") as score, \
             patch.object(pipeline, "_nightly_extras", return_value="") as extras, \
             patch.object(pipeline, "_notify"):
            out = pipeline.prepare_batch()
        gather.assert_not_called()
        score.assert_not_called()
        extras.assert_called_once_with(store.settings(), referrals=False)
        assert out.startswith("No CV imported yet")

    def test_programme_deadlines_are_still_watched(self, monkeypatch):
        from core.career import programmes
        checked = []
        monkeypatch.setattr(programmes, "check_all", lambda: checked.append(1) or "")
        with patch.object(referrals, "find") as find:
            pipeline._nightly_extras(store.settings(), referrals=False)
        assert checked == [1]
        find.assert_not_called()


class TestPremiumCareerSites:
    """Mo's picks beyond the Big 4 are searched on their own career sites too."""

    def test_the_seven_are_premium_with_career_sites(self):
        from core.career import companies
        names = {c["name"] for c in companies.premium()}
        assert names == {"Deloitte", "PwC", "EY", "KPMG", "IBM", "Accenture",
                         "Schneider Electric", "Procter & Gamble", "Siemens",
                         "Microsoft", "Nestlé"}
        for c in companies.premium():
            if c["tier"] != "big4":
                assert c["tier"] == "top" and c["sites"], c["name"]

    @pytest.mark.parametrize("name,url", [
        ("IBM", "https://careers.ibm.com/job/13997670/data-engineer-data-integration-internship-cairo-eg/"),
        ("Accenture", "https://www.accenture.com/sa-en/careers/jobdetails?id=R00228734_en&title=Cyber"),
        ("Schneider Electric", "https://careers.se.com/jobs/115748"),
        ("Siemens", "https://jobs.siemens.com/en_US/externaljobs/JobDetail/522581"),
        ("Microsoft", "https://careers.microsoft.com/us/en/job/1257263/Software-Engineer"),
        ("Nestlé", "https://jobdetails.nestle.com/job/Cairo-Marketing-Nestalent/1417702933/"),
        ("Procter & Gamble", "https://www.pgcareers.com/global/en/job/R000147313/Supply-Chain-Analyst"),
    ])
    def test_each_site_recognises_its_job_pages(self, name, url):
        import re
        from core.career import companies
        firm = next(c for c in companies.premium() if c["name"] == name)
        assert any(re.search(site["job_path"], url) for site in firm["sites"])

    @pytest.mark.parametrize("url", [
        "https://www.se.com/eg/en/about-us/careers/overview/",
        "https://www.nestle.com/jobs/search-jobs",
        "https://www.pgcareers.com/mea/en/locations/egypt",
        "https://www.accenture.com/ae-en/careers",
    ])
    def test_landing_pages_are_not_jobs(self, url):
        import re
        from core.career import companies
        for firm in companies.premium():
            for site in firm.get("sites", []):
                assert not re.search(site["job_path"], url), (firm["name"], url)

    def test_gather_searches_every_premium_site(self):
        from core.career import sources
        seen = []
        with patch.object(sources, "_site", side_effect=lambda site, firm: seen.append(firm["name"]) or []), \
             patch.object(sources, "_board_by_name", return_value=[]), \
             patch.object(sources, "_workday", return_value=[]), \
             patch("core.agents.job_search_agent.JobSearchAgent._from_source", return_value=[]):
            sources.gather({"search_terms": []})
        assert {"IBM", "Accenture", "Schneider Electric", "Procter & Gamble", "Siemens",
                "Microsoft", "Nestlé"} <= set(seen)

    def test_accenture_jobs_keep_their_id(self):
        """Accenture's job pages differ only by ?id=. Stripped, every Accenture
        job would be one job, and all but the first dropped as duplicates."""
        from core.agents.job_search_agent import _canonical
        a = _canonical("https://www.accenture.com/sa-en/careers/jobdetails?id=R1_en&title=X")
        b = _canonical("https://www.accenture.com/sa-en/careers/jobdetails?id=R2_en")
        assert a == "https://www.accenture.com/sa-en/careers/jobdetails?id=R1_en"
        assert a != b
        assert _canonical("https://eg.linkedin.com/jobs/view/x-1?refId=a&trackingId=b") == \
            "https://eg.linkedin.com/jobs/view/x-1"
