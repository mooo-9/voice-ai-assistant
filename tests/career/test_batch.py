"""The job hunt's scoring and letters go to Claude as Message Batches: half
the price of asking one by one, for Mo's $10-a-month API budget."""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import telemetry
from core.career import claude


def _usage(inp=1_000_000, out=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _entry(i, kind="succeeded", text='{"score": 70}', stop="end_turn"):
    message = SimpleNamespace(model="claude-sonnet-5", usage=_usage(), stop_reason=stop,
                              content=[SimpleNamespace(type="text", text=text)])
    return SimpleNamespace(custom_id=str(i),
                           result=SimpleNamespace(type=kind, message=message))


def _ask(prompt="p"):
    return {"prompt": prompt, "system": "s", "schema": {"type": "object"}}


@pytest.fixture
def client():
    with patch.object(claude, "_get_client") as get, patch("time.sleep") as sleep:
        batches = get.return_value.messages.batches
        batches.create.return_value = SimpleNamespace(id="b1", processing_status="in_progress")
        batches.retrieve.side_effect = [SimpleNamespace(id="b1", processing_status="in_progress"),
                                        SimpleNamespace(id="b1", processing_status="ended")]
        batches.sleep = sleep
        yield batches


class TestAskBatch:
    def test_answers_come_back_in_the_order_asked(self, client):
        # Results arrive in any order; they're matched back by custom_id.
        client.results.return_value = [_entry(2, text='{"score": 3}'),
                                       _entry(0, text='{"score": 1}'),
                                       _entry(1, text='{"score": 2}')]
        answers = claude.ask_batch([_ask("a"), _ask("b"), _ask("c")])
        assert answers == [{"score": 1}, {"score": 2}, {"score": 3}]
        sent = client.create.call_args.kwargs["requests"]
        assert [r["custom_id"] for r in sent] == ["0", "1", "2"]
        assert sent[1]["params"]["messages"][0]["content"] == "b"

    def test_waits_until_the_batch_has_ended(self, client):
        client.results.return_value = [_entry(0)]
        claude.ask_batch([_ask()])
        assert client.retrieve.call_count == 2
        assert client.sleep.call_count == 2

    def test_a_declined_failed_or_unreadable_answer_is_none(self, client):
        client.results.return_value = [_entry(0, stop="refusal"), _entry(1, kind="errored"),
                                       _entry(2, text="not json"), _entry(3)]
        assert claude.ask_batch([_ask()] * 4) == [None, None, None, {"score": 70}]

    def test_each_answer_is_costed_at_half_price_as_job_hunt_spend(self, client):
        client.results.return_value = [_entry(0), _entry(1)]
        claude.ask_batch([_ask(), _ask()])
        day = telemetry._TELEMETRY_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"
        rows = [json.loads(line) for line in day.read_text(encoding="utf-8").splitlines()]
        # 1M input tokens on Sonnet 5 is $2.00; batched, $1.00.
        assert [(r["source"], r["cost_usd"], r["batch"]) for r in rows] == [
            ("career", 1.0, True), ("career", 1.0, True)]

    def test_nothing_is_sent_over_the_monthly_budget(self, client):
        telemetry._TELEMETRY_DIR.mkdir(parents=True)
        (telemetry._TELEMETRY_DIR / f"{datetime.now():%Y-%m}-01.jsonl").write_text(
            json.dumps({"source": "chat", "cost_usd": 10.0}) + "\n", encoding="utf-8")
        with pytest.raises(telemetry.BudgetExceeded):
            claude.ask_batch([_ask()])
        client.create.assert_not_called()

    def test_nothing_to_ask_sends_nothing(self, client):
        assert claude.ask_batch([]) == []
        client.create.assert_not_called()


def test_a_batch_answer_is_recorded_at_half_the_normal_cost():
    telemetry.record_api_usage("career", "claude-sonnet-5", _usage(out=100_000), 1.0)
    telemetry.record_api_usage("career", "claude-sonnet-5", _usage(out=100_000), 1.0,
                               batch=True)
    day = telemetry._TELEMETRY_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"
    normal, batched = [json.loads(line) for line in day.read_text(encoding="utf-8").splitlines()]
    assert normal["cost_usd"] == 3.0 and "batch" not in normal       # $2 in + $1 out
    assert batched["cost_usd"] == 1.5 and batched["batch"] is True
