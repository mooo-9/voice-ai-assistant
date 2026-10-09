"""
Usage audit — is El Fager being used, and is the input pipeline healthy?

Reads data/conversations/, data/skills.json, data/skill_proposals.json and
data/telemetry/ and prints:
  - real turns per day (test-mode logs in conversations_test/ are excluded)
  - synthetic-loop contamination (exact user phrases repeated >= 10x)
  - transcription-garbage rate (user turns in scripts Mo doesn't speak)
  - tool usage leaderboard vs. features that never fire
  - skill run counts and proposal statuses
  - telemetry cost totals

Run:  python -X utf8 scripts/usage_audit.py
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_THRESHOLD = 10

# Scripts Mo never speaks: Hangul, Hebrew, Cyrillic, CJK, Thai — plus the
# Icelandic letters eth/thorn that Whisper hallucinates on silence.
_GARBAGE_RE = re.compile(r"[가-힯֐-׿Ѐ-ӿ一-鿿฀-๿ðþÞÐ]")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_turns():
    turns = []
    for f in sorted((ROOT / "data" / "conversations").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            d["_day"] = f.stem
            turns.append(d)
    return turns


def main():
    turns = load_turns()
    users = [t for t in turns if t.get("role") == "user"]
    print(f"=== El Fager usage audit ===")
    print(f"Total turns: {len(turns)}  (user: {len(users)}) across "
          f"{len({t['_day'] for t in turns})} days")

    print("\n-- Turns per day --")
    per_day = Counter(t["_day"] for t in users)
    for day in sorted(per_day):
        print(f"  {day}  {per_day[day]:4d}")

    print(f"\n-- Synthetic contamination (exact phrase >= {SYNTHETIC_THRESHOLD}x) --")
    phrase_counts = Counter(t.get("content", "") for t in users)
    synthetic = {p: n for p, n in phrase_counts.items() if n >= SYNTHETIC_THRESHOLD and p}
    if synthetic:
        for p, n in sorted(synthetic.items(), key=lambda x: -x[1]):
            print(f"  {n:4d}x {p[:70]!r}")
        print("  -> quarantine these (run with EL_FAGER_TEST_MODE=1 when testing)")
    else:
        print("  none — logs are clean")

    print("\n-- Transcription garbage rate --")
    garbage = [t for t in users if _GARBAGE_RE.search(t.get("content", ""))]
    rate = 100 * len(garbage) / max(len(users), 1)
    print(f"  {len(garbage)}/{len(users)} user turns ({rate:.1f}%) contain scripts "
          f"Mo doesn't speak (Whisper hallucination signal)")
    for t in garbage[:5]:
        print(f"    {t['_day']}  {t.get('content', '')[:60]!r}")

    print("\n-- Tool usage --")
    tools = Counter()
    for t in turns:
        for name in t.get("tools_used", []) or []:
            tools[name] += 1
    if tools:
        for name, n in tools.most_common(15):
            print(f"  {n:4d}  {name}")
    else:
        print("  no tools_used recorded")

    print("\n-- Skills --")
    skills_path = ROOT / "data" / "skills.json"
    if skills_path.exists():
        data = json.loads(skills_path.read_text(encoding="utf-8"))
        for s in data.get("skills", []):
            print(f"  runs={s.get('run_count', 0):3d}  {s.get('name')}")
    props_path = ROOT / "data" / "skill_proposals.json"
    if props_path.exists():
        props = json.loads(props_path.read_text(encoding="utf-8"))
        by_status = Counter(p.get("status") for p in props)
        print(f"  proposals: {dict(by_status)}")

    print("\n-- Telemetry cost --")
    total = 0.0
    events = 0
    job_total = 0.0
    job_calls = 0
    for f in sorted((ROOT / "data" / "telemetry").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            events += 1
            total += d.get("cost_usd", 0) or 0
            if d.get("source") == "career":
                job_calls += 1
                job_total += d.get("cost_usd", 0) or 0
    print(f"  {events} events, ${total:.4f} total")
    print(f"  job hunt: {job_calls} calls, ${job_total:.4f}")


if __name__ == "__main__":
    main()
