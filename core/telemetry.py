"""
Telemetry — API usage logging and cost metering for El Fager.

Every Claude API call the brain makes is appended as one JSON line to
data/telemetry/YYYY-MM-DD.jsonl with a computed cost estimate. Telemetry must
NEVER raise — observability failures must not break the assistant. The one
exception is check_budget(): once the month's spend reaches the monthly budget
it stops the next API call with BudgetExceeded.

Pricing cached 2026-07-05 (per 1M tokens, USD) from the claude-api reference.
Cache reads bill at ~0.1x input price; cache writes at ~1.25x (5-min TTL).
Unknown models fall back to Sonnet-tier pricing.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

_TELEMETRY_DIR = Path("data/telemetry")
_SETTINGS_FILE = Path("data/settings.json")
_DEFAULT_MONTHLY_BUDGET_USD = 10.0

# (input $/1M, output $/1M) — matched by substring against the model id
_PRICING: list[tuple[str, float, float]] = [
    ("fable-5", 10.0, 50.0),
    ("mythos-5", 10.0, 50.0),
    ("opus", 5.0, 25.0),
    ("sonnet-5", 2.0, 10.0),
    ("sonnet", 3.0, 15.0),
    ("haiku", 1.0, 5.0),
]
_FALLBACK = (3.0, 15.0)  # sonnet-tier

_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 1.25
_BATCH_MULT = 0.5       # Message Batches bill every token at half price


def _prices_for(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    for token, inp, out in _PRICING:
        if token in m:
            return inp, out
    return _FALLBACK


def estimate_cost(model: str, usage) -> float:
    """USD cost estimate for one API response's usage object."""
    inp_price, out_price = _prices_for(model)
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = (
        inp * inp_price
        + out * out_price
        + cache_w * inp_price * _CACHE_WRITE_MULT
        + cache_r * inp_price * _CACHE_READ_MULT
    ) / 1_000_000
    return round(cost, 6)


def record_api_usage(source: str, model: str, usage, latency_ms: float,
                     tools_used: list[str] | None = None, batch: bool = False,
                     subscription: bool = False) -> None:
    """Append one usage record. Swallows every error by design. A Message
    Batch answer (batch=True) is billed at half price; one Claude Code
    answered on Mo's subscription (subscription=True) costs the API nothing."""
    try:
        cost = 0.0 if subscription else estimate_cost(model, usage)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "model": model,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(
                usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(
                usage, "cache_read_input_tokens", 0) or 0,
            "cost_usd": round(cost * _BATCH_MULT, 6) if batch else cost,
            "latency_ms": round(float(latency_ms), 1),
            "tools_used": tools_used or [],
        }
        if batch:
            entry["batch"] = True
        if subscription:
            entry["subscription"] = True
        _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y-%m-%d")
        with (_TELEMETRY_DIR / f"{day}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _iter_entries(days: int):
    for offset in range(days):
        day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        fpath = _TELEMETRY_DIR / f"{day}.jsonl"
        if not fpath.exists():
            continue
        for line in fpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                pass


def summarize(days: int = 7) -> dict:
    """Totals + per-source breakdown across the last `days` daily files."""
    total_cost = 0.0
    total_in = 0
    total_out = 0
    latencies: list[float] = []
    by_source: dict[str, dict] = {}
    requests = 0
    for e in _iter_entries(days):
        requests += 1
        cost = float(e.get("cost_usd", 0) or 0)
        total_cost += cost
        total_in += int(e.get("input_tokens", 0) or 0)
        total_out += int(e.get("output_tokens", 0) or 0)
        if e.get("latency_ms") is not None:
            latencies.append(float(e["latency_ms"]))
        src = e.get("source", "unknown")
        bucket = by_source.setdefault(src, {"requests": 0, "cost_usd": 0.0})
        bucket["requests"] += 1
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)
    return {
        "days": days,
        "requests": requests,
        "cost_usd": round(total_cost, 6),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "by_source": by_source,
    }


def cost_today() -> float:
    return summarize(days=1)["cost_usd"]


def _cost_in(pattern: str, source: "str | None" = None) -> float:
    """Spend across the daily files matching `pattern`, optionally one source."""
    total = 0.0
    try:
        for fpath in _TELEMETRY_DIR.glob(pattern):
            for line in fpath.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if source is None or e.get("source") == source:
                    total += float(e.get("cost_usd", 0) or 0)
    except Exception:
        pass
    return round(total, 6)


def all_time_cost(source: str) -> float:
    """Every recorded day's spend for one source ("career" is the job hunt)."""
    return _cost_in("20*.jsonl", source)


def cost_this_month() -> float:
    return _cost_in(f"{datetime.now():%Y-%m}-*.jsonl")


class BudgetExceeded(Exception):
    """This month's API spend has reached the monthly budget."""


def monthly_budget() -> float:
    """data/settings.json 'api_monthly_budget_usd'; 0 turns the cap off."""
    try:
        settings = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        return float(settings.get("api_monthly_budget_usd", _DEFAULT_MONTHLY_BUDGET_USD))
    except Exception:
        return _DEFAULT_MONTHLY_BUDGET_USD


def check_budget() -> None:
    """Raise BudgetExceeded once this month's spend has reached the budget.
    Runs before every API call; the one place telemetry raises on purpose."""
    budget = monthly_budget()
    if budget > 0 and cost_this_month() >= budget:
        raise BudgetExceeded(f"This month's ${budget:.2f} API budget is used up.")


def instrument_client(client, source: str):
    """Wrap client.messages.create so every call is timed and recorded, and
    refused with BudgetExceeded once the monthly budget is spent.
    Idempotent. Returns the same client for chaining."""
    try:
        import time as _time
        if getattr(client.messages, "_elf_instrumented", False):
            return client
        original = client.messages.create

        def _create(*args, **kwargs):
            check_budget()
            started = _time.monotonic()
            response = original(*args, **kwargs)
            try:
                record_api_usage(
                    source,
                    kwargs.get("model", "unknown"),
                    response.usage,
                    (_time.monotonic() - started) * 1000,
                )
            except Exception:
                pass
            return response

        client.messages.create = _create
        client.messages._elf_instrumented = True
    except Exception:
        pass
    return client
