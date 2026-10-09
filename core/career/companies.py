"""The companies Mo is aiming for: the Big 4 first, then the best employers in
Egypt. The list ships in the package (data/ is gitignored); matching is by
alias as whole words, so "PwC Middle East" and "_VOIS" land on their firm."""
import json
import re
from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).parent / "companies.json"

TIER_RANK = {"big4": 0, "top": 1, "": 2}


@lru_cache(maxsize=1)
def all_companies() -> list[dict]:
    return json.loads(_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[re.Pattern, dict]]:
    out = []
    for c in all_companies():
        alts = "|".join(re.escape(a) for a in c["aliases"])
        out.append((re.compile(rf"(?<!\w)({alts})(?!\w)", re.IGNORECASE), c))
    return out


def match(company: str) -> "dict | None":
    """The target company a posting's company name belongs to, if any."""
    if not company:
        return None
    for pattern, c in _patterns():
        if pattern.search(company):
            return c
    return None


def mentions(company: dict, text: str) -> bool:
    """Whether `text` names this company (by any alias)."""
    return any(c is company and p.search(text or "") for p, c in _patterns())


def big4() -> list[dict]:
    return [c for c in all_companies() if c["tier"] == "big4"]


# The kinds of careers site a company can list; core.career.sources reads each.
CAREER_SITE_KEYS = ("sites", "workday", "smartrecruiters", "amazon_jobs", "oracle_cloud",
                    "eightfold", "jibe", "phenom", "pages")


def with_career_sites() -> list[dict]:
    """The companies whose own careers site the nightly search reads."""
    return [c for c in all_companies() if any(c.get(k) for k in CAREER_SITE_KEYS)]


def premium() -> list[dict]:
    """The Big 4 and the companies Mo picked to get the same treatment
    ("opus": true): their own career sites searched, the stronger model."""
    return [c for c in all_companies() if c["tier"] == "big4" or c.get("opus")]
