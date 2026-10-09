# tests/agents/test_browser_agent.py
from unittest.mock import MagicMock, patch
from core.agents.browser_agent import BrowserAgent


def _done(message: str = "Task complete.") -> dict:
    return {"status": "done", "message": message, "action": {"type": "none"}}


def _continue(action: dict) -> dict:
    return {"status": "continue", "message": "Working...", "action": action}


def test_execute_navigate():
    agent = BrowserAgent()
    page = MagicMock()
    agent._execute(page, {"type": "navigate", "url": "https://example.com"})
    page.goto.assert_called_once_with("https://example.com", wait_until="domcontentloaded")


def test_execute_click_by_selector():
    agent = BrowserAgent()
    page = MagicMock()
    agent._execute(page, {"type": "click", "selector": "#submit"})
    page.click.assert_called_once_with("#submit", timeout=5000)


def test_execute_type_fills_field():
    agent = BrowserAgent()
    page = MagicMock()
    agent._execute(page, {"type": "type", "selector": "#email", "text": "mo@gmail.com"})
    page.fill.assert_called_once_with("#email", "mo@gmail.com")


def test_execute_scroll_down():
    agent = BrowserAgent()
    page = MagicMock()
    agent._execute(page, {"type": "scroll", "direction": "down"})
    page.mouse.wheel.assert_called_once_with(0, 500)


def test_execute_scroll_up():
    agent = BrowserAgent()
    page = MagicMock()
    agent._execute(page, {"type": "scroll", "direction": "up"})
    page.mouse.wheel.assert_called_once_with(0, -500)


def test_execute_wait():
    agent = BrowserAgent()
    page = MagicMock()
    with patch("time.sleep") as mock_sleep:
        agent._execute(page, {"type": "wait", "seconds": 2})
    mock_sleep.assert_called_once_with(2)


def test_run_done_immediately():
    agent = BrowserAgent()
    done = _done("Found the answer.")

    mock_page = MagicMock()
    mock_page.url = "https://example.com"
    mock_page.screenshot.return_value = b"fake_png"
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_playwright_ctx = MagicMock()

    with patch.object(agent, "_get_action", return_value=done), \
         patch("time.sleep"), \
         patch("playwright.sync_api.sync_playwright") as mock_pw, \
         patch("tools.comet_tool.automation_context",
               return_value=(mock_browser, mock_context, True)):
        mock_pw.return_value.__enter__ = MagicMock(return_value=mock_playwright_ctx)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        result = agent.run("find something")

    assert result == "Found the answer."
    mock_browser.close.assert_called_once()


def test_run_records_spend_under_the_callers_label():
    labels = []
    for kwargs in ({}, {"telemetry_source": "career"}):
        agent = BrowserAgent()
        mock_context = MagicMock()
        mock_context.new_page.return_value.screenshot.return_value = b"fake_png"
        with patch.object(agent, "_get_action", return_value=_done()), \
             patch("time.sleep"), \
             patch("core.telemetry.instrument_client") as instrument, \
             patch("playwright.sync_api.sync_playwright") as mock_pw, \
             patch("tools.comet_tool.automation_context",
                   return_value=(MagicMock(), mock_context, True)):
            mock_pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_pw.return_value.__exit__ = MagicMock(return_value=False)
            agent.run("find something", **kwargs)
        labels.append(instrument.call_args.args[1])
    assert labels == ["browser_agent", "career"]


def test_run_requests_login_when_no_vault_creds():
    agent = BrowserAgent()
    need_login = {"status": "need_login", "message": "google", "action": {"type": "none"}}

    mock_page = MagicMock()
    mock_page.url = "https://accounts.google.com"
    mock_page.screenshot.return_value = b"fake_png"
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_playwright_ctx = MagicMock()

    with patch.object(agent, "_get_action", return_value=need_login), \
         patch("core.vault.Vault.get", return_value=None), \
         patch("time.sleep"), \
         patch("playwright.sync_api.sync_playwright") as mock_pw, \
         patch("tools.comet_tool.automation_context",
               return_value=(mock_browser, mock_context, True)):
        mock_pw.return_value.__enter__ = MagicMock(return_value=mock_playwright_ctx)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        result = agent.run("check gmail")

    assert "login required" in result.lower() or "vault set" in result.lower()


