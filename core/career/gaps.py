"""What the jobs keep asking for that Mo's CV doesn't show (career-ops'
upskill): every scored job's "missing" list, grouped into skills by one
Claude call and counted here, so the counts are real.

The nightly hunt works it out (refresh()) and keeps it, so asking by voice
answers at once: through Claude Code the grouping takes about a minute."""
from datetime import datetime

from core.career import claude, profile, store, tracker

_FILE = "skill_gaps.json"

_MIN_JOBS = 5
_SHOWN = 8

_SCHEMA = {
    "type": "object",
    "properties": {"groups": {"type": "array", "items": {
        "type": "object",
        "properties": {"skill": {"type": "string"},
                       "items": {"type": "array", "items": {"type": "integer"}}},
        "required": ["skill", "items"], "additionalProperties": False}}},
    "required": ["groups"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You group the requirements job postings asked for that a candidate's CV doesn't "
    "show. Put the numbered items that name the same learnable skill, tool or "
    "certificate under one short skill name ('Tableau', 'Power Automate', 'SAP FICO'). "
    "Leave out what can't be learned: years of experience, location, degree subject, "
    "languages he is a native speaker of. Leave out anything his profile already shows."
)


def summary() -> str:
    """The one the last hunt worked out; worked out now when there's none yet."""
    kept = store.load(_FILE, {})
    return kept.get("text") or refresh()


def refresh() -> str:
    text = _work_out()
    if not text.startswith(("Couldn't", "Only")):
        store.save(_FILE, {"text": text, "at": datetime.now().isoformat(timespec="seconds")})
    return text


def _work_out() -> str:
    jobs = [a for a in tracker.all_apps().values() if "score" in a and a.get("missing")]
    if len(jobs) < _MIN_JOBS:
        return (f"Only {len(jobs)} scored jobs name a gap so far; ask again after a few "
                "job hunts.")
    items, owner = [], []
    for j, app in enumerate(jobs):
        for m in app["missing"]:
            owner.append(j)
            items.append(f"{len(items)}. {m}")
    out = claude.ask(f"His profile:\n{profile.as_text()}\n\nItems:\n" + "\n".join(items),
                     system=_SYSTEM, schema=_SCHEMA, effort="low", max_tokens=4000)
    if not out:
        return "Couldn't read the gaps -- try again."
    counted = sorted(((len({owner[i] for i in g["items"] if 0 <= i < len(owner)}), g["skill"])
                      for g in out["groups"]), reverse=True)
    counted = [(n, skill) for n, skill in counted if n >= 2][:_SHOWN]
    if not counted:
        return f"No skill is missing from more than one of the {len(jobs)} jobs scored."
    return (f"What the {len(jobs)} scored jobs ask for that your CV doesn't show, most "
            "asked first: " + "; ".join(f"{skill} ({n} jobs)" for n, skill in counted) + ".")
