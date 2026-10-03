"""Where the pipeline finds jobs: every search term on Wuzzuf and LinkedIn,
the Big 4 and Mo's other picks by name on both, and every target company's own
career site that can be read."""
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from core.agents.job_search_agent import (
    SOURCES, JobSearchAgent, _dedupe, _unwanted, web_search_jobs,
)
from core.career import companies

_EGYPT_RE = re.compile(r"egypt|cairo|giza|alexandria", re.IGNORECASE)
_TIMEOUT = 15
# Workday answers 20 postings at a time; five pages is past any firm's Egypt list.
_WORKDAY_PAGE = 20
_WORKDAY_MAX = 100
# Jibe answers 10 at a time; PepsiCo lists about 100 in Egypt.
_JIBE_MAX_PAGES = 20


def gather(settings: dict) -> list[dict]:
    """Every job found, below senior level and not at a bank, one entry per posting, each tagged
    with its target company's tier ("big4", "top" or ""). The Big 4 and Mo's
    other picks are also searched by name and on their own career sites."""
    agent = JobSearchAgent()
    calls = [(agent._from_source, (src, term))
             for term in settings["search_terms"] for src in SOURCES]
    for firm in companies.premium():
        calls += [(_board_by_name, (agent, src, firm)) for src in SOURCES]
    for firm in companies.with_career_sites():
        for key, reader in READERS.items():
            calls += [(globals()[reader], (entry, firm)) for entry in firm.get(key, [])]

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda c: _safe(*c), calls))

    jobs = [j for found in results for j in found if not _unwanted(j)]
    jobs = _dedupe(jobs)
    for j in jobs:
        target = companies.match(j["company"])
        j["tier"] = target["tier"] if target else ""
        j["company_key"] = target["name"] if target else j["company"]
    return _once_each(jobs)


# A title this long is one posting wherever it's listed ("Business Analyst -
# MENA, A-Now" under Amazon and reposted by ACCA Careers); "Data Analyst" isn't.
_SPECIFIC_TITLE = 25


def _once_each(jobs: list[dict]) -> list[dict]:
    """One entry per job: the same title at the same target firm under
    another name (BCG X on LinkedIn, BCG on its site), or the same specific
    title anywhere, counts once. The first found is kept."""
    out, seen = [], set()
    for j in jobs:
        title = j["title"].strip().lower()
        keys = {(title, j["company_key"].lower())}
        if len(title) >= _SPECIFIC_TITLE:
            keys.add((title, ""))
        if keys & seen:
            continue
        seen |= keys
        out.append(j)
    return out


def _safe(fn, args) -> list[dict]:
    try:
        return fn(*args) or []
    except Exception:
        return []


def _board_by_name(agent: JobSearchAgent, source: str, firm: dict) -> list[dict]:
    """The firm's name searched on a board; only postings the firm itself
    placed, not every job that mentions it."""
    found = agent._from_source(source, firm["name"]) or []
    return [j for j in found if companies.match(j["company"]) is firm]


def _site(site: dict, firm: dict) -> "list[dict] | None":
    return web_search_jobs(site["query"], re.compile(site["job_path"]),
                           f"{firm['name']} careers", company=firm["name"])


def _get_json(url: str, params: "dict | None" = None) -> dict:
    """`params=None` keeps the URL's own query; httpx drops it for any dict, even {}."""
    import httpx
    from tools.web_tool import _BROWSER_UA
    resp = httpx.get(url, params=params, timeout=_TIMEOUT, headers={"User-Agent": _BROWSER_UA})
    resp.raise_for_status()
    return resp.json()


def _get_text(url: str) -> str:
    import httpx
    from tools.web_tool import _BROWSER_UA
    resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True,
                     headers={"User-Agent": _BROWSER_UA})
    resp.raise_for_status()
    return resp.text


def _post_json(url: str, body: dict) -> dict:
    import httpx
    resp = httpx.post(url, timeout=_TIMEOUT, json=body)
    resp.raise_for_status()
    return resp.json()


def _workday(wd: dict, firm: dict) -> list[dict]:
    """Workday's public job search, the one behind the firm's careers page.
    It gives the total only on the first page."""
    url = f"https://{wd['host']}/wday/cxs/{wd['tenant']}/{wd['site']}/jobs"
    postings, total = [], None
    while len(postings) < _WORKDAY_MAX:
        page = _post_json(url, {"appliedFacets": {}, "limit": _WORKDAY_PAGE,
                                "offset": len(postings), "searchText": "Egypt"})
        found = page.get("jobPostings", [])
        postings += found
        total = page.get("total", 0) if total is None else total
        if not found or len(postings) >= total:
            break
    jobs = []
    for p in postings:
        where = p.get("locationsText", "")
        if not (_EGYPT_RE.search(where) or _EGYPT_RE.search(p.get("title", ""))):
            continue
        jobs.append({
            "title": p.get("title", ""), "company": firm["name"], "location": where,
            "posted": p.get("postedOn", ""),
            "url": f"https://{wd['host']}/en-US/{wd['site']}{p.get('externalPath', '')}",
            "source": f"{firm['name']} careers",
        })
    return jobs


def _smartrecruiters(sr: dict, firm: dict) -> list[dict]:
    """SmartRecruiters' public postings for the firm, in Egypt only."""
    page = _get_json(f"https://api.smartrecruiters.com/v1/companies/{sr['company']}/postings",
                     {"country": "eg", "limit": 100})
    return [{"title": p["name"], "company": firm["name"],
             "location": p.get("location", {}).get("city", ""),
             "posted": p.get("releasedDate", "")[:10],
             "url": f"https://jobs.smartrecruiters.com/{sr['company']}/{p['id']}",
             "source": f"{firm['name']} careers"}
            for p in page.get("content", [])]