def test_run_continues_when_vault_creds_found():
    agent = BrowserAgent()
    need_login = {"status": "need_login", "message": "google", "action": {"type": "none"}}
    done = {"status": "done", "message": "Task complete.", "action": {"type": "none"}}

    mock_page = MagicMock()
    mock_page.url = "https://accounts.google.com"
    mock_page.screenshot.return_value = b"fake_png"
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_playwright_ctx = MagicMock()

    with patch.object(agent, "_get_action", side_effect=[need_login, done]) as mock_get_action, \
         patch("core.vault.Vault.get", return_value={"username": "mo", "password": "secret"}), \
         patch("time.sleep"), \
         patch("playwright.sync_api.sync_playwright") as mock_pw, \
         patch("tools.comet_tool.automation_context",
               return_value=(mock_browser, mock_context, True)):
        mock_pw.return_value.__enter__ = MagicMock(return_value=mock_playwright_ctx)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        result = agent.run("check gmail")

    assert result == "Task complete."
    second_call = mock_get_action.call_args_list[1]
    injected = second_call.kwargs.get("injected_creds")
    assert injected is not None, "_get_action not called with injected_creds on login retry"
    assert injected["username"] == "mo"
    assert injected["password"] == "secret"


def test_a_finished_task_leaves_his_comet_tab_open():
    """Mo found the video he wanted, then El Fager closed it."""
    agent = BrowserAgent()
    mock_page = MagicMock()
    mock_page.url = "https://www.youtube.com/watch?v=x"
    mock_page.screenshot.return_value = b"fake_png"
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page

    with patch.object(agent, "_get_action", return_value=_done("Playing it.")), \
         patch("time.sleep"), \
         patch("playwright.sync_api.sync_playwright") as mock_pw, \
         patch("tools.comet_tool.automation_context",
               return_value=(mock_browser, mock_context, False)):
        mock_pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        assert agent.run("play the video") == "Playing it."

    mock_page.close.assert_not_called()
    mock_browser.close.assert_not_called()


def test_it_says_so_when_his_comet_cant_be_used():
    from tools.comet_tool import CometUnavailable
    agent = BrowserAgent()
    with patch("playwright.sync_api.sync_playwright") as mock_pw, \
         patch("tools.comet_tool.automation_context",
               side_effect=CometUnavailable("Close Comet so I can reopen it signed in.")):
        mock_pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        assert "reopen it signed in" in agent.run("check gmail")


def test_a_failed_action_is_reported_to_the_next_step_not_raised():
    """A stale selector ("input#search" on YouTube) raised out of run() and
    ended the whole task instead of letting the model try another way."""
    agent = BrowserAgent()
    click = {"status": "continue", "message": "Clicking search",
             "action": {"type": "click", "selector": "input#search"}}
    mock_page = MagicMock()
    mock_page.url = "https://www.youtube.com"
    mock_page.screenshot.return_value = b"fake_png"
    mock_page.click.side_effect = RuntimeError("Timeout 5000ms exceeded")
    mock_page.get_by_text.return_value.first.click.side_effect = RuntimeError("Timeout 5000ms exceeded")
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page

    with patch.object(agent, "_get_action", side_effect=[click, _done("Found it.")]) as get_action, \
         patch("time.sleep"), \
         patch("playwright.sync_api.sync_playwright") as mock_pw, \
         patch("tools.comet_tool.automation_context",
               return_value=(MagicMock(), mock_context, False)):
        mock_pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        assert agent.run("search youtube") == "Found it."

    history = get_action.call_args_list[1].args[4]
    assert any("failed" in step.lower() for step in history)


def test_execute_select_picks_an_option_by_its_label():
    agent = BrowserAgent()
    page = MagicMock()
    agent._execute(page, {"type": "select", "selector": "#military", "text": "Exempted"})
    page.select_option.assert_called_once_with("#military", label="Exempted")


