# core/agents/browser_agent.py
import base64
import json
import time
import anthropic
from core.agents.base_agent import BaseAgent
from core.vault import Vault

_VISION_SYSTEM = """\
You control a web browser on behalf of the user. You will see a browser screenshot and must decide the single next action to make progress on the task.

Reply with a JSON object only — no markdown, no explanation, just raw JSON:
{
  "status": "continue" | "done" | "need_login",
  "message": "<one sentence describing what you see or what you just did>",
  "action": {
    "type": "navigate" | "click" | "type" | "select" | "upload" | "fill_many" | "scroll" | "wait" | "none",
    "url": "<full URL, required for navigate>",
    "selector": "<CSS selector, required for click/type/select/upload>",
    "text": "<text to enter, required for type; the option's visible label for select>",
    "fields": [{"selector": "<selector>", "text": "<value, or the option's label for a dropdown>"}],
    "direction": "up" | "down",
    "seconds": <float>
  }
}

Use the selectors from the page's field list when one fits. "fill_many" fills several fields in
one step: use it to fill every field you can see at once. Use "upload" on a file input to attach
the file the task provides; "select" picks a dropdown option.
Use "need_login" when you see a login page and need credentials — include the service name (e.g. "google") in message.
Set status to "done" when the task is fully complete.
"""


# The page's own fields and buttons, with selectors that work: from a
# screenshot alone the model guessed selectors, and long forms ran out of steps.
# File inputs count even when hidden: sites style a button over them.
_FIELDS_JS = r"""() => {
  const out = [], seen = new Set();
  const act = /apply|submit|next|continue|review|upload|sign in|log in|save/i;
  for (const el of document.querySelectorAll('input, select, textarea, button, [role=button], a')) {
    const file = el.tagName === 'INPUT' && el.type === 'file';
    const r = el.getBoundingClientRect();
    if (!file && (!r.width || !r.height)) continue;
    if (el.tagName === 'INPUT' && el.type === 'hidden') continue;
    const label = ((el.labels && el.labels[0] && el.labels[0].innerText) ||
      el.getAttribute('aria-label') || el.placeholder || el.innerText || el.value || '')
      .trim().replace(/\s+/g, ' ').slice(0, 80);
    const clickable = ['A', 'BUTTON'].includes(el.tagName) || el.getAttribute('role') === 'button';
    if (clickable && !act.test(label)) continue;
    let sel = el.id ? '#' + CSS.escape(el.id)
      : el.name ? el.tagName.toLowerCase() + '[name="' + el.name + '"]'
      : clickable && label ? 'text=' + label.slice(0, 50) : null;
    if (!sel || seen.has(sel)) continue;
    seen.add(sel);
    let line = sel + ' | ' + (el.type || el.tagName.toLowerCase()) + ' | "' + label + '"';
    if (el.value && !clickable && !file) line += ' | filled: "' + String(el.value).slice(0, 40) + '"';
    if (file && el.files && el.files.length) line += ' | attached: ' + el.files[0].name;
    if (el.required) line += ' | required';
    if (el.tagName === 'SELECT')
      line += ' | options: ' + [...el.options].slice(0, 15).map(o => o.text.trim()).join(' / ');
    out.push(line);
    if (out.length >= 60) break;
  }
  return out.join('\n');
}"""


def _parse_action(text: str) -> dict:
    """The model's JSON reply, even wrapped in a ```json fence."""
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {"status": "done", "message": text, "action": {"type": "none"}}


