"""
Turn report — reads what core/turn_profile.py wrote and says where a voice
turn spends its time.

The profiler appends one JSON line per turn to
data/telemetry/turns-YYYY-MM-DD.jsonl (only while EL_FAGER_PROFILE=1). A day
of those lines is unreadable by eye, and the number that matters isn't in any
one field: what Mo feels is the gap between his last word and the first word
back, and that is spread across the silence timeout, transcription, the model
and the speech synthesis.

Run:  python -X utf8 scripts/turn_report.py            # today
      python -X utf8 scripts/turn_report.py 2026-08-18 # one day
      python -X utf8 scripts/turn_report.py --all      # every day on record
"""
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "telemetry"

# The recording only stops after this much continuous silence, so it sits in
# every voice turn's `record` span and none of it is the model's fault.
END_SILENCE_SEC = 1.0

SPANS = ("record", "stt", "memory")          # durations
MOMENTS = ("first_token", "first_audio", "last_audio")   # ms since turn start

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load(day: str) -> list:
    rows = []
    files = (sorted(DIR.glob("turns-*.jsonl")) if day == "--all"
             else [DIR / f"turns-{day}.jsonl"])
    for f in files:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def p90(values: list) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * 0.9), len(ordered) - 1)]


def line(name: str, values: list) -> None:
    if not values:
        return
    print(f"  {name:<34} median {st.median(values) / 1000:5.1f}s   "
          f"p90 {p90(values) / 1000:5.1f}s   n={len(values)}")


def derived(rows: list) -> dict:
    """Per-turn gaps the raw fields only imply."""
    out = defaultdict(list)
    for r in rows:
        s = r.get("stages", {})
        if "first_token" in s:
            before = sum(s.get(k, 0.0) for k in SPANS)
            out["think (until the first token)"].append(s["first_token"] - before)
        if "first_token" in s and "first_audio" in s:
            out["speak (token to first audio)"].append(
                s["first_audio"] - s["first_token"])
        if "record" in s and "first_audio" in s:
            # Mo stops talking one silence-timeout before `record` ends.
            out["wait after you stop speaking"].append(
                s["first_audio"] - s["record"] + END_SILENCE_SEC * 1000)
    return out


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    rows = load(day)
    if not rows:
        print(f"No turns recorded for {day}. Is EL_FAGER_PROFILE=1 set, and "
              f"has El Fager been restarted since?")
        return

    voice = sum(1 for r in rows if r.get("label", "").startswith("voice"))
    print(f"=== El Fager turn report — "
          f"{'every day on record' if day == '--all' else day} ===")
    print(f"turns: {len(rows)}  (voice {voice}, typed {len(rows) - voice})")

    print("\n-- stages --")
    for k in SPANS + MOMENTS:
        line(k, [r["stages"][k] for r in rows if k in r.get("stages", {})])

    print(f"\n-- derived (the {END_SILENCE_SEC:.0f}s silence timeout is in the wait) --")
    for name, values in derived(rows).items():
        line(name, values)

    totals: Counter = Counter()
    turns_used: Counter = Counter()
    for r in rows:
        for name, ms in r.get("tools", {}).items():
            totals[name] += ms
            turns_used[name] += 1
    if totals:
        print("\n-- tools --")
        for name, ms in totals.most_common(10):
            print(f"  {name:<34} {ms / 1000:5.1f}s over {turns_used[name]} turn(s)")


if __name__ == "__main__":
    main()
