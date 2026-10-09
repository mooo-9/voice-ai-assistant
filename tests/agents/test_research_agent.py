from unittest.mock import patch, MagicMock
from core.agents.research_agent import ResearchAgent, _MAX_PAGE_CHARS


class TestExtractQuery:
    def test_strips_research_everything_about(self):
        assert ResearchAgent()._extract_query("research everything about NVDA") == "NVDA"

    def test_strips_summarize_the_news_about(self):
        assert ResearchAgent()._extract_query("summarize the news about Egypt economy") == "Egypt economy"

    def test_strips_latest_news_about(self):
        assert ResearchAgent()._extract_query("latest news about Tesla") == "Tesla"

    def test_research_the_best_keeps_best(self):
        # It was stripped to "padel rackets for beginners" — a different question.
        assert ResearchAgent()._extract_query(
            "research the best padel rackets for beginners") == "the best padel rackets for beginners"

    def test_plain_query_unchanged(self):
        assert ResearchAgent()._extract_query("Python data viz libraries") == "Python data viz libraries"


class TestSearch:
    """duckduckgo_search was renamed ddgs; the old package came back empty or
    with results about WordPad for a padel query."""

    def test_returns_list_on_success(self):
        mock_result = [{"title": "NVDA news", "href": "https://example.com", "body": "Strong momentum"}]
        with patch("ddgs.DDGS") as MockDDGS:
            inst = MockDDGS.return_value.__enter__.return_value
            inst.text.return_value = iter(mock_result)
            results = ResearchAgent()._search("NVDA")
        assert isinstance(results, list)
        assert results[0]["title"] == "NVDA news"

    def test_returns_empty_list_on_exception(self):
        with patch("ddgs.DDGS", side_effect=Exception("network error")):
            results = ResearchAgent()._search("anything")
        assert results == []

    def test_ads_are_not_sources(self):
        # "Rodri's transfer to Barcelona" came back with two Bing ads — airport
        # transfers and Barcelona tours — each an ~800-character tracking link.
        mock_result = [
            {"title": "Airport transfers", "href": "https://www.bing.com/aclick?ld=e8Lg&u=aHR0", "body": "Book"},
            {"title": "Rodri seals transfer", "href": "https://www.espn.com/soccer/story/rodri", "body": "Rodri"},
            {"title": "Tours", "href": "https://ad.doubleclick.net/searchads/link/click?lid=1", "body": "Tours"},
            {"title": "Deals", "href": "https://www.googleadservices.com/pagead/aclk?sa=L", "body": "Deals"},
            {"title": "DDG ad", "href": "https://duckduckgo.com/y.js?ad_domain=x.com", "body": "Ad"},
        ]
        with patch("ddgs.DDGS") as MockDDGS:
            MockDDGS.return_value.__enter__.return_value.text.return_value = iter(mock_result)
            results = ResearchAgent()._search("Rodri transfer to Barcelona")
        assert [r["href"] for r in results] == ["https://www.espn.com/soccer/story/rodri"]

    def test_ads_do_not_cost_it_sources(self):
        # Ads took slots: with two of five results ads, Rodri's research was
        # left with two sources. It asks for more and keeps the first real five.
        ads = [{"title": "Ad", "href": f"https://www.bing.com/aclick?ld={i}", "body": "ad"}
               for i in range(4)]
        real = [{"title": f"R{i}", "href": f"https://news{i}.com", "body": "news"}
                for i in range(7)]
        with patch("ddgs.DDGS") as MockDDGS:
            text = MockDDGS.return_value.__enter__.return_value.text
            text.side_effect = lambda q, max_results: iter((ads + real)[:max_results])
            results = ResearchAgent()._search("Rodri transfer to Barcelona")
        assert [r["href"] for r in results] == [f"https://news{i}.com" for i in range(5)]


_FETCH_FAILED = "[fetch failed: HTTP 403 — blocked]"


class TestReadPage:
    def test_a_plain_fetch_is_tried_before_a_browser(self):
        """Launching a hidden Chromium per page made research take ~34 s."""
        with patch("tools.web_tool.fetch_page", return_value="Padel rackets text " * 20), \
             patch("playwright.sync_api.sync_playwright") as pw:
            result = ResearchAgent()._read_page("https://example.com")
        assert result.startswith("Padel rackets text")
        pw.assert_not_called()

    def test_returns_empty_string_on_playwright_failure(self):
        with patch("tools.web_tool.fetch_page", return_value=_FETCH_FAILED), \
             patch("playwright.sync_api.sync_playwright", side_effect=Exception("no browser")):
            result = ResearchAgent()._read_page("https://example.com")
        assert result == ""

    def test_truncates_long_page_text(self):
        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        mock_page.inner_text.return_value = "A" * 5000
        with patch("tools.web_tool.fetch_page", return_value=_FETCH_FAILED), \
             patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            result = ResearchAgent()._read_page("https://example.com")
        assert len(result) <= _MAX_PAGE_CHARS
        mock_browser.close.assert_called_once()


