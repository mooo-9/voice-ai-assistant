"""
JobSearchAgent -- finds internships and entry-level jobs in Egypt for Mo.

Reads Wuzzuf's search page and LinkedIn's logged-out job listing directly;
Wuzzuf, which puts Cloudflare's check in front of a plain read, in a headless
browser. Either one comes through a web search limited to that site when it
blocks reading. Mo dropped Bayt and Forasna. It only reads postings: it never
applies. Every job it shows is remembered in data/jobs_seen.json, so the same
posting never comes back as new.
"""
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from core.agents.base_agent import BaseAgent

_FILE = Path(__file__).parent.parent.parent / "data" / "jobs_seen.json"

# What Mo is after: internships and entry-level AI engineering and AI, data
# analyst, business analyst and SAP/ERP roles. Searched when he doesn't name a role.
TARGET_ROLES = ["data analyst", "business analyst", "business intelligence", "machine learning",
                "AI engineer", "SAP", "ERP"]

SOURCES = ["Wuzzuf", "LinkedIn"]

_MAX_SHOWN = 15
_TIMEOUT = 10
_RENDER_TIMEOUT_MS = 20000

_WUZZUF_URL = "https://wuzzuf.net/search/jobs/?q={q}&a=hpb"

# The listing LinkedIn's own logged-out jobs page loads. f_E=1,2 is internship
# and entry level; f_TPR=r604800 is the past week.
_LINKEDIN_URL = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                 "?keywords={q}&location=Egypt&f_E=1%2C2&f_TPR=r604800&start={start}")
# It answers 10 at a time; the first page alone left 35 of data analyst's 45
# postings that week unread.
_LINKEDIN_PAGE = 10
_LINKEDIN_PAGES = 3

# Site searched, and the path a single job's page has there. Listing pages
# ("Data Analyst Jobs in Cairo") come back from the same searches and are
# dropped by the path.
_SEARCH_SITES = {
    "Wuzzuf": ("wuzzuf.net", re.compile(r"wuzzuf\.net/(jobs/p|internship)/")),
    "LinkedIn": ("linkedin.com/jobs", re.compile(r"linkedin\.com/jobs/view/")),
}

# Postings above entry level, matched on whole words in the title.
_SENIOR_RE = re.compile(
    r"\b(senior|sr|lead|principal|head|manager|director|expert"
    r"|vice president|vp|avp|svp|evp)\b", re.IGNORECASE)
# Banks, matched on the employer's name: Mo doesn't want to work at one.
_BANK_RE = re.compile(
    r"\b(bank|banque|banking|bancorp|cib|qnb|hsbc|nbe|aaib|saib|citi|citibank|alexbank"
    r"|adib|mashreq|emirates nbd|attijariwafa)\b|cr[eé]dit agricole|بنك|مصرف",
    re.IGNORECASE)
_JUNIOR_RE = re.compile(
    r"\b(intern|internship|junior|jr|entry|graduate|grad|fresh|trainee)\b", re.IGNORECASE)


class JobSearchAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "job_search"

    @property
    def description(self) -> str:
        return "Finds internships and entry-level jobs in Egypt on Wuzzuf and LinkedIn."

    def run(self, task: str = "", show_all: bool = False) -> str:
        roles = [task.strip()] if task.strip() else TARGET_ROLES
        pairs = [(source, role) for role in roles for source in SOURCES]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda p: self._from_source(*p), pairs))

        jobs, read = [], set()
        for (source, _), found in zip(pairs, results):
            if found is not None:
                read.add(source)
                jobs.extend(found)
        failed = set(SOURCES) - read

        jobs = [j for j in _dedupe(jobs) if not _unwanted(j)]
        seen = _load_seen()
        new = [j for j in jobs if j["url"] not in seen]
        shown = jobs if show_all else new
        shown.sort(key=lambda j: (-_score(j, roles), SOURCES.index(j["source"])))
        shown = shown[:_MAX_SHOWN]

        for j in shown:
            seen.setdefault(j["url"], {"title": j["title"], "company": j["company"],
                                       "source": j["source"],
                                       "first_seen": date.today().isoformat()})
        _save_seen(seen)
        return _format(shown, new, roles, failed, show_all)

    # ── Sources ──────────────────────────────────────────────────────────────

    def _from_source(self, source: str, role: str) -> "list[dict] | None":
        """Jobs for one role from one board, or None when it couldn't be read
        at all. A board read directly falls back to the web search."""
        if source == "Wuzzuf":
            url = _WUZZUF_URL.format(q=urllib.parse.quote(role))
            html = self._fetch(url)
            jobs = _parse_wuzzuf(html) if html else []
            if not jobs:
                html = self._render(url)
                jobs = _parse_wuzzuf(html) if html else []
            return jobs or self._web_search(source, role)
        direct = {"LinkedIn": (_LINKEDIN_URL, _parse_linkedin)}.get(source)
        if direct:
            url, parse = direct
            jobs = []
            for start in range(0, _LINKEDIN_PAGE * _LINKEDIN_PAGES, _LINKEDIN_PAGE):
                html = self._fetch(url.format(q=urllib.parse.quote(role), start=start))
                page = parse(html) if html else []
                jobs += page
                if len(page) < _LINKEDIN_PAGE:
                    break
            if jobs:
                return jobs
        return self._web_search(source, role)

    def _fetch(self, url: str) -> str:
        try:
            import httpx
            from tools.web_tool import _BROWSER_UA
            resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True,
                             headers={"User-Agent": _BROWSER_UA,
                                      "Accept-Language": "en-US,en;q=0.9"})
            return resp.text if resp.status_code < 400 else ""
        except Exception:
            return ""

    def _render(self, url: str) -> str:
        """The page as headless Chromium has it once Cloudflare's "Just a
        moment..." check has passed by itself (a few seconds), or ""."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=_RENDER_TIMEOUT_MS)
                    page.wait_for_function("!document.title.includes('Just a moment')",
                                           timeout=_RENDER_TIMEOUT_MS)
                    page.wait_for_load_state("domcontentloaded")
                    return page.content()
                finally:
                    browser.close()
        except Exception:
            return ""

    def _web_search(self, source: str, role: str) -> "list[dict] | None":
        site, job_path = _SEARCH_SITES[source]
        return web_search_jobs(f"site:{site} {role} Egypt", job_path, source)


def web_search_jobs(query: str, job_path: "re.Pattern", source: str,
                    company: str = "") -> "list[dict] | None":
    """Job pages among a web search's results, or None when the search itself
    failed. `job_path` tells a single job's page from the site's listings."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, timelimit="m", max_results=10))
    except Exception:
        return None
    return [{"title": _clean_search_title(r.get("title", "")), "company": company,
             "location": "", "posted": "", "url": _canonical(r["href"]),
             "source": source}
            for r in results if job_path.search(r.get("href", ""))]


