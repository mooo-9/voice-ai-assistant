"""The one way the career pipeline talks to Claude. Every call is recorded by
telemetry under "career", so the daily cost shows in the usage audit.

Each question goes to Claude Code first: it runs on Mo's Claude subscription,
not the $10 API budget, and scored real jobs the same as the API in a side-by-
side test. The API stands in, question by question, when Claude Code isn't
installed or logged in, is over the plan's limit, or answers in the wrong
shape -- so a night never fails for it."""
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

MODEL = "claude-sonnet-5"
# The Big 4, and the companies Mo picked ("opus" in companies.json), are the
# goal: scoring, interview prep and referral notes aimed at them get the
# stronger model. Cover letters get it for the Big 4 only (tailor.py).
PREMIUM_MODEL = "claude-opus-5"

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        from core.telemetry import instrument_client
        _client = instrument_client(anthropic.Anthropic(), "career")
    return _client


_CODE_TIMEOUT = 300
_CODE_WORKERS = 4
_FAILED = object()
# Why Claude Code's last call failed ("Not logged in"...), "" when it worked:
# the job-hunt status shows it.
last_code_error = ""


def _code_exe() -> "Path | None":
    """claude.exe itself: npm's claude.CMD wrapper runs through cmd.exe, which
    mangles a multi-line prompt."""
    cmd = shutil.which("claude")
    if not cmd:
        return None
    exe = Path(cmd).parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    if exe.exists():
        return exe
    return Path(cmd) if Path(cmd).suffix.lower() == ".exe" else None


def code_available() -> bool:
    # ponytail: installed, not proven logged in; a logged-out Claude Code
    # just sends each question on to the API.
    return _code_exe() is not None


def _ask_code(prompt: str, *, system: str, schema: "dict | None" = None,
              effort: str = "medium", max_tokens: int = 8000, model: str = MODEL,
              image: "str | None" = None):
    """ask() through Claude Code, or _FAILED. A bare model call: no tools,
    skills, plugins or MCP servers (out of the box they add ~143k tokens to
    every call), and no API key in its environment -- with one it would bill
    the API. Claude Code sets its own output limit, so max_tokens is unused.
    A screenshot (`image`, base64 PNG) goes in as a stream-json message."""
    exe = _code_exe()
    if exe is None:
        return _FAILED
    fmt = ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"] \
        if image else ["--output-format", "json"]
    cmd = [str(exe), "-p", "--safe-mode", "--model", model, "--effort", effort, *fmt,
           "--max-turns", "3", "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--system-prompt", system]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    if image:
        prompt = json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": image}},
            {"type": "text", "text": prompt}]}}) + "\n"
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", env=env, timeout=_CODE_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if image:     # one JSON event a line; the answer is the "result" one
            out = next(e for e in map(json.loads, reversed(proc.stdout.splitlines()))
                       if e.get("type") == "result")
        else:
            out = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
        return _FAILED
    global last_code_error
    if out.get("is_error"):
        last_code_error = (str(out.get("result") or "error").splitlines() or ["error"])[0][:80]
        return _FAILED
    last_code_error = ""
    answer = out.get("structured_output") if schema else (out.get("result") or "").strip()
    if not answer or (schema and not (isinstance(answer, dict)
                                      and all(k in answer for k in schema.get("required", [])))):
        return _FAILED
    from core.telemetry import record_api_usage
    usage = {k: v for k, v in (out.get("usage") or {}).items() if k.endswith("_tokens")}
    record_api_usage("career", model, SimpleNamespace(**usage),
                     (time.monotonic() - started) * 1000, subscription=True)
    return answer


def model_for(company: str) -> str:
    from core.career import companies
    target = companies.match(company)
    return PREMIUM_MODEL if target and target in companies.premium() else MODEL


def _params(prompt: str, *, system: str, schema: "dict | None" = None,
            effort: str = "medium", max_tokens: int = 8000, model: str = MODEL,
            image: "str | None" = None) -> dict:
    output_config: dict = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "output_config": output_config,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": image}},
            {"type": "text", "text": prompt}] if image else prompt}],
    }


def _answer(message, schema: "dict | None"):
    if message.stop_reason == "refusal":
        return None
    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text) if schema else text.strip()


def ask(prompt: str, *, system: str, schema: "dict | None" = None,
        effort: str = "medium", max_tokens: int = 8000, model: str = MODEL,
        image: "str | None" = None):
    """Claude's answer: parsed JSON when `schema` is given, text otherwise.
    None when the model declines. `image` is a base64 PNG shown with the prompt."""
    answer = _ask_code(prompt, system=system, schema=schema, effort=effort,
                       max_tokens=max_tokens, model=model, image=image)
    if answer is not _FAILED:
        return answer
    response = _get_client().messages.create(**_params(
        prompt, system=system, schema=schema, effort=effort,
        max_tokens=max_tokens, model=model, image=image))
    return _answer(response, schema)


# Most batches end within minutes; the API guarantees an end within 24 hours.
_BATCH_POLL_SECONDS = 30


def ask_batch(asks: "list[dict]") -> list:
    """ask() for many prompts at once. Each item holds ask()'s keyword
    arguments, with the prompt under "prompt". Claude Code answers what it
    can, a few at a time; the rest go to the API as one Message Batch. Answers
    come back in the same order; None for any that was declined, failed or
    couldn't be read."""
    if not asks:
        return []
    with ThreadPoolExecutor(_CODE_WORKERS) as pool:
        answers = list(pool.map(lambda a: _ask_code(**a), asks))
    todo = [i for i, a in enumerate(answers) if a is _FAILED]
    for i, answer in zip(todo, _api_batch([asks[i] for i in todo])):
        answers[i] = answer
    return answers


def _api_batch(asks: "list[dict]") -> list:
    """The API's Message Batch: half the price, answered in minutes rather
    than seconds."""
    if not asks:
        return []
    from core.telemetry import check_budget, record_api_usage
    check_budget()
    client = _get_client()
    batch = client.messages.batches.create(requests=[
        {"custom_id": str(i), "params": _params(**a)} for i, a in enumerate(asks)])
    started = time.monotonic()
    while batch.processing_status != "ended":
        time.sleep(_BATCH_POLL_SECONDS)
        batch = client.messages.batches.retrieve(batch.id)
    waited_ms = (time.monotonic() - started) * 1000
    answers: list = [None] * len(asks)
    # Results arrive in any order: matched back by custom_id.
    for entry in client.messages.batches.results(batch.id):
        if entry.result.type != "succeeded":
            continue
        i = int(entry.custom_id)
        message = entry.result.message
        record_api_usage("career", message.model, message.usage, waited_ms, batch=True)
        try:
            answers[i] = _answer(message, asks[i].get("schema"))
        except ValueError:
            pass    # one unreadable answer mustn't cost the rest of the run
    return answers
