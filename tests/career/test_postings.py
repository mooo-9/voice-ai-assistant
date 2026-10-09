"""A posting's own text, read from the API behind its page. The first
practice run scored 40 jobs; Workday pages came back empty, LinkedIn gave its
sign-in wall and PepsiCo its menu, and the scorer marked good jobs down for
"posting lacks detail"."""
from unittest.mock import patch

import pytest

from core.agents.job_search_agent import JobSearchAgent
from core.career import pipeline, postings, scorer, sources


def _html(text):
    return f"<p>{text}</p>"


class TestEachSourcesText:
    def test_linkedin_reads_the_guest_posting_not_the_sign_in_wall(self):
        page = ('<div class="show-more-less-html__markup"><p>Build SQL reports.</p></div>'
                '<div>Sign in to see who you know</div>')
        with patch.object(sources, "_get_text", return_value=page) as get:
            text = postings.text("https://eg.linkedin.com/jobs/view/data-analyst-at-dubizzle-4301")
        assert get.call_args.args[0] == \
            "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4301"
        assert text == "Build SQL reports."

    def test_a_closed_linkedin_job_says_so(self):
        page = ('<figure class="closed-job"><figcaption class="closed-job__flavor--closed">'
                'No longer accepting applications</figcaption></figure>')
        with patch.object(sources, "_get_text", return_value=page):
            text = postings.text("https://eg.linkedin.com/jobs/view/x-4301")
        assert text == "No longer accepting applications"

    @pytest.mark.parametrize("status,closed", [(404, True), (410, True), (403, False)])
    def test_a_posting_its_source_says_is_gone_reads_as_closed(self, status, closed):
        """Gone is 404 or 410; a 403 is a block, not a closed job."""
        import httpx
        from core.career import liveness
        err = httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://x"),
                                    response=httpx.Response(status))
        with patch.object(sources, "_get_text", side_effect=err):
            text = postings.text("https://eg.linkedin.com/jobs/view/x-4301")
        assert liveness.closed(text) is closed

    def test_workday_reads_the_job_behind_its_page(self):
        detail = {"jobPostingInfo": {"jobDescription": _html("Train AI models.")}}
        with patch.object(sources, "_get_json", return_value=detail) as get:
            text = postings.text("https://valeo.wd3.myworkdayjobs.com/en-US/valeo_jobs"
                                 "/job/Cairo/AI-Engineer_REQ1")
        assert get.call_args.args[0] == ("https://valeo.wd3.myworkdayjobs.com/wday/cxs/valeo"
                                         "/valeo_jobs/job/Cairo/AI-Engineer_REQ1")
        assert text == "Train AI models."

    def test_wuzzuf_reads_description_and_requirements_past_cloudflare(self):
        page = ("<h1>Data Analyst</h1><h2>Job Description</h2><div>Build dashboards.</div>"
                "<h2>Job Requirements</h2><div>Power BI and SQL.</div><h2>Similar Jobs</h2><div>x</div>")
        with patch.object(JobSearchAgent, "_render", lambda self, url: page):
            text = postings.text("https://wuzzuf.net/jobs/p/abc-data-analyst-egec-cairo-egypt")
        assert text == "Build dashboards. Power BI and SQL."

    def test_smartrecruiters_reads_every_section(self):
        detail = {"jobAd": {"sections": {"companyDescription": {"text": _html("About talabat.")},
                                         "jobDescription": {"text": _html("Analyze orders.")}}}}
        with patch.object(sources, "_get_json", return_value=detail) as get:
            text = postings.text("https://jobs.smartrecruiters.com/DeliveryHero/744000151")
        assert get.call_args.args[0] == \
            "https://api.smartrecruiters.com/v1/companies/DeliveryHero/postings/744000151"
        assert text == "About talabat. Analyze orders."

    def test_eightfold_reads_the_position_details(self):
        detail = {"data": {"jobDescription": _html("Automate networks.")}}
        with patch.object(sources, "_get_json", return_value=detail) as get:
            text = postings.text("https://jobs.ericsson.com/careers/job/563121777194376")
        assert get.call_args.args[1] == {"position_id": "563121777194376",
                                         "domain": "ericsson.com", "hl": "en"}
        assert text == "Automate networks."

    def test_oracle_cloud_reads_the_requisition(self):
        detail = {"items": [{"ExternalDescriptionStr": _html("Fusion consulting."),
                             "ExternalQualificationsStr": _html("SQL."),
                             "ExternalResponsibilitiesStr": None}]}
        with patch.object(sources, "_get_json", return_value=detail) as get:
            text = postings.text("https://careers.oracle.com/en/sites/jobsearch/job/344587")
        (url,) = get.call_args.args
        assert "recruitingCEJobRequisitionDetails" in url
        assert 'Id="344587"' in urllib_unquote(url) and "siteNumber=CX_45001" in url
        assert text == "Fusion consulting. SQL."

    def test_phenom_reads_the_job_data_in_its_page(self):
        page = ('<script>phApp.ddo = {"jobDetail":{"data":{"job":{"title":"Intern",'
                '"description":"<p>Model data.<\\/p>","description2":"<p>Python.<\\/p>"}}}};</script>')
        with patch.object(sources, "_get_text", return_value=page):
            text = postings.text("https://careers.bcg.com/global/en/job/58478")
        assert text == "Model data. Python."

    def test_any_other_page_is_read_as_before(self):
        with patch("tools.web_tool.fetch_page", return_value="Amazon job text") as fetch:
            assert postings.text("https://www.amazon.jobs/en/jobs/1/x") == "Amazon job text"
        fetch.assert_called_once()

    @pytest.mark.parametrize("url", [
        "https://eg.linkedin.com/jobs/view/x-1", "https://wuzzuf.net/jobs/p/x",
        "https://careers.bcg.com/global/en/job/1"])
    def test_a_source_that_fails_gives_no_text_not_an_error(self, url):
        # The suite-wide guards make every real read raise or come back empty.
        assert postings.text(url) == ""

    def test_the_text_is_capped(self):
        page = f'<div class="show-more-less-html__markup">{"word " * 5000}</div>'
        with patch.object(sources, "_get_text", return_value=page):
            assert len(postings.text("https://eg.linkedin.com/jobs/view/x-1")) <= \
                postings.MAX_CHARS


