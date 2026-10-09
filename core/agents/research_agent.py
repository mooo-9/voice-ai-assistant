"""
ResearchAgent -- synthesizes deep web research into a single coherent answer.
Searches DuckDuckGo, reads top pages (a plain fetch, or headless Playwright when
that's blocked), synthesizes via Claude Haiku.
"""
from core.agents.base_agent import BaseAgent

_MAX_SEARCH_RESULTS = 5
_MAX_PAGES_TO_READ = 2
_MAX_PAGE_CHARS = 3000  # per page, keeps Haiku prompt under token budget

# Search results that are ads: a click-tracking link is not a source.
_AD_LINKS = ("bing.com/aclick", "doubleclick.net/", "googleadservices.com/",
             "duckduckgo.com/y.js")

_STRIP_PREFIXES = [
    "research everything about",
    "research everything on",
    "find out everything about",
    "tell me everything about",
    "summarize the news about",
    "latest news about",
    "everything happening with",
    "what do we know about",
    "comprehensive analysis of",
    "compare and contrast",
    "deep dive into",
    "investigate",
    "research",          # last: "research the best X" keeps "the best X"
]


def _today():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Africa/Cairo")).date()


class ResearchAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "research"

    @property
    def description(self) -> str:
        return "Deep web research -- searches multiple sources and synthesizes answers."

    def run(self, task: str) -> str:
        query = self._extract_query(task)
        results = self._search(query)
        if not results:
            return f"No search results found for: {query}"

        # Read down the results until enough pages actually open — a blocked
        # first page used to leave one source, or none. The snippets of the
        # results not read still go in: they often hold the fact itself.
        page_texts = []
        for r in results:
            if len(page_texts) >= _MAX_PAGES_TO_READ:
                break
            text = self._read_page(r["href"])
            if text:
                page_texts.append({"url": r["href"], "title": r["title"], "text": text})

        for r in results:
            if r["href"] not in {p["url"] for p in page_texts} and r.get("body"):
                page_texts.append({"url": r["href"], "title": r["title"], "text": r["body"]})

        return self._synthesize(query, page_texts)

    def _extract_query(self, task: str) -> str:
        task_lower = task.lower()
        for prefix in _STRIP_PREFIXES:
            if task_lower.startswith(prefix):
                return task[len(prefix):].strip()
        return task.strip()

    def _search(self, query: str) -> list[dict]:
        # duckduckgo_search was renamed ddgs, and the old package now comes back
        # empty or off-topic (WordPad results for a padel query).
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                # Twice as many as needed: ads take slots.
                results = list(ddgs.text(query, max_results=_MAX_SEARCH_RESULTS * 2))
            return [r for r in results
                    if not any(ad in r.get("href", "") for ad in _AD_LINKS)
                    ][:_MAX_SEARCH_RESULTS]
        except Exception:
            return []

    def _read_page(self, url: str) -> str:
        # A plain fetch first: a hidden Chromium per page made research ~34 s.
        from tools.web_tool import fetch_page
        text = fetch_page(url, max_chars=_MAX_PAGE_CHARS)
        if not text.startswith("[") and len(text) >= 200:
            return text[:_MAX_PAGE_CHARS]
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(url, timeout=8000, wait_until="domcontentloaded")
                    text = page.inner_text("body")
                    return text[:_MAX_PAGE_CHARS]
                finally:
                    browser.close()
        except Exception:
            return ""

    def _synthesize(self, query: str, sources: list[dict]) -> str:
        context_parts = []
        for i, src in enumerate(sources, 1):
            context_parts.append(
                f"[{i}] {src['title']}\nURL: {src['url']}\n{src['text'][:1500]}"
            )
        context = "\n\n".join(context_parts)

        # Without the date, a March article read as "this week's news" in September.
        prompt = (
            "You are El Fager's research engine. Based on the sources below, "
            "answer the query in 3-5 sentences. Be specific and cite sources as [1], [2] etc. "
            "No emojis. Plain English only.\n"
            f"Today is {_today():%A, %d %B %Y}. If the query asks about a time the "
            "sources don't cover, say so plainly and give the dates they do cover.\n\n"
            f"Query: {query}\n\n"
            f"Sources:\n{context}"
        )

        try:
            import anthropic
            client = anthropic.Anthropic()
            from core.telemetry import instrument_client
            instrument_client(client, "research_agent")
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text.strip()
            source_lines = [f"[{i}] {s['url']}" for i, s in enumerate(sources, 1)]
            return answer + "\n\nSources:\n" + "\n".join(source_lines)
        except Exception:
            snippets = [
                f"[{i}] {s['title']}: {s['text'][:200]}"
                for i, s in enumerate(sources, 1)
            ]
            return f"Research on '{query}':\n" + "\n".join(snippets)
