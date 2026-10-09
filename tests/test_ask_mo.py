"""What El Fager needs from Mo goes to his WhatsApp, one question at a time,
and his reply there goes back to whatever asked: a job form, a site to sign in
to, a stuck mission, a background task's question."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core import ask_mo
from core.career import pipeline, profile, tracker


@pytest.fixture
def phone():
    """A stand-in for Mo's WhatsApp: what El Fager sends, and his replies."""
    fake = MagicMock(whatsapp_ready=True, sent=[], replies=[])
    fake.send.side_effect = lambda text: fake.sent.append(text) or True
    fake.replies_since.side_effect = lambda since: [r for r in fake.replies if r[0] > since]
    with patch("core.notifier.get_notifier", return_value=fake):
        yield fake


def _reply(phone, text):
    phone.replies.append((datetime.now().astimezone() + timedelta(seconds=5), text))


def _blocked(url, note, company="Valeo"):
    tracker.add({"url": url, "title": "Data Analyst", "company": company,
                 "status": "approved", "channel": "site"})
    tracker.update(tracker.job_id(url), "needs_you", note)
    return tracker.job_id(url)


def test_a_job_form_question_is_asked_once_and_the_reply_sends_it_again(phone):
    app = _blocked("https://v.com/1", "BLOCKED: Do you hold a driving licence?")
    pipeline._ask_mo_what_stopped()
    pipeline._ask_mo_what_stopped()               # asked once, not every run
    assert len(phone.sent) == 1 and '"Do you hold a driving licence?"' in phone.sent[0]
    assert ask_mo.check_reply() == ""             # no reply yet
    _reply(phone, " Yes ")
    with patch.object(pipeline, "start_in_background", return_value=True):
        assert ask_mo.check_reply() == "Saved, and sending it again now."
    assert profile.load()["extra_answers"] == {"Do you hold a driving licence?": "Yes"}
    assert tracker.all_apps()[app]["status"] == "approved"
    assert phone.sent[-1] == "Got it. Saved, and sending it again now."
    assert ask_mo.waiting() == []


def test_one_question_on_his_phone_at_a_time(phone):
    """A reply can only mean one thing; only the form that asked is sent again."""
    _blocked("https://v.com/1", "BLOCKED: Driving licence?")
    ey = _blocked("https://e.com/2", "BLOCKED: Notice period?", company="EY")
    pipeline._ask_mo_what_stopped()
    assert len(phone.sent) == 1 and '"Driving licence?"' in phone.sent[0]
    _reply(phone, "Yes")
    with patch.object(pipeline, "start_in_background", return_value=True):
        ask_mo.check_reply()
    assert '"Notice period?"' in phone.sent[-1]
    assert tracker.all_apps()[ey]["status"] == "needs_you"


def test_a_site_to_sign_in_to_goes_again_when_he_says_done(phone):
    app = _blocked("https://w.com/3", "BLOCKED: needs an account on Workday")
    pipeline._ask_mo_what_stopped()
    assert "Sort it out in Comet" in phone.sent[0]
    _reply(phone, "done")
    with patch.object(pipeline, "start_in_background", return_value=True):
        assert ask_mo.check_reply() == "Retrying 1 application(s) now."
    assert tracker.all_apps()[app]["status"] == "approved"


def test_skip_drops_the_question(phone):
    _blocked("https://v.com/1", "BLOCKED: Driving licence?")
    pipeline._ask_mo_what_stopped()
    _reply(phone, "skip")
    assert ask_mo.check_reply() == "Skipped."
    assert ask_mo.waiting() == [] and "extra_answers" not in profile.load()


def test_a_stuck_mission_goes_on_his_way(phone, tmp_path, monkeypatch):
    import core.missions as missions
    monkeypatch.setattr(missions, "_MISSIONS_PATH", tmp_path / "missions.json")
    mgr = missions.MissionManager()
    m = mgr.create("Book a gym class", ["Find the timetable", "Book it"])
    mgr.fail_step(m["id"], 1, "site down")
    mgr.fail_step(m["id"], 1, "site down")
    assert mgr.last_finished()["status"] == "blocked"
    ask_mo.ask("mission", m["id"], "Mission 'Book a gym class' is stuck. Reply how to go on.")
    _reply(phone, "use the app's website instead")
    assert ask_mo.check_reply() == "Carrying on with the mission, your way."
    step = mgr.next_step()
    assert step["n"] == 1 and "(Mo said: use the app's website instead)" in step["description"]


