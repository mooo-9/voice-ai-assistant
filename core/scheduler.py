"""
ElFagerScheduler — APScheduler-backed proactive job runner.

Jobs are persisted to data/schedules.json and re-registered on startup.
Each firing logs to data/schedule_history.jsonl.

Singleton: main.py calls _set_instance() once; all tool code uses get_instance().
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False

_SCHEDULES_FILE = Path("data/schedules.json")
_HISTORY_FILE   = Path("data/schedule_history.jsonl")
_TZ             = os.getenv("SCHEDULER_TIMEZONE", "Africa/Cairo")
_TTS_MAX_CHARS  = 300   # trim spoken output to avoid 5-minute readings

_GLOBAL_INSTANCE: "ElFagerScheduler | None" = None


def get_instance() -> "ElFagerScheduler | None":
    return _GLOBAL_INSTANCE


def _set_instance(inst: "ElFagerScheduler") -> None:
    global _GLOBAL_INSTANCE
    _GLOBAL_INSTANCE = inst


# ──────────────────────────────────────────────────────────────────────────────
# JSON helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_schedules() -> list[dict]:
    if _SCHEDULES_FILE.exists():
        try:
            return json.loads(_SCHEDULES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_schedules(data: list[dict]) -> None:
    _SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SCHEDULES_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _append_history(entry: dict) -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ──────────────────────────────────────────────────────────────────────────────
# Direct tool dispatcher  (no Brain / no Claude API needed for scheduled calls)
# ──────────────────────────────────────────────────────────────────────────────

_TOOL_WHITELIST = {
    # weather
    "get_weather", "get_weather_forecast", "get_hourly_weather",
    # news
    "get_all_headlines", "get_news", "search_news",
    # calendar / email
    "list_calendar_events", "list_emails",
    # journal / finance
    "get_journal_stats", "journal_streak", "mood_summary",
    "get_expense_summary", "list_recent_expenses",
    # analytics
    "weekly_report", "spending_insights", "journal_insights",
    "productivity_insights", "conversation_stats", "top_tools", "mood_trend",
    # smart home — Hue
    "smart_home_status", "list_hue_lights", "list_hue_groups",
    "hue_on", "hue_off", "hue_toggle",
    "hue_scene", "hue_brightness", "hue_color", "hue_temperature",
    "hue_wake_up", "hue_fade_off", "hue_alert", "hue_effect",
    "hue_group_on", "hue_group_off",
    "hue_group_brightness", "hue_group_scene", "hue_group_temperature",
    # smart home — Kasa
    "kasa_list_devices", "kasa_on", "kasa_off", "kasa_toggle", "kasa_status",
    "kasa_power_usage", "kasa_energy_today",
    # system
    "get_battery_status", "get_system_volume", "mute_system",
    "get_process_info",
    # reminders / tasks
    "list_reminders", "list_tasks",
    # music
    "play_music", "pause_music", "next_track", "what_playing",
    # focus / pomodoro
    "enable_focus_mode", "disable_focus_mode",
    "start_pomodoro", "stop_pomodoro",
    # code
    "run_python", "run_powershell",
    # history
    "read_conversation", "conversation_stats",
    # macro
    "run_macro",
    # the job hunt Mo arms for tonight from the AUTOMATIONS panel
    "run_job_hunt",
}


def _dispatch_scheduled_tool(tool_name: str, args: dict) -> str:
    """Call a tool directly without going through Brain/Claude."""
    if tool_name not in _TOOL_WHITELIST:
        return (
            f"Tool '{tool_name}' is not available for scheduled dispatch. "
            "Use a macro instead, or check tool name spelling."
        )
    try:
        if tool_name == "run_macro":
            from tools.macro_tool import run_macro
            return run_macro(args.get("name", ""))
        if tool_name == "run_job_hunt":
            from tools.career_tool import run_job_hunt
            return run_job_hunt()
        from tools.macro_tool import _dispatch_macro_step
        return _dispatch_macro_step(tool_name, args)
    except Exception as e:
        return f"Tool '{tool_name}' failed: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# ElFagerScheduler
# ──────────────────────────────────────────────────────────────────────────────

class ElFagerScheduler:
    def __init__(self):
        if _APSCHEDULER_AVAILABLE:
            self._scheduler = BackgroundScheduler(timezone=_TZ)
        else:
            self._scheduler = None
        self._available   = _APSCHEDULER_AVAILABLE
        self._speak_fn: Callable[[str], None] | None = None
        self._start_time: datetime | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def set_speak_callback(self, speak_fn: Callable[[str], None]) -> None:
        self._speak_fn = speak_fn

    def start(self) -> None:
        if not self._available:
            print("[Scheduler] APScheduler not installed — scheduler disabled.")
            return
        self._scheduler.start()
        self._start_time = datetime.now()
        schedules = _load_schedules()
        registered = 0
        for job in schedules:
            if job.get("enabled", True):
                try:
                    self._register_job(job)
                    registered += 1
                except Exception as e:
                    print(f"[Scheduler] Failed to register '{job.get('name')}': {e}")
        print(f"[Scheduler] Started ({_TZ}) — {registered}/{len(schedules)} job(s) active.")

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def status(self) -> dict:
        s = {
            "available": self._available,
            "running": bool(self._available and self._scheduler and self._scheduler.running),
            "timezone": _TZ,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "jobs_registered": 0,
            "jobs_total": len(_load_schedules()),
        }
        if s["running"]:
            s["jobs_registered"] = len(self._scheduler.get_jobs())
        return s

    # ── Job CRUD ──────────────────────────────────────────────────────────

    def add_job(self, job_dict: dict) -> bool:
        """Persist job and register with live scheduler. Returns True if it replaced an existing job."""
        schedules = _load_schedules()
        replaced = any(s.get("id") == job_dict["id"] for s in schedules)
        schedules = [s for s in schedules if s.get("id") != job_dict["id"]]
        schedules.append(job_dict)
        _save_schedules(schedules)
        if self._available and self._scheduler.running and job_dict.get("enabled", True):
            try:
                self._register_job(job_dict)
            except Exception as e:
                print(f"[Scheduler] Live-register failed for '{job_dict.get('name')}': {e}")
        return replaced

    def remove_job(self, job_id: str) -> bool:
        schedules = _load_schedules()
        before = len(schedules)
        schedules = [s for s in schedules if s.get("id") != job_id]
        _save_schedules(schedules)
        if self._available:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        return len(schedules) < before

    def pause_job(self, job_id: str) -> bool:
        schedules = _load_schedules()
        for s in schedules:
            if s.get("id") == job_id:
                s["enabled"] = False
                break
        _save_schedules(schedules)
        if self._available:
            try:
                self._scheduler.pause_job(job_id)
                return True
            except Exception:
                pass
        return False

    def resume_job(self, job_id: str) -> bool:
        schedules = _load_schedules()
        job_dict = None
        for s in schedules:
            if s.get("id") == job_id:
                s["enabled"] = True
                job_dict = s
                break
        _save_schedules(schedules)
        if self._available and job_dict:
            try:
                self._scheduler.resume_job(job_id)
                return True
            except Exception:
                try:
                    self._register_job(job_dict)
                    return True
                except Exception:
                    pass
        return False

    def pause_all(self) -> int:
        schedules = _load_schedules()
        count = 0
        for s in schedules:
            if s.get("enabled", True):
                s["enabled"] = False
                count += 1
        _save_schedules(schedules)
        if self._available and self._scheduler.running:
            self._scheduler.pause()
        return count

    def resume_all(self) -> int:
        schedules = _load_schedules()
        count = 0
        for s in schedules:
            if not s.get("enabled", True):
                s["enabled"] = True
                count += 1
        _save_schedules(schedules)
        if self._available and self._scheduler.running:
            self._scheduler.resume()
        return count

    def reschedule_job(self, job_id: str, trigger_spec: dict) -> bool:
        """Change only the trigger of an existing job, keeping its action."""
        schedules = _load_schedules()
        match = next((s for s in schedules if s.get("id") == job_id), None)
        if not match:
            return False
        match["trigger"] = trigger_spec
        _save_schedules(schedules)
        if self._available and self._scheduler.running:
            try:
                trigger = self._build_trigger(trigger_spec)
                self._scheduler.reschedule_job(job_id, trigger=trigger)
                return True
            except Exception:
                try:
                    self._register_job(match)
                    return True
                except Exception:
                    pass
        return True  # saved to disk at least

    def run_now(self, job_id: str) -> str:
        schedules = _load_schedules()
        match = next((s for s in schedules if s.get("id") == job_id), None)
        if not match:
            return f"No job with id '{job_id}'."
        return self._run_job(match) or f"'{match.get('name')}' completed (no output)."

    # ── Job info ──────────────────────────────────────────────────────────

    def _enrich(self, entry: dict) -> dict:
        """Add live next_run_time from APScheduler if available."""
        result = dict(entry)
        if self._available and self._scheduler.running:
            try:
                job = self._scheduler.get_job(entry["id"])
                if job and job.next_run_time:
                    result["next_run"] = job.next_run_time.isoformat()
                    delta = job.next_run_time - datetime.now(job.next_run_time.tzinfo)
                    result["next_run_in_seconds"] = int(delta.total_seconds())
            except Exception:
                pass
        return result

    def get_job_info(self, job_id: str) -> dict | None:
        match = next((s for s in _load_schedules() if s.get("id") == job_id), None)
        return self._enrich(match) if match else None

    def list_job_info(self) -> list[dict]:
        schedules = _load_schedules()
        enriched = [self._enrich(s) for s in schedules]
        # Sort: active before paused, then by next_run (soonest first)
        def sort_key(j):
            active = 0 if j.get("enabled", True) else 1
            secs   = j.get("next_run_in_seconds", 999999)
            return (active, secs)
        return sorted(enriched, key=sort_key)

    # ── History ───────────────────────────────────────────────────────────

    def get_history(self, n: int = 20, job_id: str | None = None) -> list[dict]:
        if not _HISTORY_FILE.exists():
            return []
        lines = _HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        entries: list[dict] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if job_id and entry.get("job_id") != job_id:
                    continue
                entries.append(entry)
                if len(entries) >= n:
                    break
            except Exception:
                pass
        return entries

    def history_stats(self) -> dict:
        """Aggregate stats across all history."""
        if not _HISTORY_FILE.exists():
            return {}
        stats: dict[str, dict] = {}
        for line in _HISTORY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                jid = e.get("job_id", "?")
                if jid not in stats:
                    stats[jid] = {"name": e.get("job_name", jid), "runs": 0, "errors": 0, "total_ms": 0}
                stats[jid]["runs"] += 1
                if e.get("status") != "ok":
                    stats[jid]["errors"] += 1
                stats[jid]["total_ms"] += e.get("duration_ms", 0)
            except Exception:
                pass
        return stats

    # ── Internal ──────────────────────────────────────────────────────────

    def _register_job(self, job_dict: dict) -> None:
        trigger = self._build_trigger(job_dict["trigger"])
        # Add small random jitter to cron jobs to avoid simultaneous firing
        jitter = 30 if job_dict.get("trigger", {}).get("type") == "cron" else 0
        self._scheduler.add_job(
            func=self._run_job,
            trigger=trigger,
            id=job_dict["id"],
            name=job_dict.get("name", job_dict["id"]),
            args=[job_dict],
            replace_existing=True,
            misfire_grace_time=600,
            max_instances=1,
            coalesce=True,
            jitter=jitter,
        )

    def _build_trigger(self, spec: dict):
        t = spec.get("type")
        if t == "cron":
            return CronTrigger(timezone=_TZ, **{k: v for k, v in spec.items() if k != "type"})
        if t == "interval":
            return IntervalTrigger(**{k: v for k, v in spec.items() if k != "type"})
        if t == "date":
            return DateTrigger(run_date=spec.get("run_date"))
        raise ValueError(f"Unknown trigger type: {t!r}")

    def _run_job(self, job_dict: dict) -> str:
        start  = datetime.now()
        action = job_dict.get("action", {})
        atype  = action.get("type")
        result = ""
        status = "ok"

        try:
            if atype == "macro":
                from tools.macro_tool import run_macro
                result = run_macro(action["name"])
            elif atype == "tool":
                result = _dispatch_scheduled_tool(action["tool"], action.get("args", {}))
            elif atype == "python":
                from tools.code_tool import run_python
                result = run_python(action.get("code", ""))
            elif atype == "notify":
                result = action.get("text", "")
            else:
                result = f"Unknown action type: {atype!r}"
                status = "error"
        except Exception as e:
            result = f"Job '{job_dict.get('name')}' failed: {e}"
            status = "error"

        duration_ms = int((datetime.now() - start).total_seconds() * 1000)
        name = job_dict.get("name", "Scheduled task")

        _append_history({
            "job_id":         job_dict.get("id"),
            "job_name":       name,
            "fired_at":       start.isoformat(),
            "status":         status,
            "duration_ms":    duration_ms,
            "result_preview": result[:300] if result else "",
        })

        # Auto-remove one-shot date jobs after firing
        if job_dict.get("trigger", {}).get("type") == "date":
            self.remove_job(job_dict["id"])

        if result:
            self._deliver(name, result)

        return result

    def _deliver(self, job_name: str, result: str) -> None:
        """Send result to TTS (trimmed) and Windows toast (full)."""
        # Toast — always, with full result
        preview = result[:512]
        try:
            from winotify import Notification
            toast = Notification(
                app_id="El Fager",
                title=f"Scheduled: {job_name}",
                msg=preview,
                duration="long",
            )
            toast.show()
        except Exception:
            pass

        # TTS — trimmed to avoid reading out 10-paragraph briefings
        spoken = result[:_TTS_MAX_CHARS]
        if len(result) > _TTS_MAX_CHARS:
            spoken += "… (see notification for full result)"
        if self._speak_fn:
            try:
                self._speak_fn(f"{job_name}: {spoken}")
            except Exception:
                pass
        else:
            print(f"[Scheduler] {job_name}: {spoken}")
