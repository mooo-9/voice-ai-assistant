"""Every job the pipeline has touched, and where each one stands.

Statuses, in order:
  skipped    scored too low, over a cap, or Mo unticked it
  ready      drafted, waiting for Mo; stays saved until he sends or skips it
  approved   Mo approved it; queued to send
  practice   approved in practice mode: nothing was sent
  applied    sent
  needs_you  a form is filled in Comet and waits for Mo's Submit, or a
             send failed in a way only Mo can fix (login, captcha)
  failed     couldn't be sent
  interview / rejected / replied   what came back
"""
import hashlib
from datetime import datetime, timedelta

from core.career import store

_FILE = "applications.json"

# When to follow up on a sent application with no answer (career-ops' cadence):
# a week after sending, once more a week later, then let it go.
FOLLOW_UP_DAYS = 7
MAX_FOLLOW_UPS = 2


def job_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]


def all_apps() -> dict:
    return store.load(_FILE, {})


def add(app: dict) -> dict:
    apps = all_apps()
    app.setdefault("id", job_id(app["url"]))
    app.setdefault("found_at", datetime.now().isoformat(timespec="seconds"))
    app.setdefault("events", [])
    apps[app["id"]] = app
    store.save(_FILE, apps)
    return app


def update(app_id: str, status: "str | None" = None, note: str = "", **fields) -> "dict | None":
    apps = all_apps()
    app = apps.get(app_id)
    if app is None:
        return None
    app.update(fields)
    if status:
        app["status"] = status
        app["events"].append({"at": datetime.now().isoformat(timespec="seconds"),
                              "status": status, "note": note})
    store.save(_FILE, apps)
    return app


def with_status(*statuses: str) -> list[dict]:
    return [a for a in all_apps().values() if a.get("status") in statuses]


def follow_ups_due(now: "datetime | None" = None) -> list[dict]:
    """Sent applications with no answer yet whose follow-up is due, oldest first."""
    now = now or datetime.now()
    due = []
    for a in with_status("applied"):
        done = a.get("followed_up", [])
        last = done[-1] if done else a.get("applied_at", "")
        if last and len(done) < MAX_FOLLOW_UPS and \
                datetime.fromisoformat(last) + timedelta(days=FOLLOW_UP_DAYS) <= now:
            due.append(a)
    return sorted(due, key=lambda a: a.get("applied_at", ""))


def mark_followed_up(app_id: str) -> "dict | None":
    app = all_apps().get(app_id)
    if app is None:
        return None
    return update(app_id, followed_up=app.get("followed_up", [])
                  + [datetime.now().isoformat(timespec="seconds")])


def reached_status_since(status: str, since: datetime) -> list[dict]:
    """Apps that moved into `status` at or after `since`."""
    iso = since.isoformat(timespec="seconds")
    return [a for a in all_apps().values()
            if any(e["status"] == status and e["at"] >= iso for e in a.get("events", []))]