class TestSynthesize:
    def test_returns_answer_with_sources(self):
        sources = [{"title": "NVDA drop", "url": "https://ex.com", "text": "Earnings missed"}]
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="NVDA fell due to missed earnings [1].")]
        with patch("anthropic.Anthropic") as MockCl:
            MockCl.return_value.messages.create.return_value = mock_resp
            result = ResearchAgent()._synthesize("NVDA price drop", sources)
        assert "NVDA" in result
        assert "https://ex.com" in result

    def test_the_summariser_is_told_today_s_date(self, monkeypatch):
        # Without it, a March article reads as "this week's news" in September.
        from datetime import date
        import core.agents.research_agent as ra
        monkeypatch.setattr(ra, "_today", lambda: date(2026, 9, 17))
        sources = [{"title": "EPL", "url": "https://ex.com", "text": "Zamalek won"}]
        with patch("anthropic.Anthropic") as MockCl:
            MockCl.return_value.messages.create.return_value = MagicMock(
                content=[MagicMock(text="Zamalek won [1].")])
            ResearchAgent()._synthesize("Egyptian Premier League this week", sources)
        prompt = MockCl.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "Thursday, 17 September 2026" in prompt
        assert "don't cover" in prompt

    def test_fallback_on_api_failure(self):
        sources = [{"title": "Test", "url": "https://ex.com", "text": "Some snippet here"}]
        with patch("anthropic.Anthropic", side_effect=Exception("API down")):
            result = ResearchAgent()._synthesize("test query", sources)
        assert isinstance(result, str)
        assert len(result) > 0


class TestRun:
    def test_returns_message_on_empty_search(self):
        with patch.object(ResearchAgent, "_search", return_value=[]):
            result = ResearchAgent().run("research everything about nothing")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_uses_snippets_when_page_read_fails(self):
        mock_results = [
            {"title": "T1", "href": "https://a.com", "body": "snippet A"},
            {"title": "T2", "href": "https://b.com", "body": "snippet B"},
        ]
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="Summary from snippets.")]
        with patch.object(ResearchAgent, "_search", return_value=mock_results), \
             patch.object(ResearchAgent, "_read_page", return_value=""), \
             patch("anthropic.Anthropic") as MockCl:
            MockCl.return_value.messages.create.return_value = mock_resp
            result = ResearchAgent().run("latest news about test")
        assert isinstance(result, str)
        assert len(result) > 0


class TestReadsPastBlockedPages:
    """For the pound's exchange rate the first page was blocked, only two were
    ever tried, and the snippets holding the rate were thrown away."""

    RESULTS = [
        {"title": "Blocked", "href": "https://blocked.com", "body": "blocked snippet"},
        {"title": "Good 1", "href": "https://good1.com", "body": "1 USD = 51.38 EGP"},
        {"title": "Good 2", "href": "https://good2.com", "body": "rate snippet two"},
        {"title": "Extra", "href": "https://extra.com", "body": "EGP steady this week"},
    ]

    def _run(self):
        pages = {"https://blocked.com": "", "https://good1.com": "page one text",
                 "https://good2.com": "page two text", "https://extra.com": "page three"}
        captured = {}

        def synth(self, query, sources):
            captured["sources"] = sources
            return "ok"

        with patch.object(ResearchAgent, "_search", return_value=self.RESULTS), \
             patch.object(ResearchAgent, "_read_page", side_effect=lambda url: pages[url]) as read, \
             patch.object(ResearchAgent, "_synthesize", synth):
            ResearchAgent().run("latest news about the Egyptian pound")
        return captured["sources"], read

    def test_a_blocked_page_is_skipped_for_the_next_result(self):
        sources, _ = self._run()
        urls = [s["url"] for s in sources]
        assert "https://good1.com" in urls and "https://good2.com" in urls

    def test_it_stops_reading_once_it_has_enough_pages(self):
        _, read = self._run()
        assert [c.args[0] for c in read.call_args_list] == [
            "https://blocked.com", "https://good1.com", "https://good2.com"]

    def test_snippets_of_results_not_read_still_go_in(self):
        sources, _ = self._run()
        texts = " ".join(s["text"] for s in sources)
        assert "EGP steady this week" in texts
