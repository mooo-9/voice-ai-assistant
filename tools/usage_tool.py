"""
usage_report — spoken-friendly API cost and performance report.
Reads data/telemetry/*.jsonl written by core/telemetry.py.
cp1252-safe output (TTS-bound).
"""


def usage_report(days: int = 1) -> str:
    from core.telemetry import all_time_cost, cost_this_month, monthly_budget, summarize
    days = max(1, min(int(days or 1), 90))
    s = summarize(days=days)
    tail = ""
    budget = monthly_budget()
    if budget > 0:
        tail = (f" This month: ${cost_this_month():.2f} of the "
                f"${budget:.2f} budget used.")
    job_total = all_time_cost("career")
    if job_total > 0:
        job_now = s["by_source"].get("career", {}).get("cost_usd", 0.0)
        when = "today" if days == 1 else f"in the last {days} days"
        tail += (f" Job hunt: ${job_now:.2f} {when}, "
                 f"${job_total:.2f} since it started.")
    if s["requests"] == 0:
        period = "today" if days == 1 else f"the last {days} days"
        return f"No API usage recorded for {period}.{tail}"
    period = "Today" if days == 1 else f"Last {days} days"
    parts = [
        f"{period}: {s['requests']} API calls costing about "
        f"${s['cost_usd']:.2f}.",
        f"Tokens: {s['input_tokens']:,} in, {s['output_tokens']:,} out.",
        f"Average response time {s['avg_latency_ms'] / 1000:.1f} seconds.",
    ]
    if s["by_source"]:
        top = sorted(s["by_source"].items(),
                     key=lambda kv: -kv[1]["cost_usd"])[:3]
        breakdown = ", ".join(
            f"{name} ${b['cost_usd']:.2f} ({b['requests']} calls)"
            for name, b in top
        )
        parts.append(f"Breakdown: {breakdown}.")
    return " ".join(parts) + tail
