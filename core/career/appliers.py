"""Sending one approved application.

Channels:
  email     Gmail, from Mo's account, CV attached.
  wuzzuf    Wuzzuf's apply flow in Mo's signed-in Comet.
  linkedin  LinkedIn in Mo's signed-in Comet: Easy Apply, or its Apply button
            through to the employer's own form.
  site      Any other form (company career sites): filled in and submitted.

Mo approving an application in the morning review is the go-ahead to submit it.
The form steps are decided by Claude Code on his subscription (the API when it
can't). A question the facts don't answer, or a site that needs an account he
doesn't have, stops that one application as needs_you.

Each returns (status, note) for the tracker. Every send is written to the
Trust Ledger.
"""
import re
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from core.career import profile as career_profile

_PROVENANCE = "APPROVED IN THE MORNING REVIEW"
# The CV's own type, so recruiters' mail apps preview it; unknown types go as
# plain bytes.
_CV_TYPES = {".pdf": "pdf", ".docx": "vnd.openxmlformats-officedocument.wordprocessingml.document",
             ".doc": "msword"}
BROWSER_CHANNELS = ("wuzzuf", "linkedin", "site")


def channel_for(job: dict) -> str:
    if job.get("hr_email"):
        return "email"
    url = job["url"].lower()
    if "wuzzuf.net" in url:
        return "wuzzuf"
    if "linkedin.com" in url:
        return "linkedin"
    return "site"


def apply(app: dict, profile: dict) -> tuple[str, str]:
    if app["channel"] == "email":
        return send_email(app, profile)
    return apply_in_browser(app, profile)


def send_email(app: dict, profile: dict) -> tuple[str, str]:
    from tools import gmail_tool
    service = gmail_tool.get_gmail_service() if gmail_tool.GMAIL_AVAILABLE else None
    if service is None:
        return "failed", "Gmail isn't set up."
    cv = Path(profile.get("cv_path", ""))
    if not cv.is_file():
        return "failed", "No CV file to attach -- import the CV again."

    msg = MIMEMultipart()
    msg["to"] = app["hr_email"]
    msg["subject"] = app["draft"]["subject"]
    msg.attach(MIMEText(app["draft"]["body"], "plain", "utf-8"))
    attachment = MIMEApplication(cv.read_bytes(),
                                 _subtype=_CV_TYPES.get(cv.suffix.lower(), "octet-stream"),
                                 Name=cv.name)
    attachment["Content-Disposition"] = f'attachment; filename="{cv.name}"'
    msg.attach(attachment)
    try:
        service.users().messages().send(
            userId="me", body={"raw": gmail_tool._encode_mime(msg)}).execute()
    except Exception as e:
        return "failed", f"Gmail send error: {e}"
    _ledger("gmail", app["hr_email"], app)
    return "applied", f"Emailed {app['hr_email']} with {cv.name}."


_BROWSER_STEPS = {
    "wuzzuf": ("This is a job on Wuzzuf. Mo is signed in. Click the apply button, answer "
               "the screening questions, and submit the application."),
    "linkedin": ("This is a LinkedIn job. Mo is signed in. If there is an Easy Apply button, "
                 "go through each step and submit. If there is only 'Apply', click it and "
                 "complete the application on the employer's site it opens, the same way."),
    "site": ("This is the employer's job page. Find its apply button if the form isn't "
             "open yet, fill in every field of the form going through each step, and submit."),
}

# The form steps a multi-page application takes: one screen can hold many
# fields, but each page, upload and question costs a step.
_BROWSER_STEPS_MAX = 40


def browser_task(app: dict, profile: dict) -> str:
    answers = profile.get("answers", {})
    facts = "\n".join(f"- {career_profile.ANSWER_KEYS[k]}: {v}"
                      for k, v in answers.items() if v and k in career_profile.ANSWER_KEYS)
    facts += "".join(f"\n- {q}: {a}" for q, a in profile.get("extra_answers", {}).items())
    return (
        f"Apply for '{app['title']}' at {app.get('company') or 'this company'} on behalf of "
        f"{profile.get('name') or 'Mohamed'}.\n{_BROWSER_STEPS[app['channel']]}\n\n"
        "Rules: answer questions only from the facts below or his CV; if a required "
        "question isn't covered, stop and reply 'BLOCKED: <the question>'. Attach his CV "
        "with an upload action wherever a CV/resume is asked for. Paste the cover letter "
        "where one is asked for. Never create accounts, change account settings, or pay. "
        "If the site needs an account Mo doesn't have (a sign-up page, not a sign-in he's "
        "already past), stop and reply 'BLOCKED: needs an account on <site>'. "
        "When the application is submitted, reply 'SUBMITTED'. To reply, set status "
        "'done' with a message that starts with the reply word (SUBMITTED or BLOCKED).\n\n"
        f"Facts:\n{facts or '- (none given)'}\n\nCover letter:\n{app['draft']['body']}"
    )


def apply_in_browser(app: dict, profile: dict) -> tuple[str, str]:
    from core.agents.browser_agent import BrowserAgent
    cv = profile.get("cv_path", "")
    if not Path(cv).is_file():
        return "failed", "No CV file to attach -- import the CV again."
    agent = BrowserAgent()
    agent.MAX_STEPS = _BROWSER_STEPS_MAX
    # Each step through Claude Code, like the rest of the job hunt; the API
    # when Claude Code can't answer, counted as job-hunt spend.
    message = agent.run(browser_task(app, profile), start_url=app["url"], upload_path=cv,
                        close_tab=True, telemetry_source="career", ask_fn=_ask_step)
    upper = message.strip().upper()
    if upper.startswith("BLOCKED") or "login required" in message.lower():
        return "needs_you", message
    # Live, a sent form came back as "Application already submitted successfully
    # as confirmed by the 'Thank you...' message", not the word asked for.
    if upper.startswith("SUBMITTED") or _SENT_RE.search(message):
        _ledger(app["channel"], app["url"], app)
        return "applied", message
    return "failed", message


_SENT_RE = re.compile(r"(?<!not )(?<!n't )(?:submitted successfully|successfully submitted|"
                      r"application\b[^.]{0,25}(?<!not )\breceived)", re.IGNORECASE)


def _ask_step(system: str, prompt: str, screenshot_b64: str) -> str:
    from core.career import claude
    return claude.ask(prompt, system=system, image=screenshot_b64, effort="low") or ""


def _ledger(medium: str, target: str, app: dict) -> None:
    try:
        from core import ledger
        ledger.append(category="sent", medium=medium, target=target,
                      summary=f"job application: {app['title']} at {app.get('company') or '?'}",
                      provenance=_PROVENANCE)
    except Exception:
        pass          # the ledger is a record, never a gate on sending
