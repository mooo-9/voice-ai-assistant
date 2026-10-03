"""Mo's CV as a profile the pipeline scores and writes from, plus the answers
application forms keep asking for.

Nothing here is invented: answers Mo hasn't given stay empty, and the review
page lists them so he can fill them in by voice ("my expected salary is ...").
"""
import re
from pathlib import Path

from core.career import store

# Mo keeps two CVs: the main one for AI, data and business-analysis roles, and
# an ERP one. A job whose title names ERP work is scored, written and sent
# from the ERP CV, once it is imported; everything else from the main one.
_ERP_RE = re.compile(r"\b(erp|sap|odoo|netsuite|d365|dynamics 365"
                     r"|oracle (?:ebs|fusion|financials|apps))\b", re.IGNORECASE)
_CV_FIELDS = ("name", "headline", "education", "skills", "experience",
              "projects", "certifications", "languages")

# The questions Egyptian application forms ask most. Empty until Mo answers.
ANSWER_KEYS = {
    "phone": "Phone number",
    "email": "Email address for applications",
    "linkedin_url": "LinkedIn profile URL",
    "military_status": "Military status (exempted / completed / postponed)",
    "university": "University",
    "graduation_year": "Graduation year",
    "gpa": "GPA or grade",
    "expected_salary": "Expected monthly salary (EGP)",
    "availability": "Start date / notice period",
    "english_level": "English level",
    "willing_to_relocate": "Willing to relocate or work on-site",
}

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "headline": {"type": "string"},
        "education": {"type": "string"},
        "skills": {"type": "array", "items": {"type": "string"}},
        "experience": {"type": "array", "items": {"type": "string"}},
        "projects": {"type": "array", "items": {"type": "string"}},
        "certifications": {"type": "array", "items": {"type": "string"}},
        "languages": {"type": "array", "items": {"type": "string"}},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "linkedin_url": {"type": "string"},
        "graduation_year": {"type": "string"},
        "gpa": {"type": "string"},
    },
    "required": ["name", "headline", "education", "skills", "experience", "projects",
                 "certifications", "languages", "email", "phone", "linkedin_url",
                 "graduation_year", "gpa"],
    "additionalProperties": False,
}


def load() -> dict:
    return store.load("profile.json", {})


def save(profile: dict) -> None:
    store.save("profile.json", profile)


def has_cv() -> bool:
    p = load()
    return bool(p.get("cv_path")) and Path(p["cv_path"]).exists()


def is_erp(job: dict) -> bool:
    return bool(_ERP_RE.search(job.get("title") or ""))


def for_job(profile: dict, job: dict) -> dict:
    """The profile an application to `job` is written and sent from: the ERP
    CV's for an ERP role once it's imported, the main one otherwise. A CV
    tailored to this one job (made with Mo in Claude Code) is the file sent."""
    erp = profile.get("erp") or {}
    chosen = {**profile, **erp} if erp.get("cv_path") and is_erp(job) else profile
    return {**chosen, "cv_path": job["cv_path"]} if job.get("cv_path") else chosen


