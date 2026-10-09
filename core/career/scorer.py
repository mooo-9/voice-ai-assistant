"""How well one job fits Mo, from its posting and his profile."""
import re
from datetime import date
from pathlib import Path


# "send your CV to hr@company.com". Board and example addresses aren't HR.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NOT_HR = ("wuzzuf", "linkedin", "bayt", "forasna", "example.", "noreply", "no-reply",
           "sentry", ".png", ".jpg")

_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "0-100 fit"},
        "fit": {"type": "string", "description": "one sentence: why he fits or doesn't"},
        "missing": {"type": "array", "items": {"type": "string"}},
        "level": {"type": "string", "enum": ["internship", "entry", "mid", "senior"]},
        "in_egypt": {"type": "boolean"},
    },
    "required": ["score", "fit", "missing", "level", "in_egypt"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You screen job postings for one candidate in Egypt: a recent Business Informatics "
    "graduate looking for his first job. Score 0-100 how likely an application gets a "
    "first interview: requirements he meets, the level (graduate programmes, fresh-graduate "
    "and 0-2 years roles fit; internships only if open to graduates, not current students "
    "only; 3+ years required does not), and the field. If his graduation date has passed "
    "or falls within the next 3 months, he is a fresh graduate: don't mark him down for "
    "still studying. 'missing' lists requirements the posting states that his profile "
    "doesn't show. Judge only from the posting and the profile given."
)
# career-ops' evaluation rules, condensed by Claude from its ~27k-token prompt to
# ~1.1k tokens. Tried on 10 scored jobs: same order, tighter gaps, and it passed
# an entry Power BI role the plain prompt had put at 58.
_SYSTEM += "\n\n" + (Path(__file__).parent / "rubric.md").read_text(encoding="utf-8")


def read_description(url: str) -> str:
    from core.career import postings
    return postings.text(url)


def hr_email(text: str) -> str:
    for address in _EMAIL_RE.findall(text or ""):
        if not any(bad in address.lower() for bad in _NOT_HR):
            return address.rstrip(".")
    return ""


# A link Mo found himself comes with no title or company: the posting gives them.
_LINK_SCHEMA = {**_SCHEMA,
                "properties": {**_SCHEMA["properties"], "title": {"type": "string"},
                               "company": {"type": "string"}},
                "required": _SCHEMA["required"] + ["title", "company"]}


def score_link(text: str, profile_text: str) -> "dict | None":
    """score_all's judgement for one posting, asked now rather than batched,
    plus its title and company as the posting names them."""
    from core.career import claude
    return claude.ask(
        f"Today is {date.today().isoformat()}.\n\nCandidate profile:\n{profile_text}\n\n"
        f"Posting (give its job title and company as it names them):\n{text}",
        system=_SYSTEM, schema=_LINK_SCHEMA, effort="medium", max_tokens=8000)


def score_all(jobs: list, profile_text) -> list:
    """{"score", "fit", "missing", "level", "in_egypt"} for each job, in order,
    asked as one batch. The posting's own text is used when it was read;
    otherwise the title decides. `profile_text` is the profile, or a function
    giving each job's (Mo's ERP CV for ERP roles)."""
    from core.career import claude
    asks = []
    for job in jobs:
        posting = job.get("description") or "(posting text unavailable -- judge from the title)"
        ptext = profile_text(job) if callable(profile_text) else profile_text
        asks.append({
            "prompt": (f"Today is {date.today().isoformat()}.\n\n"
                       f"Candidate profile:\n{ptext}\n\n"
                       f"Job: {job['title']} at {job.get('company') or 'unknown company'}, "
                       f"{job.get('location') or 'Egypt'}\n\nPosting:\n{posting}"),
            # Mo would rather wait than miss a good job: batched, medium costs little.
            "system": _SYSTEM, "schema": _SCHEMA, "effort": "medium", "max_tokens": 8000,
            "model": claude.model_for(job.get("company_key") or job.get("company", "")),
        })
    out = []
    for result in claude.ask_batch(asks):
        if result is None:
            result = {"score": 0, "fit": "Couldn't be scored.", "missing": [],
                      "level": "entry", "in_egypt": True}
        result["score"] = max(0, min(100, int(result["score"])))
        out.append(result)
    return out
