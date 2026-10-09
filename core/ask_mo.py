"""What El Fager needs from Mo, asked on WhatsApp, and his reply read back.

Anything that can't go on without him -- a job form's question, a site he has
to sign in to, a stuck mission, a question a background task has -- is queued
here. One question is on his phone at a time, so a reply can only mean one
thing: it goes back to the part of El Fager that asked, and the next follows.
"skip" drops a question. Twilio keeps his messages to the sandbox number, so
replies are read from its API; nothing is read while nothing waits.

data/ask_mo.json is the queue: [{kind, key, question, asked_at}], oldest first.
"""
import json
from datetime import datetime
from pathlib import Path

_FILE = Path("data/ask_mo.json")
_SKIP = {"skip", "skip it", "no", "ignore"}


def ask(kind: str, key: str, question: str) -> None:
    """Queue a question for Mo (the same kind and key only once) and put it on
    his phone if none is waiting there."""
    queue = _load()
    if not any(q["kind"] == kind and q["key"] == key for q in queue):
        queue.append({"kind": kind, "key": key, "question": question, "asked_at": ""})
        _save(queue)
    _send_next()


def waiting() -> list[dict]:
    return _load()


def check_reply() -> str:
    """Run every minute: drop what got answered another way, and hand Mo's
    reply to the question on his phone to whatever asked it."""
    from core.notifier import get_notifier
    queue = [q for q in _load() if not _resolved(q)]
    _save(queue)
    sent = next((q for q in queue if q["asked_at"]), None)
    if sent is None:
        _send_next()
        return ""
    since = datetime.fromisoformat(sent["asked_at"])
    replies = [(when, body.strip()) for when, body in get_notifier().replies_since(since)
               if body.strip()]
    if not replies:
        return ""
    reply = min(replies)[1]
    _save([q for q in queue if q is not sent])
    if reply.lower() in _SKIP:
        result = "Skipped."
    else:
        result = _HANDLERS[sent["kind"]][1](sent, reply)
    get_notifier().send(f"Got it. {result}")
    _send_next()
    return result


def _send_next() -> None:
    from core.notifier import get_notifier
    queue = _load()
    if not queue or any(q["asked_at"] for q in queue):
        return
    notifier = get_notifier()
    if not notifier.whatsapp_ready:
        return
    first = queue[0]
    more = f" ({len(queue) - 1} more after this.)" if len(queue) > 1 else ""
    if notifier.send(f"El Fager needs you: {first['question']}\nReply here, or 'skip'.{more}"):
        first["asked_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _save(queue)


# ── What each kind of question is about ─────────────────────────────────────
# kind: (answered another way?, what his reply does -> what to tell him)

def _job_form_open(q: dict) -> bool:
    from core.career import pipeline
    return q["key"] in {pipeline._question(a) for a in pipeline.blocked_questions()}


def _job_form_reply(q: dict, reply: str) -> str:
    from tools import career_tool
    result = career_tool.set_application_answer(q["key"], reply)
    return "Saved, and sending it again now." if "Retrying" in result else result


def _signin_open(q: dict) -> bool:
    from core.career import tracker
    app = tracker.all_apps().get(q["key"])
    return bool(app) and app.get("status") == "needs_you"


def _signin_reply(q: dict, reply: str) -> str:
    from core.career import pipeline
    return pipeline.retry()


def _mission_open(q: dict) -> bool:
    from core.missions import MissionManager
    return any(m["id"] == q["key"] and m["status"] == "blocked"
               for m in MissionManager()._load())


def _mission_reply(q: dict, reply: str) -> str:
    from core.missions import MissionManager
    mgr = MissionManager()
    if reply.lower() in ("stop", "cancel"):
        mgr.stop(q["key"])
        return "Mission stopped."
    if mgr.resume(q["key"], reply):
        return "Carrying on with the mission, your way."
    return "Another mission is running; this one stays stuck until it's done."


def _task_reply(q: dict, reply: str) -> str:
    from core.autonomous_tasks import AutonomousTaskManager
    AutonomousTaskManager().add(
        f"Earlier you asked Mo on WhatsApp: \"{q['question']}\" He replied: \"{reply}\". "
        "Carry on with what you needed it for.")
    return "I'll carry on with that."


_HANDLERS = {
    "job_form": (_job_form_open, _job_form_reply),
    "job_signin": (_signin_open, _signin_reply),
    "mission": (_mission_open, _mission_reply),
    "task": (lambda q: True, _task_reply),
}


def _resolved(q: dict) -> bool:
    try:
        return not _HANDLERS[q["kind"]][0](q)
    except Exception:
        return False


def _load() -> list[dict]:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(queue: list[dict]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(queue, indent=1, ensure_ascii=False), encoding="utf-8")
