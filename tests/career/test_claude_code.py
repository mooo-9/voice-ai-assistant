"""The job hunt's questions go to Claude Code first, on Mo's subscription, and
to the API only when Claude Code can't answer -- a night never fails for it."""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core import telemetry
from core.career import claude

SCHEMA = {"type": "object", "properties": {"score": {"type": "integer"}}, "required": ["score"]}


def _done(out: dict):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(out), stderr="")


@pytest.fixture
def code(monkeypatch):
    monkeypatch.setattr(claude, "_code_exe", lambda: Path("C:/fake/claude.exe"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-key")
    with patch("subprocess.run") as run:
        yield run


def test_claude_code_answers_on_the_subscription(code):
    code.return_value = _done({"is_error": False, "structured_output": {"score": 70},
                               "usage": {"input_tokens": 900, "output_tokens": 50}})
    assert claude.ask("p", system="s", schema=SCHEMA, model="claude-opus-5") == {"score": 70}
    cmd, kw = code.call_args.args[0], code.call_args.kwargs
    assert cmd[0].endswith("claude.exe") and "--safe-mode" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"
    assert cmd[cmd.index("--tools") + 1] == ""          # a bare model call
    assert kw["input"] == "p"
    # With the key in its environment Claude Code would bill the API.
    assert "ANTHROPIC_API_KEY" not in kw["env"]
    # Counted, at no API cost.
    assert telemetry.cost_this_month() == 0


@pytest.mark.parametrize("out", [
    {"is_error": True, "result": "Not logged in · Please run /login"},
    {"is_error": False, "structured_output": {"wrong": 1}},
    {"is_error": False, "result": ""},
])
def test_the_api_answers_what_claude_code_cant(code, out):
    code.return_value = _done(out)
    with patch.object(claude, "_get_client") as get:
        message = get.return_value.messages.create.return_value
        message.stop_reason = "end_turn"
        message.content = [type("B", (), {"type": "text", "text": '{"score": 55}'})()]
        assert claude.ask("p", system="s", schema=SCHEMA) == {"score": 55}


def test_claude_code_timing_out_falls_back_too(code):
    code.side_effect = subprocess.TimeoutExpired("claude", 300)
    with patch.object(claude, "_get_client") as get:
        get.return_value.messages.create.return_value.stop_reason = "refusal"
        assert claude.ask("p", system="s", schema=SCHEMA) is None


def test_a_batch_sends_only_claude_codes_misses_to_the_api(code):
    def answer(cmd, input, **kw):
        if input == "b":
            return _done({"is_error": True, "result": "usage limit reached"})
        return _done({"is_error": False, "structured_output": {"score": ord(input)}})
    code.side_effect = answer
    asks = [{"prompt": p, "system": "s", "schema": SCHEMA} for p in "abc"]
    with patch.object(claude, "_api_batch", return_value=[{"score": 0}]) as api:
        assert claude.ask_batch(asks) == [{"score": 97}, {"score": 0}, {"score": 99}]
    assert [a["prompt"] for a in api.call_args.args[0]] == ["b"]


def test_a_hunt_costs_the_api_nothing_while_claude_code_is_there(monkeypatch):
    from core.career import pipeline
    assert pipeline.run_estimate() > 0
    monkeypatch.setattr(claude, "_code_exe", lambda: Path("C:/fake/claude.exe"))
    assert pipeline.run_estimate() == 0