class TestTheScorerGetsIt:
    def test_read_description_uses_the_posting_text(self):
        with patch.object(postings, "text", return_value="Real text") as text:
            assert scorer.read_description("https://eg.linkedin.com/jobs/view/x-1") == "Real text"
        text.assert_called_once()

    def test_a_job_that_came_with_its_text_is_not_read_again(self):
        """PepsiCo's job list carries each full description."""
        job = {"title": "Data Analyst", "company": "PepsiCo", "url": "https://www.pepsicojobs.com/main/jobs/1",
               "description": "Full PepsiCo text", "tier": "top"}
        with patch.object(scorer, "read_description") as read:
            app = pipeline._prepared(job)
        read.assert_not_called()
        assert app["description"] == "Full PepsiCo text"

    def test_jibe_keeps_the_description_its_list_gives(self):
        page = {"totalCount": 1, "jobs": [{"data": {
            "slug": "1", "title": "Data Analyst", "city": "Giza", "country": "Egypt",
            "posted_date": "2026-09-07T00:00:00+0000", "description": _html("Own the KPIs.")}}]}
        from core.career import companies
        with patch.object(sources, "_get_json", return_value=page):
            jobs = sources._jibe({"host": "www.pepsicojobs.com"}, companies.match("PepsiCo"))
        assert jobs[0]["description"] == "Own the KPIs."


def urllib_unquote(s):
    import urllib.parse
    return urllib.parse.unquote(s)