class BrowserAgent(BaseAgent):
    MAX_STEPS = 20
    STEP_DELAY = 1.0

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return "Automates any website -- navigate, fill forms, click buttons, handle logins."

    def run(self, task: str, start_url: str = None, upload_path: str = None,
            close_tab: bool = False, telemetry_source: str = "browser_agent",
            ask_fn=None) -> str:
        """`ask_fn(system, text, screenshot_b64) -> reply` decides each step
        instead of the API client (the job hunt sends it to Claude Code)."""
        from playwright.sync_api import sync_playwright

        client = anthropic.Anthropic()
        from core.telemetry import instrument_client
        instrument_client(client, telemetry_source)
        vault = Vault()
        history: list[str] = []
        # The one file an "upload" may attach. The model names a field, never
        # a path, so it can't pick a different file off Mo's disk.
        self._upload_path = upload_path
        self._ask_fn = ask_fn

        with sync_playwright() as p:
            from tools.comet_tool import CometUnavailable, automation_context
            try:
                browser, context, owned = automation_context(p, headless=False)
            except CometUnavailable as e:
                return str(e)
            page = context.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            # "Apply" often opens the employer's site in a new tab: follow it.
            pages = [page]
            # A lambda: Playwright tags its handler, and list.append can't be tagged.
            context.on("page", lambda opened: pages.append(opened))

            def close_up():
                # In Mo's own Comet (`owned` False) the tab stays: it holds what
                # he asked for — he found his video and then watched it close.
                # Leaving the with-block only disconnects from his browser.
                # A caller running many tasks (job applications) asks for its
                # own tab to go, so they don't pile up.
                if not owned:
                    if close_tab:
                        for opened in pages:
                            try:
                                opened.close()
                            except Exception:
                                pass
                    return
                for obj in (page, browser):
                    try:
                        obj.close()
                    except Exception:
                        pass

            if start_url:
                page.goto(start_url, wait_until="domcontentloaded")

            injected_creds: dict | None = None
            for _ in range(self.MAX_STEPS):
                page = next((pg for pg in reversed(pages) if pg.is_closed() is False), page)
                screenshot_b64 = self._capture(page)
                result = self._get_action(
                    client, task, screenshot_b64, page.url, history,
                    injected_creds=injected_creds, fields=self._fields(page),
                )
                injected_creds = None
                history.append(result.get("message", ""))

                if result["status"] == "done":
                    close_up()
                    return result.get("message", "Task complete.")

                if result["status"] == "need_login":
                    service = result.get("message", "unknown").lower().split()[0]
                    creds = vault.get(service)
                    if not creds:
                        close_up()
                        return (
                            f"Login required for {service} -- "
                            f"add credentials first: vault set {service}"
                        )
                    history.append(f"Using saved credentials for {service}")
                    injected_creds = {
                        "service": service,
                        "username": creds.get("username", ""),
                        "password": creds.get("password", ""),
                    }
                    continue

                try:
                    action = result.get("action", {})
                    self._execute(page, action)
                    if action.get("type") == "upload":
                        # Most sites show nothing after a hidden file input is
                        # set; unseen, the model uploaded the CV twenty times.
                        history.append(f"The file is attached to {action.get('selector')}: "
                                       "don't upload it again.")
                except Exception as e:
                    # A stale selector used to end the whole task; the next
                    # step sees what went wrong and can try another way.
                    reason = (str(e).splitlines() or ["unknown error"])[0][:160]
                    history.append(f"That action failed ({reason}) — try a different selector or approach.")
                time.sleep(self.STEP_DELAY)

            close_up()
            return "Browser task completed (reached max steps)."

    def _capture(self, page) -> str:
        return base64.b64encode(page.screenshot(full_page=False)).decode()

    def _fields(self, page) -> str:
        try:
            listed = page.evaluate(_FIELDS_JS)
            return listed if isinstance(listed, str) else ""
        except Exception:
            return ""

    def _get_action(
        self,
        client: anthropic.Anthropic,
        task: str,
        screenshot_b64: str,
        current_url: str,
        history: list[str],
        injected_creds: dict | None = None,
        fields: str = "",
    ) -> dict:
        history_text = (
            "\n".join(f"Step {i + 1}: {h}" for i, h in enumerate(history))
            if history
            else "None yet."
        )
        creds_hint = (
            f"\nCredentials available: username={injected_creds['username']} "
            f"password={injected_creds['password']} -- fill them into the login form now."
            if injected_creds
            else ""
        )
        prompt = (
            f"Task: {task}\n"
            f"Current URL: {current_url}\n\n"
            f"Fields and buttons on the page:\n{fields or '(none read)'}\n\n"
            f"Steps so far:\n{history_text}\n\n"
            f"What is the next action?{creds_hint}"
        )
        if getattr(self, "_ask_fn", None):
            return _parse_action(self._ask_fn(_VISION_SYSTEM, prompt, screenshot_b64) or "")
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            system=_VISION_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return _parse_action(response.content[0].text.strip())

    def _execute(self, page, action: dict) -> None:
        action_type = action.get("type", "none")
        if action_type == "navigate":
            page.goto(action["url"], wait_until="domcontentloaded")
        elif action_type == "click":
            try:
                page.click(action["selector"], timeout=5000)
            except Exception:
                page.get_by_text(action["selector"]).first.click(timeout=5000)
        elif action_type == "type":
            page.fill(action["selector"], action["text"])
        elif action_type == "select":
            page.select_option(action["selector"], label=action["text"])
        elif action_type == "upload":
            if not getattr(self, "_upload_path", None):
                raise ValueError("this task has no file to upload")
            page.set_input_files(action["selector"], self._upload_path)
        elif action_type == "fill_many":
            # Each field on its own: one bad selector mustn't undo the rest.
            failed = []
            for field in action.get("fields", []):
                try:
                    box = page.locator(field["selector"]).first
                    if box.evaluate("e => e.tagName") == "SELECT":
                        box.select_option(label=field["text"])
                    else:
                        box.fill(field["text"])
                except Exception:
                    failed.append(field.get("selector", "?"))
            if failed:
                raise ValueError(f"couldn't fill {', '.join(failed)}")
        elif action_type == "scroll":
            page.mouse.wheel(0, 500 if action.get("direction") == "down" else -500)
        elif action_type == "wait":
            time.sleep(action.get("seconds", 2))