def _amazon(aj: dict, firm: dict) -> list[dict]:
    """amazon.jobs' own search, for one country."""
    page = _get_json("https://www.amazon.jobs/en/search.json",
                     {"normalized_country_code[]": aj["country"], "result_limit": 100})
    return [{"title": j["title"], "company": firm["name"], "location": j.get("city", ""),
             "posted": j.get("posted_date", ""), "url": "https://www.amazon.jobs" + j["job_path"],
             "source": f"{firm['name']} careers"}
            for j in page.get("jobs", [])]


def _oracle_cloud(oc: dict, firm: dict) -> list[dict]:
    """Oracle Recruiting Cloud's public search, as the firm's careers page
    asks it for Egypt (Oracle's and Dell's sites)."""
    url = (f"https://{oc['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
           "?onlyData=true&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
           f"&finder=findReqs;siteNumber={oc['site']},facetsList=LOCATIONS%3BFLEX_FIELDS,"
           "limit=100,location=Egypt,sortBy=POSTING_DATES_DESC")
    reqs = _get_json(url)["items"][0]["requisitionList"]
    return [{"title": r["Title"], "company": firm["name"], "location": r.get("PrimaryLocation", ""),
             "posted": r.get("PostedDate", ""), "url": oc["job_url"] + str(r["Id"]),
             "source": f"{firm['name']} careers"}
            for r in reqs]


def _eightfold(ef: dict, firm: dict) -> list[dict]:
    """Eightfold's public search (Ericsson's careers site). A posting open in
    several countries keeps only its Egypt locations."""
    positions, start = [], 0
    while True:
        data = _get_json(f"https://{ef['host']}/api/pcsx/search",
                         {"domain": ef["domain"], "query": "", "location": "Egypt",
                          "start": start})["data"]
        found = data.get("positions", [])
        positions += found
        start += len(found)
        if not found or start >= data.get("count", 0):
            break
    return [{"title": p["name"], "company": firm["name"],
             "location": "; ".join(l for l in p.get("locations", []) if _EGYPT_RE.search(l)),
             "posted": date.fromtimestamp(p["postedTs"]).isoformat() if p.get("postedTs") else "",
             "url": f"https://{ef['host']}{p['positionUrl']}", "source": f"{firm['name']} careers"}
            for p in positions]


def _jibe(jb: dict, firm: dict) -> list[dict]:
    """Jibe's public job search (PepsiCo's careers site), page by page. Its
    list carries each job's full text, which the job page only draws in."""
    from core.career.postings import MAX_CHARS, _plain
    postings, seen, page = [], 0, 1
    while page <= _JIBE_MAX_PAGES:
        found = _get_json(f"https://{jb['host']}/api/jobs", {"location": "Egypt", "page": page})
        batch = [j["data"] for j in found.get("jobs", [])]
        postings += [d for d in batch if d.get("country") == "Egypt"]
        seen += len(batch)
        if not batch or seen >= found.get("totalCount", 0):
            break
        page += 1
    return [{"title": d["title"], "company": firm["name"], "location": d.get("city", ""),
             "posted": d.get("posted_date", "")[:10],
             "url": f"https://{jb['host']}/main/jobs/{d['slug']}", "source": f"{firm['name']} careers",
             "description": _plain(d.get("description", ""))[:MAX_CHARS]}
            for d in postings]


def _phenom(ph: dict, firm: dict) -> list[dict]:
    """A Phenom careers site's own search, the call its search page makes
    (BCG's and Majid Al Futtaim's), Egypt only, all in one answer."""
    found = _post_json(f"https://{ph['host']}/widgets", {
        "ddoKey": "refineSearch", "refNum": ph["ref"], "lang": "en_global",
        "siteType": "external", "from": 0, "size": 100, "jobs": True,
        "selected_fields": {"country": ["Egypt"]}})
    return [{"title": j["title"], "company": firm["name"],
             "location": j.get("cityStateCountry") or j.get("location", ""),
             "posted": j.get("postedDate", "")[:10],
             "url": f"https://{ph['host']}/global/en/job/{j['jobId']}",
             "source": f"{firm['name']} careers"}
            for j in found["refineSearch"]["data"]["jobs"]]


def _page(pg: dict, firm: dict) -> list[dict]:
    """The job links on a careers search page that lists them in its HTML
    (L'Oréal's). A job linked twice keeps its first link text, the title."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_get_text(pg["url"]), "html.parser")
    titles: dict = {}
    for a in soup.select(f'a[href*="{pg["job_path"]}"]'):
        titles.setdefault(urllib.parse.urljoin(pg["url"], a["href"]), a.get_text(" ", strip=True))
    return [{"title": title, "company": firm["name"], "location": "", "posted": "", "url": url,
             "source": f"{firm['name']} careers"}
            for url, title in titles.items()]


# The reader for each kind of careers site in companies.json, by name so it
# is looked up when called.
READERS = {"sites": "_site", "workday": "_workday", "smartrecruiters": "_smartrecruiters",
           "amazon_jobs": "_amazon", "oracle_cloud": "_oracle_cloud", "eightfold": "_eightfold",
           "jibe": "_jibe", "phenom": "_phenom", "pages": "_page"}
