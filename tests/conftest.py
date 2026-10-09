"""Suite-wide isolation: tests that exercise brain.chat() log conversation
turns; EL_FAGER_TEST_MODE diverts them to data/conversations_test/ so the
suite never pollutes the real usage logs HabitMiner mines."""
import pytest


@pytest.fixture(autouse=True)
def _el_fager_test_mode(monkeypatch):
    monkeypatch.setenv("EL_FAGER_TEST_MODE", "1")


@pytest.fixture(autouse=True)
def _isolated_trust_ledger(monkeypatch, tmp_path):
    """Any test that confirms a staged send appends to the Trust Ledger. Left
    alone, that wrote "sent gmail -> a@b.c" and "whatsapp -> Omar" into Mo's
    real data/action_ledger.jsonl on every run — an append-only record he
    reads as what El Fager did. Point it at a file of the test's own."""
    from core import ledger
    monkeypatch.setattr(ledger, "_LEDGER", tmp_path / "action_ledger.jsonl")


@pytest.fixture(autouse=True)
def _isolated_telemetry(monkeypatch, tmp_path):
    """Every API call is costed into data/telemetry/, and that month's total
    is what the monthly budget stops El Fager on. A test's fake calls must not
    count against Mo's budget, and his real spend must not put a test over it."""
    from core import telemetry
    monkeypatch.setattr(telemetry, "_TELEMETRY_DIR", tmp_path / "telemetry")
    monkeypatch.setattr(telemetry, "_SETTINGS_FILE", tmp_path / "telemetry_settings.json")


@pytest.fixture(autouse=True)
def _isolated_voice_learned(monkeypatch, tmp_path):
    """Songs El Fager plays teach Whisper their names, in data/voice_learned.json.
    A test that plays a fake song must not teach Mo's real El Fager "Song 39"."""
    from core import voice_learned
    monkeypatch.setattr(voice_learned, "_FILE", tmp_path / "voice_learned.json")


@pytest.fixture(autouse=True)
def _isolated_voice_liked(monkeypatch, tmp_path):
    """Names read from Mo's Liked Songs live in data/voice_liked.json; a test's
    fake library must never replace them."""
    from core import voice_liked
    monkeypatch.setattr(voice_liked, "_FILE", tmp_path / "voice_liked.json")


@pytest.fixture(autouse=True)
def _isolated_jobs_seen(monkeypatch, tmp_path):
    """Jobs the job search showed Mo are remembered in data/jobs_seen.json so
    they don't come back as new. A test's fake jobs must never hide real ones."""
    from core.agents import job_search_agent
    monkeypatch.setattr(job_search_agent, "_FILE", tmp_path / "jobs_seen.json")


@pytest.fixture(autouse=True)
def _no_real_job_page_browser(monkeypatch):
    """The job search reads Wuzzuf in a headless Chromium when a plain read
    is blocked. No test may launch one and reach the real site."""
    from core.agents.job_search_agent import JobSearchAgent
    monkeypatch.setattr(JobSearchAgent, "_render", lambda self, url: "")


@pytest.fixture(autouse=True)
def _no_real_career_sites(monkeypatch):
    """The nightly search reads the target firms' own career sites (Workday,
    Oracle, Phenom and the rest). No test may reach them; a test that needs a
    page patches these with its own."""
    from core.career import sources

    def blocked(*args, **kwargs):
        raise RuntimeError("real career sites are off in tests")

    monkeypatch.setattr(sources, "_get_json", blocked)
    monkeypatch.setattr(sources, "_post_json", blocked)
    monkeypatch.setattr(sources, "_get_text", blocked)