def import_cv(path: str, erp: bool = False) -> str:
    """Read Mo's CV (PDF or Word) into the profile -- the ERP one when `erp`.
    Answers the CV holds (phone, GPA...) fill empty answers; ones Mo gave by
    hand are kept."""
    cv = Path(path).expanduser()
    if not cv.exists():
        return f"Error: no file at {cv}"
    text = _cv_text(cv)
    if len(text.strip()) < 200:
        return f"Error: couldn't read text from {cv.name} -- is it a scanned image? Export it as a text PDF."

    from core.career import claude
    fields = claude.ask(
        f"CV text:\n\n{text}",
        system=("Extract this CV into the given fields, copying facts exactly as written. "
                "Leave a field empty (\"\" or []) when the CV doesn't state it. Never infer "
                "or embellish."),
        schema=_EXTRACT_SCHEMA, effort="low",
    )
    if fields is None:
        return "Error: couldn't read the CV -- try again."

    profile = load()
    answers = profile.get("answers", {})
    for key in ("email", "phone", "linkedin_url", "graduation_year", "gpa"):
        if fields.get(key) and not answers.get(key):
            answers[key] = fields[key]
    cv_part = {k: v for k, v in fields.items() if k in _CV_FIELDS}
    cv_part.update({"cv_path": str(cv), "cv_text": text})
    if erp:
        profile["erp"] = cv_part
    else:
        profile.update(cv_part)
    profile["answers"] = answers
    save(profile)

    missing = missing_answers(profile)
    note = f" Still missing: {', '.join(missing)}." if missing else ""
    ats = ats_problems(text)
    if ats:
        note += " Hiring systems may misread it: " + "; ".join(ats) + "."
    which = "ERP CV (for ERP roles)" if erp else "CV"
    return (f"{which} imported: {cv_part['name'] or cv.name} -- {len(cv_part['skills'])} "
            f"skills, {len(cv_part['experience'])} experience entries.{note}")


# The headings hiring systems split a CV by (career-ops' ATS check).
_ATS_HEADINGS = {"Experience": r"experience|employment|internships?",
                 "Education": r"education", "Skills": r"skills"}


def ats_problems(text: str) -> list[str]:
    """What an applicant-tracking system reading the CV's text would miss."""
    problems = [f"no '{name}' heading" for name, words in _ATS_HEADINGS.items()
                if not re.search(rf"^\W*[\w &]*\b(?:{words})\b[\w &]*:?\s*$", text,
                                 re.IGNORECASE | re.MULTILINE)]
    if not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        problems.append("no email address in its text")
    return problems


def _cv_text(cv: Path) -> str:
    # PDFs through pymupdf, which requirements.txt installs; file_agent's
    # pdfplumber isn't in it.
    if cv.suffix.lower() == ".pdf":
        try:
            import fitz
            with fitz.open(str(cv)) as doc:
                return "\n".join(page.get_text() for page in doc)
        except Exception:
            return ""
    from core.agents.file_agent import FileAgent
    return FileAgent()._extract_content(cv)


def set_answer(key: str, answer: str) -> str:
    """One of the usual questions, or any other a form asked (kept word for
    word in "extra_answers" and given to every form from then on)."""
    known = key.strip().lower().replace(" ", "_")
    profile = load()
    if known in ANSWER_KEYS:
        profile.setdefault("answers", {})[known] = answer.strip()
        save(profile)
        return f"Saved: {ANSWER_KEYS[known]} = {answer.strip()}"
    if not key.strip() or not answer.strip():
        return "Error: give both the question and the answer."
    profile.setdefault("extra_answers", {})[key.strip()] = answer.strip()
    save(profile)
    return f"Saved for application forms: {key.strip()} = {answer.strip()}"


def missing_answers(profile: "dict | None" = None) -> list[str]:
    answers = (profile if profile is not None else load()).get("answers", {})
    return [k for k in ANSWER_KEYS if not answers.get(k)]


def as_text(profile: "dict | None" = None) -> str:
    """The profile as the prompts see it. Before a CV is imported it is only
    what El Fager knows from Mo's profile.json -- scores and drafts made from it
    are practice runs."""
    p = profile if profile is not None else load()
    if not p.get("cv_text"):
        return ("Mohamed (Mo), Cairo, Egypt. Business Informatics graduate. "
                "No CV imported yet: nothing else is known.")
    answers = p.get("answers", {})
    lines = [f"Name: {p.get('name', '')}", f"Headline: {p.get('headline', '')}",
             f"Education: {p.get('education', '')}"]
    for key in ("skills", "experience", "projects", "certifications", "languages"):
        if p.get(key):
            lines.append(f"{key.capitalize()}: " + "; ".join(p[key]))
    lines += [f"{ANSWER_KEYS[k]}: {v}" for k, v in answers.items() if v and k in ANSWER_KEYS]
    return "\n".join(lines)
