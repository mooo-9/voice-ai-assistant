"""One line on the job hunt, for every brain turn: Mo's main goal is an
interview at one of the biggest companies in Cairo, so whatever is waiting
on him there is what El Fager raises first. Reads local files only -- it runs
on every turn."""
from core.career import profile, programmes, referrals, tracker


def status_line() -> str:
    try:
        apps = list(tracker.all_apps().values())
        interviews = [a for a in apps if a.get("status") == "interview"]
        ready = sum(1 for a in apps if a.get("status") == "ready")
        needs_you = sum(1 for a in apps if a.get("status") == "needs_you")
        parts = []
        if interviews:
            parts.append(f"{len(interviews)} interview(s): "
                         + ", ".join(sorted({a.get('company') or '?' for a in interviews})))
        if ready:
            parts.append(f"{ready} applications waiting for his review")
        from core.career import pipeline
        asked = pipeline.blocked_questions()
        if asked:
            parts.append("a job form asks what El Fager doesn't know -- ask him, save it with "
                         "set_application_answer (that retries it): "
                         + "; ".join(f"'{pipeline._question(a)}' ({a.get('company') or '?'})"
                                     for a in asked[:3]))
        if needs_you - len(asked):
            parts.append(f"{needs_you - len(asked)} applications waiting on him (a sign-in or an "
                         "account a site wants); once done, retry_applications")
        for p in programmes.closing_soon()[:2]:
            parts.append(f"{p['name']} closes in {programmes.days_left(p)} days")
        follow = len(tracker.follow_ups_due())
        if follow:
            parts.append(f"{follow} applications due a follow-up")
        pending = len(referrals.to_send())
        if pending:
            parts.append(f"{pending} referral notes to send")
        if not profile.has_cv():
            parts.append("his CV isn't imported yet")
        return ("JOB HUNT -- Mo's main goal, raise it first: "
                + ("; ".join(parts) if parts else "nothing waiting on him right now") + ".")
    except Exception:
        return ""