@pytest.fixture(autouse=True)
def _isolated_schedules(monkeypatch, tmp_path):
    """Mo's scheduled jobs (and tonight's job hunt, armed from the AUTOMATIONS
    panel) live in data/schedules.json. A test must never arm or drop his."""
    from core import scheduler
    monkeypatch.setattr(scheduler, "_SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "_HISTORY_FILE", tmp_path / "schedule_history.jsonl")


@pytest.fixture(autouse=True)
def _isolated_career(monkeypatch, tmp_path):
    """Mo's CV profile, his applications and the pipeline's settings live in
    data/career/. A test's fake batch must never land in his morning review."""
    from core.career import store
    monkeypatch.setattr(store, "DIR", tmp_path / "career")


@pytest.fixture(autouse=True)
def _no_real_career_claude(monkeypatch):
    """The job hunt sends its scoring and letters as Message Batches. A test
    that reached the real API would pay for a batch of fake jobs out of Mo's
    $10 a month. A test that needs a client patches claude._get_client."""
    from core.career import claude

    def blocked():
        raise RuntimeError("real Claude calls from the job hunt are off in tests")

    monkeypatch.setattr(claude, "_get_client", blocked)
    # Claude Code answers on Mo's real subscription: no test starts it. A test
    # of that path gives _code_exe a fake and patches subprocess.run.
    monkeypatch.setattr(claude, "_code_exe", lambda: None)


@pytest.fixture(autouse=True)
def _isolated_ask_mo(monkeypatch, tmp_path):
    """The questions waiting on Mo's WhatsApp reply live in data/ask_mo.json.
    A test's question must never sit in his real queue."""
    from core import ask_mo
    monkeypatch.setattr(ask_mo, "_FILE", tmp_path / "ask_mo.json")


@pytest.fixture(autouse=True)
def _no_real_whatsapp_alerts(monkeypatch):
    """Phone alerts go to Mo's real WhatsApp through Twilio, and some modules
    load .env (with the Twilio keys) when imported. No test may send one or
    read his replies: here El Fager has no keys. A test of the sending gives
    its own notifier keys and patches httpx."""
    from core import notifier
    for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM",
                "WHATSAPP_PHONE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(notifier, "_INSTANCE", None)     # rebuilt without the keys


@pytest.fixture(autouse=True)
def _no_real_whatsapp(monkeypatch):
    """Drafting a WhatsApp message searches Mo's real WhatsApp Desktop, and a
    confirm clicks and types into it. No test may do either — a test that
    needs them replaces these stubs with its own."""
    from tools import whatsapp_desktop

    def blocked(*args, **kwargs):
        raise whatsapp_desktop.WhatsAppDesktopError("real WhatsApp is off in tests")

    monkeypatch.setattr(whatsapp_desktop, "find_chats", blocked)
    monkeypatch.setattr(whatsapp_desktop, "send", blocked)


@pytest.fixture(autouse=True)
def _no_real_input(monkeypatch):
    """Keys and clicks go to whatever window is in front. A WhatsApp test
    pressed a real Enter into Mo's foreground window on every suite run. No
    test may press, type, click or move; a test that needs one of these
    replaces it with its own stub."""
    def blocked(*args, **kwargs):
        raise RuntimeError("real keyboard and mouse input is off in tests")

    try:
        import keyboard
        for name in ("press_and_release", "send", "write", "press", "release"):
            monkeypatch.setattr(keyboard, name, blocked)
    except ImportError:
        pass
    try:
        import pyautogui
        for name in ("press", "hotkey", "write", "typewrite", "click", "doubleClick",
                     "rightClick", "moveTo", "moveRel", "scroll", "keyDown", "keyUp",
                     "dragTo", "mouseDown", "mouseUp"):
            monkeypatch.setattr(pyautogui, name, blocked)
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _no_real_comet(monkeypatch):
    """Browser automation starts Comet with a debugging port when it can't
    attach to one. With Mo's own Comet already open, the browser-agent tests
    ran comet.exe for real — Chromium hands that to the running browser, which
    opens windows on his screen. Launching Comet or the default browser is off
    in tests; a test that needs either patches these with its own mock."""
    from tools import comet_tool

    def blocked(*args, **kwargs):
        raise RuntimeError("launching a real browser is off in tests")

    monkeypatch.setattr(comet_tool, "subprocess",
                        type("NoSubprocess", (), {"Popen": staticmethod(blocked),
                                                  "run": staticmethod(blocked)}))
    monkeypatch.setattr(comet_tool, "webbrowser",
                        type("NoBrowser", (), {"open": staticmethod(blocked)}))