def test_execute_upload_attaches_only_the_file_the_task_gave():
    """The model names the field; the path comes from the caller, so a job form
    can't be made to upload some other file from Mo's disk."""
    agent = BrowserAgent()
    agent._upload_path = "C:/cv.pdf"
    page = MagicMock()
    agent._execute(page, {"type": "upload", "selector": "input[type=file]",
                          "path": "C:/secrets.txt"})
    page.set_input_files.assert_called_once_with("input[type=file]", "C:/cv.pdf")


def test_execute_upload_without_a_file_is_refused():
    import pytest
    agent = BrowserAgent()
    agent._upload_path = None
    with pytest.raises(ValueError):
        agent._execute(MagicMock(), {"type": "upload", "selector": "input[type=file]"})


def test_a_task_that_asks_closes_its_own_tab_but_never_his_browser():
    """Dozens of job applications a day would each leave a tab in Mo's Comet."""
    agent = BrowserAgent()
    mock_page = MagicMock()
    mock_page.screenshot.return_value = b"fake_png"
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page

    with patch.object(agent, "_get_action", return_value=_done("SUBMITTED")), \
         patch("time.sleep"), \
         patch("playwright.sync_api.sync_playwright") as mock_pw, \
         patch("tools.comet_tool.automation_context",
               return_value=(mock_browser, mock_context, False)):
        mock_pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        assert agent.run("apply", close_tab=True) == "SUBMITTED"

    mock_page.close.assert_called_once()
    mock_browser.close.assert_not_called()


def test_a_step_can_be_decided_by_the_callers_ask_fn():
    """The job hunt decides each form step through Claude Code, which wraps
    its JSON in a ```json fence."""
    agent = BrowserAgent()
    seen = []
    agent._ask_fn = lambda system, prompt, shot: seen.append((prompt, shot)) or \
        '```json\n{"status": "done", "message": "SUBMITTED", "action": {"type": "none"}}\n```'
    client = MagicMock()
    out = agent._get_action(client, "apply", "PNG", "https://x", [], fields="#fn | text | \"Name\"")
    assert out["message"] == "SUBMITTED"
    assert '#fn | text | "Name"' in seen[0][0] and seen[0][1] == "PNG"
    client.messages.create.assert_not_called()


def test_it_follows_the_tab_an_apply_button_opens():
    """LinkedIn's Apply opens the employer's form in a new tab."""
    agent = BrowserAgent()
    first, employer = MagicMock(), MagicMock()
    for pg in (first, employer):
        pg.screenshot.return_value = b"png"
        pg.is_closed.return_value = False
    first.url, employer.url = "https://linkedin.com/jobs/view/1", "https://careers.valeo.com/apply"
    context = MagicMock()
    context.new_page.return_value = first
    urls = []

    def act(client, task, shot, url, history, **kw):
        urls.append(url)
        if len(urls) == 1:
            context.on.call_args.args[1](employer)   # the new tab opens
            return _continue({"type": "click", "selector": "text=Apply"})
        return _done("SUBMITTED")

    with patch.object(agent, "_get_action", side_effect=act), patch("time.sleep"), \
         patch("playwright.sync_api.sync_playwright") as mock_pw, \
         patch("tools.comet_tool.automation_context", return_value=(MagicMock(), context, False)):
        mock_pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        assert agent.run("apply", close_tab=True) == "SUBMITTED"
    assert urls == ["https://linkedin.com/jobs/view/1", "https://careers.valeo.com/apply"]
    first.close.assert_called_once()
    employer.close.assert_called_once()


def test_fill_many_fills_each_field_and_names_the_ones_it_couldnt():
    agent = BrowserAgent()
    page = MagicMock()
    good, bad = MagicMock(), MagicMock()
    good.evaluate.return_value = "INPUT"
    bad.evaluate.side_effect = Exception("not found")
    page.locator.side_effect = lambda sel: MagicMock(first=good if sel == "#fn" else bad)
    try:
        agent._execute(page, {"type": "fill_many", "fields": [
            {"selector": "#nope", "text": "x"}, {"selector": "#fn", "text": "Mohamed"}]})
    except ValueError as e:
        assert "#nope" in str(e)
    good.fill.assert_called_once_with("Mohamed")