# ── Parsers ──────────────────────────────────────────────────────────────────

def _parse_linkedin(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    jobs = []
    for card in BeautifulSoup(html, "html.parser").select("div.base-search-card"):
        link = card.select_one("a.base-card__full-link")
        title = card.select_one(".base-search-card__title")
        if not (link and title and link.get("href")):
            continue
        company = card.select_one(".base-search-card__subtitle")
        location = card.select_one(".job-search-card__location")
        posted = card.select_one("time")
        jobs.append({
            "title": _text(title), "company": _text(company), "location": _text(location),
            "posted": _text(posted), "url": _canonical(link["href"]), "source": "LinkedIn",
        })
    return jobs


def _parse_wuzzuf(html: str) -> list[dict]:
    """Wuzzuf's class names are build hashes that change between releases, so
    cards are found by their links: the title links to the job page, and the
    company links to its careers page."""
    from bs4 import BeautifulSoup
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.select('h2 a[href*="/jobs/p/"], h2 a[href*="/internship/"]'):
        card = link
        for _ in range(4):
            card = card.parent
            if card is None or card.select_one('a[href*="/jobs/careers/"]'):
                break
        company = card.select_one('a[href*="/jobs/careers/"]') if card else None
        location = company.find_next_sibling("span") if company else None
        posted = card.find(string=re.compile(r"\bago\b")) if card else None
        jobs.append({
            "title": _text(link),
            "company": _text(company).rstrip(" -"),
            "location": _text(location),
            "posted": posted.strip() if posted else "",
            "url": _canonical(urllib.parse.urljoin("https://wuzzuf.net", link["href"])),
            "source": "Wuzzuf",
        })
    return jobs


# ── Helpers ──────────────────────────────────────────────────────────────────

def _text(tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split()) if tag else ""


def _canonical(url: str) -> str:
    """The job's address without tracking parameters, so one posting has one
    key: LinkedIn adds a fresh trackingId to every listing. An `id` is kept:
    it's the only thing telling one Accenture job page from another."""
    parts = urllib.parse.urlsplit(url)
    ids = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k in ("id", "jobId")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"),
                                    urllib.parse.urlencode(ids), ""))


def _clean_search_title(title: str) -> str:
    # "Data Analyst Intern at TMentors| Maadi, Cairo on Wuzzuf | Egypt"
    return re.split(r"\s*\|\s*|\s+–\s+", title)[0].strip()


def _unwanted(job: dict) -> bool:
    """Above entry level, or at a bank."""
    return bool(_SENIOR_RE.search(job["title"]) or _BANK_RE.search(job.get("company", "")))


def _dedupe(jobs: list[dict]) -> list[dict]:
    """One entry per posting. The same job listed on two boards counts once,
    kept from the board searched first."""
    out, urls, keys = [], set(), set()
    for j in jobs:
        key = (j["title"].lower(), j["company"].lower())
        if j["url"] in urls or (j["company"] and key in keys):
            continue
        urls.add(j["url"])
        keys.add(key)
        out.append(j)
    return out


def _score(job: dict, roles: list[str]) -> int:
    title = job["title"].lower()
    best = max(sum(w in title for w in role.lower().split()) for role in roles)
    return best + (2 if _JUNIOR_RE.search(title) else 0)


def _load_seen() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_seen(seen: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")


def _format(shown: list[dict], new: list[dict], roles: list[str],
            failed: set, show_all: bool) -> str:
    what = ", ".join(roles)
    lines = []
    if not shown:
        lines.append(f"No new jobs for {what} since the last check."
                     if not show_all else f"No jobs found for {what}.")
    else:
        new_urls = {j["url"] for j in new}
        counts = {s: sum(j["source"] == s for j in shown) for s in SOURCES}
        by_board = ", ".join(f"{s} {n}" for s, n in counts.items() if n)
        label = "jobs" if show_all else "new jobs"
        lines.append(f"{len(shown)} {label} for {what} ({by_board}):")
        for i, j in enumerate(shown, 1):
            where = ", ".join(p for p in (j["company"], j["location"]) if p)
            meta = ", ".join(p for p in (j["source"], j["posted"]) if p)
            mark = " NEW" if show_all and j["url"] in new_urls else ""
            lines.append(f"{i}. {j['title']}" + (f" -- {where}" if where else "")
                         + f" [{meta}]{mark}")
            lines.append(f"   {j['url']}")
    if failed:
        lines.append("Couldn't read: " + ", ".join(s for s in SOURCES if s in failed) + ".")
    return "\n".join(lines)
