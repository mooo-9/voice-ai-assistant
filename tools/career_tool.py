"""The job-application pipeline as tools the brain can call. The work lives
in core/career/; these only check inputs and say what happened."""
from datetime import datetime, timedelta

# Tonight's job hunt: armed from the AUTOMATIONS panel, run once by the
# scheduler (which, unlike queued tasks, runs through the night), then gone.
HUNT_JOB_ID = "job_hunt_tonight"
_HUNT_AT = 2          # 02:00, so the batch is ready by morning


def _over_budget() -> str:
    """Why a run can't start this month, or "" when it can."""
    from core.career import pipeline
    from core.telemetry import cost_this_month, monthly_budget
    # A run the budget can't cover would be cut off halfway by the monthly cap.
    budget, spent, estimate = monthly_budget(), cost_this_month(), pipeline.run_estimate()
    if budget > 0 and spent + estimate > budget:
        return (f"Not starting the job hunt: this month has used ${spent:.2f} of the "
                f"${budget:.2f} API budget, and a run costs about ${estimate:.2f}. "
                "It can run on the 1st, or now with a lower daily target.")
    return ""


def prepare_applications() -> str:
    from core.career import pipeline
    refusal = _over_budget()
    if refusal:
        return refusal
    if not pipeline.start_in_background(pipeline.prepare_batch):
        return "The pipeline is already running -- the batch will be ready when it finishes."
    return ("Preparing today's batch in the background: searching the boards and the Big 4 "
            "career sites, scoring each job, and drafting applications. Claude Code does the "
            "thinking on your subscription, so it usually takes a few minutes; "
            f"review it at {pipeline.review_url()} when it's done.")


def hunt_tonight() -> str:
    """The AUTOMATIONS button: arm one job hunt for 02:00 tonight, or take it
    back if it's already armed. Never repeats on its own."""
    from core import scheduler
    sched = scheduler.get_instance() or scheduler.ElFagerScheduler()
    if hunt_armed():
        sched.remove_job(HUNT_JOB_ID)
        return "Tonight's job hunt is off."
    refusal = _over_budget()
    if refusal:
        return refusal
    now = datetime.now()
    at = now.replace(hour=_HUNT_AT, minute=0, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    sched.add_job({"id": HUNT_JOB_ID, "name": "Job hunt", "enabled": True,
                   "trigger": {"type": "date", "run_date": at.isoformat()},
                   "action": {"type": "tool", "tool": "run_job_hunt", "args": {}}})
    return "Job hunt set for tonight at 2 AM. The batch is ready by morning."


def hunt_armed() -> bool:
    """A hunt set for a time still to come. One El Fager slept through (the PC
    was off at 02:00) is left in the file but no longer counts."""
    from core import scheduler
    now = datetime.now().isoformat()
    return any(j.get("id") == HUNT_JOB_ID and j["trigger"]["run_date"] > now
               for j in scheduler._load_schedules())


def run_job_hunt() -> str:
    """What the scheduler runs at 02:00: replies first, then the hunt. Says
    nothing at that hour -- the batch reports itself when it's ready, and a
    run that can't start says why on his phone."""
    from core.career import pipeline
    try:    # Gmail being down mustn't stop the hunt
        check_application_replies()
    except Exception:
        pass
    said = prepare_applications()
    if not said.startswith("Preparing"):
        pipeline._notify(said)
    return ""


def review_applications() -> str:
    from core.career import pipeline
    return pipeline.review_text()


def approve_applications(skip: "list[str] | None" = None, only: "list[str] | None" = None) -> str:
    from core.career import pipeline
    return pipeline.approve(skip=skip, only=only)


def application_status() -> str:
    from core.career import pipeline
    return pipeline.status_text()


def skill_gaps() -> str:
    from core.career import gaps
    return gaps.summary()


def evaluate_job(url: str = "") -> str:
    """Mo can't say a link aloud: with none given, the one he copied is used."""
    import re
    from core.career import pipeline
    if not url:
        from tools.clipboard_tool import get_clipboard_text
        m = re.search(r"https?://\S+", get_clipboard_text())
        if not m:
            return "No job link given or copied. Copy the posting's link, then ask again."
        url = m.group(0)
    return pipeline.evaluate_one(url)


def mark_followed_up(app_id: str) -> str:
    from core.career import tracker
    app = tracker.mark_followed_up(app_id)
    if app is None:
        return f"Error: no application '{app_id}'."
    left = tracker.MAX_FOLLOW_UPS - len(app["followed_up"])
    return (f"Follow-up on {app['title']} at {app.get('company')} noted. "
            + (f"The next one is due in {tracker.FOLLOW_UP_DAYS} days." if left
               else "That was the last one."))


def check_application_replies() -> str:
    from core.career import pipeline
    return pipeline.check_replies()


def import_cv(path: str, erp: bool = False, analyst: bool = False) -> str:
    from core.career import profile
    return profile.import_cv(path, erp=erp, analyst=analyst)


def set_application_answer(question: str, answer: str) -> str:
    """Saved for every form from now on; an application that stopped on a
    question tries again with it."""
    from core.career import pipeline, profile
    saved = profile.set_answer(question, answer)
    if saved.startswith("Error") or not pipeline.blocked_questions():
        return saved
    # A usual fact (salary, notice...) may be what any stopped form asked, in
    # its own words; anything else unblocks only the forms that asked it.
    known = question.strip().lower().replace(" ", "_") in profile.ANSWER_KEYS
    retried = pipeline.retry(only_questions=True) if known else \
        pipeline.retry(question=question.strip())
    return f"{saved}. {retried}"


def retry_applications() -> str:
    from core.career import pipeline
    return pipeline.retry()


def interview_prep(company: str, role: str = "") -> str:
    from core.career import interview
    return interview.prep(company, role)


def application_settings(live: "bool | None" = None,
                         daily_target: "int | None" = None, min_score: "int | None" = None,
                         linkedin_daily_cap: "int | None" = None,
                         referrals_per_day: "int | None" = None) -> str:
    from core.career import profile, store
    if live and not profile.has_cv():
        return "Error: import your CV first (say 'import my CV from <path>') -- live mode sends it."
    changes = {k: v for k, v in {
        "live": live, "daily_target": daily_target, "min_score": min_score,
        "linkedin_daily_cap": linkedin_daily_cap,
        "referrals_per_day": referrals_per_day}.items() if v is not None}
    s = store.update_settings(**changes)
    mode = "LIVE -- approved applications are sent" if s["live"] else "practice -- nothing is sent"
    return (f"Mode: {mode}. Daily target {s['daily_target']}, minimum score "
            f"{s['min_score']}, LinkedIn cap {s['linkedin_daily_cap']}/day.")


def graduate_programmes(check: bool = False) -> str:
    from core.career import programmes
    if check:
        opened = programmes.check_all()
        head = f"Checked. Newly open: {opened}.\n" if opened else "Checked.\n"
        return head + programmes.summary_text()
    return programmes.summary_text()


def find_referrals(company: str = "") -> str:
    from core.career import referrals, store
    return referrals.find(company or None, count=store.settings()["referrals_per_day"])


def import_linkedin_connections(path: str = "") -> str:
    from core.career import referrals
    return referrals.import_connections(path or None)


def referral_list() -> str:
    from core.career import referrals
    return referrals.list_text()


def mark_referral(referral_id: str, status: str) -> str:
    from core.career import referrals
    return referrals.mark(referral_id, status)