def test_stop_cancels_a_stuck_mission(phone, tmp_path, monkeypatch):
    import core.missions as missions
    monkeypatch.setattr(missions, "_MISSIONS_PATH", tmp_path / "missions.json")
    mgr = missions.MissionManager()
    m = mgr.create("x", ["a"])
    mgr.fail_step(m["id"], 1, "e")
    mgr.fail_step(m["id"], 1, "e")
    ask_mo.ask("mission", m["id"], "stuck")
    _reply(phone, "stop")
    assert ask_mo.check_reply() == "Mission stopped."
    assert mgr.last_finished()["status"] == "cancelled"


def test_a_background_tasks_question_comes_back_as_a_task(phone):
    """The brain's ask_mo tool: his answer lets the work carry on."""
    from core.brain import Brain
    out = Brain(profile={})._dispatch_tool("ask_mo", {"question": "Which gym branch?"})
    assert out.startswith("Asked Mo on WhatsApp")
    assert "Which gym branch?" in phone.sent[0]
    with patch("core.autonomous_tasks.AutonomousTaskManager") as tasks:
        _reply(phone, "Maadi")
        assert ask_mo.check_reply() == "I'll carry on with that."
    assert 'He replied: "Maadi"' in tasks.return_value.add.call_args.args[0]


def test_answered_another_way_the_question_is_dropped(phone):
    _blocked("https://v.com/1", "BLOCKED: Driving licence?")
    pipeline._ask_mo_what_stopped()
    with patch.object(pipeline, "start_in_background", return_value=True):
        profile.set_answer("Driving licence?", "Yes")
        pipeline.retry(only_questions=True)
    ask_mo.check_reply()
    assert ask_mo.waiting() == []
    phone.replies_since.assert_not_called()


def test_a_reply_from_before_the_question_is_not_the_answer(phone):
    _blocked("https://v.com/1", "BLOCKED: Driving licence?")
    phone.replies.append((datetime.now(timezone.utc) - timedelta(hours=1), "ok"))
    pipeline._ask_mo_what_stopped()
    assert ask_mo.check_reply() == ""


def test_nothing_waiting_reads_nothing(phone):
    assert ask_mo.check_reply() == ""
    phone.replies_since.assert_not_called()


def test_twilio_replies_are_his_messages_after_the_question():
    from core.notifier import ElFagerNotifier
    n = ElFagerNotifier()
    n._account_sid, n._auth_token, n._from_number, n._to_number = "AC", "t", "+1415", "+2010"
    since = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    page = {"messages": [{"date_sent": "Thu, 01 Oct 2026 10:05:00 +0000", "body": "Yes"},
                         {"date_sent": "Thu, 01 Oct 2026 09:00:00 +0000", "body": "old"}]}
    fake_httpx = MagicMock()
    fake_httpx.get.return_value = MagicMock(status_code=200, json=lambda: page)
    with patch("core.notifier.httpx", fake_httpx):
        replies = n.replies_since(since)
    assert [body for _, body in replies] == ["Yes"]
    assert fake_httpx.get.call_args.kwargs["params"]["From"] == "whatsapp:+2010"


def test_an_alert_twilio_failed_is_noticed():
    """Every alert from 11 to 30 September failed (63015) and nobody knew."""
    from core.notifier import ElFagerNotifier
    n = ElFagerNotifier()
    n._account_sid, n._auth_token, n._from_number, n._to_number = "AC", "t", "+1415", "+2010"
    fake_httpx = MagicMock()
    for status, code, said in [("failed", 63015, "left the Twilio sandbox"),
                               ("delivered", None, "")]:
        fake_httpx.get.return_value = MagicMock(
            status_code=200, json=lambda: {"messages": [{"status": status, "error_code": code}]})
        with patch("core.notifier.httpx", fake_httpx):
            got = n.delivery_problem()
        assert (said in got) if said else got == ""
