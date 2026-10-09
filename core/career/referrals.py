"""People at the target companies who could refer Mo, and what to send them.

An employee's referral gets a CV read where a cold application often
doesn't. This finds people through a web search of public LinkedIn profiles
(alumni of Mo's university first), and drafts a connection note and a
referral request for each. Mo sends them himself: LinkedIn restricts accounts
that message at machine pace, and a referral ask should come from him.

People Mo already knows come first: his LinkedIn connections export
(Connections.csv), imported once, keeps only those at a target company
(data/career/connections.json). They need no connection note, only the ask.

Stored in data/career/referrals.json:
  to_send -> sent -> replied / referred     (or skipped)
"""
import csv
import hashlib
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

from core.career import companies, profile, store, tracker

_FILE = "referrals.json"
_CONNECTIONS = "connections.json"
_PROFILE_URL = re.compile(r"linkedin\.com/in/[^/?#]+")
# LinkedIn's cap on a connection note: 200 characters on a free account (300 on
# Premium). Past it the note is cut off or refused (career-ops checked both).
_NOTE_LIMIT = 200

_SCHEMA = {
    "type": "object",
    "properties": {
        "note": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": ["note", "message"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You write short, genuine networking messages for Mohamed, a Business Informatics "
    "graduate in Cairo looking for his first job. Use only facts from his profile. "
    "'note' is a LinkedIn connection note under 190 characters: who he is, one real "
    "connection point (same university if they share it), no ask for a job yet. "
    "'message' is the follow-up after they accept, under 100 words: a specific, polite "
    "request for a referral to the named role (or advice on applying if no role is "
    "named), offering to send his CV. No flattery, no clichés, no placeholders. "
    "If the person is already one of his connections, 'note' is empty and 'message' "
    "goes to them straight away."
)


def all_referrals() -> dict:
    return store.load(_FILE, {})


def _save(refs: dict) -> None:
    store.save(_FILE, refs)


# Where LinkedIn's export lands: Connections.csv, or the zip it comes in.
DOWNLOADS = Path.home() / "Downloads"


def _find_export() -> "Path | None":
    """The newest Connections.csv or LinkedIn data-export zip in Downloads."""
    found = [p for p in DOWNLOADS.glob("*") if p.name.lower() == "connections.csv"
             or (p.suffix.lower() == ".zip" and "linkedin" in p.name.lower())]
    return max(found, key=lambda p: p.stat().st_mtime, default=None)


def _read_export(path: Path) -> str:
    if path.suffix.lower() != ".zip":
        return path.read_text(encoding="utf-8-sig")
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if n.lower().endswith("connections.csv")), None)
        if name is None:
            raise OSError("no Connections.csv inside it -- ask LinkedIn for the Connections export")
        return z.read(name).decode("utf-8-sig")


def import_connections(path: "str | None" = None) -> str:
    """Read LinkedIn's Connections.csv export (or the zip it comes in; without
    a path, the newest one in Downloads) and keep the people at target
    companies. The export opens with a "Notes:" preamble, so the header row is
    found by its columns, not its position."""
    file = Path(path) if path else _find_export()
    if file is None:
        return ("No LinkedIn export in Downloads. On LinkedIn: Settings > Data privacy > "
                "Get a copy of your data > Connections. LinkedIn emails the file; save it "
                "to Downloads and ask again.")
    try:
        text = _read_export(file)
    except (OSError, zipfile.BadZipFile) as e:
        return f"Error: couldn't read {file.name}: {e}"
    rows = list(csv.reader(io.StringIO(text)))
    head = next((i for i, r in enumerate(rows)
                 if {"first name", "company"} <= {c.strip().lower() for c in r}), None)
    if head is None:
        return "Error: that isn't LinkedIn's Connections.csv (no First Name / Company columns)."
    cols = [c.strip().lower() for c in rows[head]]
    people = {}
    for r in rows[head + 1:]:
        row = dict(zip(cols, (c.strip() for c in r)))
        target = companies.match(row.get("company", ""))
        name = f"{row.get('first name', '')} {row.get('last name', '')}".strip()
        if not (target and name and row.get("url")):
            continue
        people[row["url"]] = {"name": name, "headline": row.get("position", ""),
                              "url": row["url"], "company": target["name"],
                              "alumni": False, "connected": True}
    store.save(_CONNECTIONS, list(people.values()))
    firms = sorted({p["company"] for p in people.values()})
    return (f"{len(people)} of your connections work at target companies"
            + (f": {', '.join(firms)}." if firms else ".")
            + " They come first when finding referrals.")


def connections_at(firm: str) -> list[dict]:
    return [p for p in store.load(_CONNECTIONS, []) if p["company"] == firm]


