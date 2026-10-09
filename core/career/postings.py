"""A posting's own text, read from the API behind its page.

Most careers pages build the job text with JavaScript, so a plain read gets
nothing (Workday), a sign-in wall (LinkedIn) or the site's menu (PepsiCo).
The first practice run scored 40 jobs that way and marked good ones down for
"posting lacks detail". Each source here is read where its own page reads it
from; any other page is read as before.
"""
import json
import re
import urllib.parse

from core.career import companies, sources

MAX_CHARS = 4000


def text(url: str) -> str:
    """The posting's text, capped; "" when its source couldn't be read. A
    posting its source says is gone (404, 410) reads as closed."""
    import httpx
    for pattern, read in _READERS:
        m = re.match(pattern, url)
        if m:
            try:
                return _cap(read(url, m))
            except httpx.HTTPStatusError as e:
                return ("Job no longer available." if e.response.status_code in (404, 410)
                        else "")
            except Exception:
                return ""
    from tools.web_tool import fetch_page
    page = fetch_page(url, max_chars=MAX_CHARS)
    return "" if page.startswith("[") else page


def _cap(s: str) -> str:
    return s[:MAX_CHARS]


def _plain(html: str) -> str:
    from bs4 import BeautifulSoup
    return " ".join(BeautifulSoup(html or "", "html.parser").get_text(" ").split())


def _linkedin(url, m):
    from bs4 import BeautifulSoup
    page = sources._get_text(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m[1]}")
    soup = BeautifulSoup(page, "html.parser")
    # A closed job keeps its page, with "No longer accepting applications" on top.
    banner = soup.select_one(".closed-job__flavor--closed")
    body = soup.select_one(".show-more-less-html__markup, .description__text")
    return " ".join(_plain(str(t)) for t in (banner, body) if t)


def _workday(url, m):
    host, site, path = m[1], m[2], m[3]
    tenant = host.split(".")[0]
    detail = sources._get_json(f"https://{host}/wday/cxs/{tenant}/{site}{path}")
    return _plain(detail["jobPostingInfo"]["jobDescription"])


def _wuzzuf(url, m):
    """Wuzzuf's job page past Cloudflare, like its search: the Job
    Description and Job Requirements sections."""
    from bs4 import BeautifulSoup
    from core.agents.job_search_agent import JobSearchAgent
    soup = BeautifulSoup(JobSearchAgent()._render(url), "html.parser")
    parts = []
    for label in ("Job Description", "Job Requirements"):
        head = soup.find(lambda t: t.name in ("h2", "h3", "h4") and t.get_text(strip=True) == label)
        section = head.find_next_sibling() if head else None
        if section:
            parts.append(_plain(str(section)))
    return " ".join(parts)


def _smartrecruiters(url, m):
    detail = sources._get_json(
        f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}")
    return " ".join(_plain(s.get("text", "")) for s in detail["jobAd"]["sections"].values())


def _eightfold(url, m):
    domain = next(ef["domain"] for c in companies.all_companies()
                  for ef in c.get("eightfold", []) if ef["host"] == m[1])
    detail = sources._get_json(f"https://{m[1]}/api/pcsx/position_details",
                               {"position_id": m[2], "domain": domain, "hl": "en"})
    return _plain(detail.get("data", detail).get("jobDescription", ""))


def _oracle_cloud(url, m):
    oc = next(oc for c in companies.all_companies() for oc in c.get("oracle_cloud", [])
              if url.startswith(oc["job_url"]))
    job_id = url[len(oc["job_url"]):]
    detail = sources._get_json(
        f"https://{oc['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
        f"?expand=all&onlyData=true&finder=ById;Id=%22{job_id}%22,siteNumber={oc['site']}")
    req = detail["items"][0]
    return " ".join(_plain(req.get(k) or "") for k in
                    ("ExternalDescriptionStr", "ExternalQualificationsStr",
                     "ExternalResponsibilitiesStr") if req.get(k))


def _phenom(url, m):
    """A Phenom job page carries the job's data in its page script."""
    page = sources._get_text(url)
    start = page.index("{", page.index('"jobDetail"'))
    job = json.JSONDecoder().raw_decode(page[start:])[0]["data"]["job"]
    return " ".join(_plain(job.get(k, "")) for k in ("description", "description2") if job.get(k))


def _hosts(key: str) -> str:
    """The hosts companies.json lists for one kind of careers site, as a regex."""
    hosts = {e["host"] for c in companies.all_companies() for e in c.get(key, [])}
    return "|".join(re.escape(h) for h in sorted(hosts)) or "(?!)"


def _oracle_prefixes() -> str:
    urls = {oc["job_url"] for c in companies.all_companies() for oc in c.get("oracle_cloud", [])}
    return "|".join(re.escape(u) for u in sorted(urls)) or "(?!)"


# Each source: the posting URL it recognises, and how its text is read.
_READERS = [
    (r"https://[a-z]+\.linkedin\.com/jobs/view/(?:.*-)?(\d+)/?$", _linkedin),
    (r"https://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/.+)$",
     _workday),
    (r"https://wuzzuf\.net/(?:jobs/p|internship)/", _wuzzuf),
    (r"https://jobs\.smartrecruiters\.com/([^/]+)/(\d+)", _smartrecruiters),
    (rf"https://({_hosts('eightfold')})/careers/job/(\d+)", _eightfold),
    (rf"(?:{_oracle_prefixes()})\w+$", _oracle_cloud),
    (rf"https://(?:{_hosts('phenom')})/[a-z]+/[a-z]{{2}}/job/", _phenom),
]