def search_people(company: str, university: str = "", limit: int = 10) -> list[dict]:
    """Public LinkedIn profiles of people at `company` in Cairo, via web search."""
    target = companies.match(company)
    firm = target["name"] if target else company
    alias = (target["aliases"][0] if target else company)
    query = f'site:linkedin.com/in "{alias}" Cairo' + (f' "{university}"' if university else "")
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=limit * 2))
    except Exception:
        return []
    people = []
    for r in results:
        m = _PROFILE_URL.search(r.get("href", ""))
        title = r.get("title", "")
        if not m or (target and not companies.mentions(target, f"{title} {r.get('body', '')}")):
            continue
        parts = [p.strip() for p in re.split(r"\s+[-–|]\s+", title) if p.strip()]
        if not parts or parts[0].lower() == "linkedin":
            continue
        people.append({
            "name": parts[0],
            "headline": " - ".join(p for p in parts[1:] if p.lower() != "linkedin"),
            "url": "https://www." + m.group(0),
            "company": firm,
            "alumni": bool(university) and university.lower() in
                      f"{title} {r.get('body', '')}".lower(),
        })
    return people[:limit]


def draft(person: dict, role: str = "") -> "dict | None":
    from core.career import claude
    out = claude.ask(
        f"His profile:\n{profile.as_text()}\n\n"
        f"Person: {person['name']}, {person['headline'] or 'works'} at {person['company']}"
        f"{' (went to the same university)' if person.get('alumni') else ''}"
        f"{' (already one of his connections)' if person.get('connected') else ''}.\n"
        f"Role he wants to be referred for: {role or '(none named -- ask for advice)'}",
        system=_SYSTEM, schema=_SCHEMA, effort="low", max_tokens=3000,
        model=claude.model_for(person["company"]))
    if out:
        out["note"] = "" if person.get("connected") else out["note"][:_NOTE_LIMIT]
    return out


def find(company: "str | None" = None, count: int = 5) -> str:
    """Find and draft up to `count` new people to ask, Mo's own connections
    at each firm before strangers. Without a company, the targets with jobs in
    play come first: Big 4, then top companies."""
    uni = profile.load().get("answers", {}).get("university", "")
    targets = [company] if company else _companies_in_play()
    refs = all_referrals()
    known = {r["url"] for r in refs.values()}
    added = []
    for firm in targets:
        if len(added) >= count:
            break
        target = companies.match(firm)
        found = connections_at(target["name"] if target else firm)
        found += search_people(firm, uni) or (search_people(firm) if uni else [])
        for person in found:
            if len(added) >= count or person["url"] in known:
                continue
            role = _role_at(person["company"])
            text = draft(person, role)
            if not text:
                continue
            rid = hashlib.sha1(person["url"].encode()).hexdigest()[:8]
            refs[rid] = {**person, **text, "id": rid, "role": role, "status": "to_send",
                         "found_at": datetime.now().isoformat(timespec="seconds")}
            known.add(person["url"])
            added.append(refs[rid])
    _save(refs)
    if not added:
        return "No new people found to ask for a referral."
    return (f"{len(added)} people to ask for a referral: "
            + "; ".join(f"{r['name']} ({r['company']})" for r in added)
            + ". The notes are on the review page, ready to copy.")


def _companies_in_play() -> list[str]:
    """Target companies with an application ready, sent or waiting, Big 4 first,
    then the rest of the Big 4 and top list."""
    in_play = {a.get("company_key") for a in tracker.all_apps().values()
               if a.get("tier") and a.get("status") in
               ("ready", "approved", "applied", "needs_you", "practice")}
    ranked = sorted(companies.all_companies(),
                    key=lambda c: (c["name"] not in in_play, companies.TIER_RANK[c["tier"]]))
    return [c["name"] for c in ranked]


def _role_at(firm: str) -> str:
    for a in tracker.all_apps().values():
        if a.get("company_key") == firm and a.get("status") in ("ready", "approved", "applied", "needs_you"):
            return a["title"]
    return ""


def to_send() -> list[dict]:
    return sorted((r for r in all_referrals().values() if r["status"] == "to_send"),
                  key=lambda r: (not r.get("connected"), not r.get("alumni"), r["found_at"]))


def mark(ref_id: str, status: str) -> str:
    if status not in ("sent", "replied", "referred", "skipped"):
        return "Error: status must be sent, replied, referred or skipped."
    refs = all_referrals()
    if ref_id not in refs:
        return f"Error: no referral '{ref_id}'."
    refs[ref_id]["status"] = status
    refs[ref_id][f"{status}_at"] = datetime.now().isoformat(timespec="seconds")
    _save(refs)
    return f"{refs[ref_id]['name']} ({refs[ref_id]['company']}): {status}."


def list_text() -> str:
    pending = to_send()
    refs = all_referrals().values()
    sent = sum(1 for r in refs if r["status"] == "sent")
    referred = [r for r in refs if r["status"] == "referred"]
    lines = [f"{len(pending)} referral notes to send, {sent} sent and waiting, "
             f"{len(referred)} referred."]
    for r in pending[:8]:
        lines.append(f"- {r['id']}: {r['name']}, {r['headline'] or r['company']}"
                     f"{' (connection)' if r.get('connected') else ' (alumni)' if r.get('alumni') else ''}"
                     f" -- {r['url']}")
    return "\n".join(lines)
