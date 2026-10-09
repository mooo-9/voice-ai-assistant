"""
Dashboard — read-only LAN status surface for El Fager.

Serves a single dark status page (auto-refresh) plus /api/status JSON on
http://<laptop-ip>:8765 so Mo can watch missions, costs, tasks, and skills
from his phone on the same network.

Deliberately READ-ONLY and stdlib-only: GET requests, no commands, no
secrets in the snapshot. Commands stay voice/desktop-side.

Settings (data/settings.json): dashboard_enabled (default true),
dashboard_port (default 8765), dashboard_host (default 127.0.0.1 — set to
"0.0.0.0" explicitly to allow phone/LAN access; the command channel can run
code through the brain, so LAN exposure is opt-in).
"""
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SETTINGS_PATH = Path("data/settings.json")
_DATA = Path("data")
_FONT_DIR = Path(__file__).parent.parent / "ui" / "assets" / "fonts"

_STARTED_AT = datetime.now()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ──────────────────────────────────────────────────────────────────────────────
# Design tokens
#
# Generated from ui/tokens.py rather than hand-copied, so the phone cannot
# drift from the desktop palette. The names match the handoff's shipped
# tokens/el-fager-tokens.css, which exists for exactly this surface.
#
# The phone is the companion to the "today" view, so it runs the DAWN palette
# (ember on blue-black) like the Command Center. Cyan belongs to the Cockpit.
# ──────────────────────────────────────────────────────────────────────────────


def _token_css() -> str:
    from ui import tokens as t

    lines = [
        ("bg-void", t.BG_VOID), ("surface-0", t.SURFACE_0),
        ("surface-1", t.SURFACE_1), ("surface-2", t.SURFACE_2),
        ("surface-3", t.SURFACE_3),
        ("stroke-hairline", t.HAIRLINE),
        ("stroke-hairline-strong", t.HAIRLINE_STRONG),
        ("text-hi", t.TEXT_HI), ("text-mid", t.TEXT_MID), ("text-low", t.TEXT_LOW),
        ("text-on-ember", t.TEXT_ON_EMBER),
        ("accent-ember", t.EMBER), ("accent-bright", t.EMBER_BRIGHT),
        ("accent-press", t.EMBER_PRESS), ("accent-wash", t.EMBER_WASH),
        ("state-idle", t.STATE["idle"]), ("state-listening", t.STATE["listening"]),
        ("state-thinking", t.STATE["thinking"]),
        ("state-speaking", t.STATE["speaking"]), ("state-error", t.STATE["error"]),
        ("sem-ok", t.OK), ("sem-warn", t.WARN), ("sem-bad", t.BAD),
        ("font-ui", f"'{t.FONT_UI}',-apple-system,"
                    "BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif"),
        ("font-mono", f"'{t.FONT_MONO}','Consolas',monospace"),
        ("s-1", f"{t.S1}px"), ("s-2", f"{t.S2}px"), ("s-3", f"{t.S3}px"),
        ("s-4", f"{t.S4}px"), ("s-5", f"{t.S5}px"), ("s-6", f"{t.S6}px"),
        ("r-1", f"{t.R1}px"), ("r-2", f"{t.R2}px"), ("r-3", f"{t.R3}px"),
        ("r-4", f"{t.R4}px"), ("r-pill", f"{t.R_PILL}px"),
        ("t-instant", f"{t.T_INSTANT}ms"), ("t-fast", f"{t.T_FAST}ms"),
        ("t-standard", f"{t.T_STANDARD}ms"),
        ("ease-swift", "cubic-bezier(0.22,1,0.36,1)"),
    ]
    return ":root{\n" + "\n".join(f"  --{k}:{v};" for k, v in lines) + "\n}"


# The bundled OFL faces, served from /fonts/ — a phone has none of them
# installed, and the handoff's rule is that nothing is ever fetched at runtime.
_FONTS = {
    "SpaceGrotesk.ttf": ("Space Grotesk", "400 600"),
    "SplineSansMono.ttf": ("Spline Sans Mono", "400 500"),
}


def _font_css() -> str:
    return "\n".join(
        f"@font-face{{font-family:'{family}';src:url('/fonts/{filename}')"
        f" format('truetype');font-weight:{weights};font-display:swap;}}"
        for filename, (family, weights) in _FONTS.items()
        if (_FONT_DIR / filename).exists()
    )


_MANIFEST = json.dumps({
    "name": "El Fager",
    "short_name": "El Fager",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#07080C",
    "theme_color": "#07080C",
    "start_url": "/",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
})

# The mark: the ember sun over its horizon, the same shape the day-arc paints.
_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
<rect width="192" height="192" rx="42" fill="#07080C"/>
<path d="M40 124a56 56 0 0 1 112 0" fill="none" stroke="#F09950" stroke-width="10"
 stroke-linecap="round"/>
<line x1="28" y1="140" x2="164" y2="140" stroke="#5C6478" stroke-width="6"
 stroke-linecap="round"/>
<circle cx="96" cy="76" r="14" fill="#FFD9A3"/></svg>"""


def _profile_section() -> dict:
    """Greeting + identity from profile.json (fast on-disk read)."""
    prof = _read_json(Path("profile.json"), {})
    now = datetime.now()
    hour = now.hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 18:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    return {
        "name": prof.get("name", "Mo"),
        "greeting": greeting,
        "location": prof.get("location", ""),
        "date_str": now.strftime("%A, %B %d"),
    }


def _nutrition_section() -> dict:
    """Today's macros vs targets — the Whoop-style rings.
    Reads meal_log.json (today's entry) and health_profile.json targets."""
    health = _read_json(_DATA / "health_profile.json", {})
    targets = health.get("targets", {}) or {}
    # overrides (e.g. protein) win over computed targets
    for k, v in (health.get("overrides", {}) or {}).items():
        targets[k] = v

    today = datetime.now().strftime("%Y-%m-%d")
    log = _read_json(_DATA / "meal_log.json", {})
    totals = {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    meals = 0
    for entry in log.get("entries", []):
        if entry.get("date") == today:
            t = entry.get("totals", {}) or {}
            for k in totals:
                totals[k] += float(t.get(k, 0) or 0)
            meals += len(entry.get("items", []))

    def ring(cur_key, tgt_key):
        cur = round(totals.get(cur_key, 0))
        tgt = round(float(targets.get(tgt_key, 0) or 0))
        return {"value": cur, "target": tgt}

    return {
        "logged": meals > 0,
        "meals": meals,
        "kcal": ring("kcal", "kcal"),
        "protein": ring("protein_g", "protein_g"),
        "carbs": ring("carbs_g", "carbs_g"),
        "fat": ring("fat_g", "fat_g"),
    }


def _schedule_section() -> list:
    """Today's routine — enabled recurring schedules as a simple timeline."""
    schedules = _read_json(_DATA / "schedules.json", [])
    out = []
    for s in schedules:
        if not s.get("enabled", True):
            continue
        trig = s.get("trigger", {}) or {}
        ttype = trig.get("type")
        when = ""
        sort_key = 99.0
        if ttype == "cron":
            h = trig.get("hour")
            m = trig.get("minute", 0) or 0
            if h is not None:
                when = f"{int(h):02d}:{int(m):02d}"
                sort_key = int(h) + int(m) / 60
                dow = trig.get("day_of_week")
                if dow:
                    when = f"{str(dow).title()} {when}"
            else:
                when = "daily"
        elif ttype == "date":
            rd = trig.get("run_date", "")
            when = rd.replace("T", " ")[:16]
        out.append({
            "time": when,
            "name": s.get("name", "Task"),
            "desc": s.get("description", ""),
            "_sort": sort_key,
        })
    out.sort(key=lambda x: x["_sort"])
    for x in out:
        x.pop("_sort", None)
    return out


def _journal_section() -> dict:
    """Journal streak + latest entry snippet from data/journal/*.md."""
    jdir = _DATA / "journal"
    try:
        files = sorted(
            p for p in jdir.glob("*.md")
            if not p.name.startswith("export_")
        )
    except Exception:
        files = []
    latest_date, snippet = "", ""
    if files:
        latest = files[-1]
        latest_date = latest.stem
        try:
            text = latest.read_text(encoding="utf-8").strip()
            snippet = " ".join(text.split())[:180]
        except Exception:
            snippet = ""
    return {"streak": len(files), "latest_date": latest_date, "snippet": snippet}


def _reminders_section() -> list:
    """Pending local reminders (El Fager's own token-free to-do store).
    Reads data/reminders.json directly — fast, always available."""
    items = _read_json(_DATA / "reminders.json", [])
    out = []
    now = datetime.now()
    for r in items:
        if r.get("fired"):
            continue
        try:
            fire = datetime.fromisoformat(r["fire_at"])
        except Exception:
            continue
        mins = int((fire - now).total_seconds() / 60)
        if mins < 0:
            when = "overdue"
        elif mins < 90:
            when = f"in {mins}m"
        elif fire.date() == now.date():
            when = fire.strftime("%H:%M")
        else:
            when = fire.strftime("%b %d %H:%M")
        out.append({"message": str(r.get("message", ""))[:80], "when": when, "_t": fire})
    out.sort(key=lambda x: x["_t"])
    for x in out:
        x.pop("_t", None)
    return out


def _memory_section() -> dict:
    """What El Fager knows — fact count + a few recent facts."""
    data = _read_json(_DATA / "facts.json", {})
    facts = data.get("facts", []) or []
    recent = [f.get("content", "") for f in facts[-6:] if f.get("content")]
    recent.reverse()
    return {"count": len(facts), "recent": recent}


# ── Live integrations (Calendar / Email / News) ───────────────────────────────
# Fetched by a background daemon thread into an in-memory cache so the page
# paints instantly from on-disk data and live cards fill within seconds — the
# snapshot request never blocks on a network call.

_LIVE: dict = {"weather": {}, "calendar": [], "email": [], "news": [], "updated": None}
_LIVE_LOCK = threading.Lock()
_LIVE_REFRESH_SEC = 300
_LIVE_IDLE_TIMEOUT_SEC = 900  # skip network fetches once nobody's viewed the dashboard this long
_last_viewed = {"ts": 0.0}
_refresher_started = False


def _with_timeout(fn, timeout=12):
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        try:
            return ex.submit(fn).result(timeout=timeout)
        except Exception:
            return None


def _fetch_calendar() -> list:
    from tools.calendar_tool import (
        get_calendar_service, _parse_time_range, _format_event, CALENDAR_AVAILABLE,
    )
    if not CALENDAR_AVAILABLE:
        return []
    svc = get_calendar_service()
    if svc is None:
        return []
    tmin, tmax, _ = _parse_time_range("today")
    res = svc.events().list(
        calendarId="primary", timeMin=tmin, timeMax=tmax,
        maxResults=8, singleEvents=True, orderBy="startTime",
    ).execute()
    return [_format_event(ev).lstrip("- ").strip() for ev in res.get("items", [])]


def _fetch_email() -> list:
    from tools.gmail_tool import get_gmail_service, _get_header, GMAIL_AVAILABLE
    if not GMAIL_AVAILABLE:
        return []
    svc = get_gmail_service()
    if svc is None:
        return []
    res = svc.users().messages().list(
        userId="me", q="is:unread", maxResults=5).execute()
    out = []
    for ref in res.get("messages", []):
        meta = svc.users().messages().get(
            userId="me", id=ref["id"], format="metadata",
            metadataHeaders=["Subject", "From"]).execute()
        h = meta.get("payload", {}).get("headers", [])
        frm = _get_header(h, "From") or ""
        name = (frm.split("<")[0].strip().strip('"') or frm)[:28]
        subj = (_get_header(h, "Subject") or "(no subject)")[:70]
        out.append({"from": name, "subject": subj})
    return out


def _fetch_news() -> list:
    from tools.news_tool import get_news
    out = []
    for line in str(get_news("world", 4)).splitlines():
        line = line.strip()
        head, _, _ = line.partition(". ")
        if head.isdigit() and _:
            title = line.split(". ", 1)[1]
            if " [" in title:
                title = title.rsplit(" [", 1)[0]
            out.append(title[:90])
    return out


def _fetch_weather() -> dict:
    import httpx
    from tools.weather_tool import _get_coords, WMO
    coords = _get_coords("Cairo")
    if not coords:
        return {}
    lat, lon = coords
    r = httpx.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": "auto", "forecast_days": 1}, timeout=8)
    d = r.json()
    cur = d.get("current", {})
    daily = d.get("daily", {})
    return {
        "city": "Cairo",
        "temp": round(float(cur.get("temperature_2m", 0))),
        "condition": WMO.get(cur.get("weather_code", 0), ""),
        "hi": round(float(daily.get("temperature_2m_max", [0])[0])),
        "lo": round(float(daily.get("temperature_2m_min", [0])[0])),
    }


def _refresh_live_once() -> None:
    weather = _with_timeout(_fetch_weather) or {}
    cal = _with_timeout(_fetch_calendar) or []
    mail = _with_timeout(_fetch_email) or []
    news = _with_timeout(_fetch_news) or []
    with _LIVE_LOCK:
        _LIVE["weather"] = weather
        _LIVE["calendar"] = cal
        _LIVE["email"] = mail
        _LIVE["news"] = news
        _LIVE["updated"] = datetime.now().isoformat(timespec="seconds")


def start_live_refresher() -> None:
    """Daemon thread: refresh live cards every _LIVE_REFRESH_SEC while the
    dashboard is being viewed. Started lazily on first view (see mark_viewed)
    rather than at app startup, and skips network fetches after
    _LIVE_IDLE_TIMEOUT_SEC of no views — a 24/7 background process has no
    business waking the network every 5 minutes when nobody's looking."""
    import time

    def _loop():
        while True:
            if time.time() - _last_viewed["ts"] <= _LIVE_IDLE_TIMEOUT_SEC:
                try:
                    _refresh_live_once()
                except Exception:
                    pass
            time.sleep(_LIVE_REFRESH_SEC)

    threading.Thread(target=_loop, daemon=True, name="DashboardLive").start()


def mark_viewed() -> None:
    """Record a dashboard view; lazily starts the live refresher on first call."""
    import time
    global _refresher_started
    _last_viewed["ts"] = time.time()
    if not _refresher_started:
        _refresher_started = True
        start_live_refresher()


def build_snapshot() -> dict:
    """Assemble the full read-only status snapshot from on-disk state.
    Every section is individually fault-tolerant."""
    snap: dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": _STARTED_AT.isoformat(timespec="seconds"),
    }

    for key, fn in (
        ("profile", _profile_section),
        ("nutrition", _nutrition_section),
        ("schedule", _schedule_section),
        ("reminders", _reminders_section),
        ("journal", _journal_section),
        ("memory", _memory_section),
    ):
        try:
            snap[key] = fn()
        except Exception:
            snap[key] = {} if key not in ("schedule", "reminders") else []

    try:
        from core.missions import MissionManager
        mgr = MissionManager()
        active = mgr.get_active()
        snap["mission"] = {
            "summary": mgr.format_status(),
            "active": bool(active),
            "steps": [
                {"n": s["n"], "description": s["description"], "status": s["status"]}
                for s in (active["steps"] if active else [])
            ],
        }
    except Exception:
        snap["mission"] = {"summary": "unavailable", "active": False, "steps": []}

    try:
        from core.telemetry import summarize
        today = summarize(days=1)
        week = summarize(days=7)
        snap["cost"] = {
            "today_usd": today["cost_usd"],
            "today_requests": today["requests"],
            "week_usd": week["cost_usd"],
            "avg_latency_ms": today["avg_latency_ms"],
        }
    except Exception:
        snap["cost"] = {"today_usd": 0, "today_requests": 0, "week_usd": 0,
                        "avg_latency_ms": 0}

    try:
        from core.skills.store import SkillStore
        skills = SkillStore().list_all()
        snap["skills"] = {
            "count": len(skills),
            "scheduled": sum(1 for s in skills if s.get("scheduled_task_id")),
            "names": [s["name"] for s in skills][:20],
        }
    except Exception:
        snap["skills"] = {"count": 0, "scheduled": 0, "names": []}

    try:
        from core.autonomous_tasks import AutonomousTaskManager
        tasks = AutonomousTaskManager().list_all()
        snap["tasks"] = {
            "pending": sum(1 for t in tasks if t["status"] == "pending"),
            "recent": [
                {"description": t["description"][:80], "status": t["status"]}
                for t in tasks[-8:]
            ],
        }
    except Exception:
        snap["tasks"] = {"pending": 0, "recent": []}

    with _LIVE_LOCK:
        snap["live"] = dict(_LIVE)

    return snap


_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>El Fager</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<meta name="theme-color" content="#07080C">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
__FONTS__
__TOKENS__
 *{box-sizing:border-box}
 html,body{margin:0}
 body{background:radial-gradient(1200px 700px at 80% -10%,var(--accent-wash),var(--bg-void) 60%),var(--bg-void);
   color:var(--text-hi);font-family:var(--font-ui);
   -webkit-font-smoothing:antialiased;padding:22px 18px 40px;max-width:1120px;margin:0 auto}
 .top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:2px}
 .brand{font-size:13px;font-weight:600;letter-spacing:4px;color:var(--accent-ember)}
 .brand span{color:var(--text-low)}
 .clock{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:13px;color:var(--text-mid);letter-spacing:1px}
 .hero{margin:14px 0 22px}
 .greet{font-size:28px;font-weight:600;letter-spacing:-.3px}
 .greet b{color:var(--accent-ember);font-weight:600}
 .date{color:var(--text-mid);font-size:13px;margin-top:2px}
 .pulse{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--sem-ok);
   margin-right:7px;box-shadow:0 0 8px var(--sem-ok);vertical-align:middle;animation:p 2.4s infinite}
 @keyframes p{0%,100%{opacity:1}50%{opacity:.35}}

 .rings{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
 .ring{flex:1;min-width:130px;background:var(--surface-1);border:1px solid var(--stroke-hairline);
   border-radius:var(--r-3);padding:16px 12px 14px;text-align:center;position:relative}
 .ring svg{display:block;margin:0 auto 6px}
 .ring .rv{position:absolute;top:52px;left:0;right:0;font-family:var(--font-mono);font-size:20px;font-weight:500;
   font-variant-numeric:tabular-nums}
 .ring .ru{position:absolute;top:76px;left:0;right:0;font-family:var(--font-mono);font-size:10px;color:var(--text-mid)}
 .ring .rl{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--text-mid);margin-top:2px}
 .ring .rt{font-family:var(--font-mono);font-size:11px;color:var(--text-low);margin-top:1px}

 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:var(--s-3)}
 .card{background:var(--surface-1);border:1px solid var(--stroke-hairline);border-radius:var(--r-3);padding:15px 16px}
 .card h2{font-family:var(--font-mono);font-size:11px;letter-spacing:2.5px;text-transform:uppercase;color:var(--text-mid);
   margin:0 0 12px;display:flex;align-items:center;gap:8px}
 .card h2 .dot{width:6px;height:6px;border-radius:50%;background:var(--accent-ember)}
 .stat{font-size:30px;font-weight:600;letter-spacing:-.5px;line-height:1}
 .stat small{font-size:13px;color:var(--text-mid);font-weight:400;letter-spacing:0}
 .sub{color:var(--text-mid);font-size:12px;margin-top:6px}
 .row{font-size:13px;padding:5px 0;border-top:1px solid var(--stroke-hairline);display:flex;
   gap:9px;align-items:flex-start}
 .row:first-of-type{border-top:0}
 .row .t{font-family:var(--font-mono);color:var(--accent-ember);font-variant-numeric:tabular-nums;flex:0 0 auto;min-width:64px;font-size:12px}
 .row .nm{flex:1}
 .row .nm small{display:block;color:var(--text-mid);font-size:11px;margin-top:1px}
 .tag{font-size:10px;padding:1px 7px;border-radius:var(--r-pill);border:1px solid var(--stroke-hairline);color:var(--text-mid)}
 .done{color:var(--sem-ok)}.pending{color:var(--sem-warn)}.failed{color:var(--sem-bad)}
 .fact{font-size:12.5px;color:var(--text-mid);padding:5px 0;border-top:1px solid var(--stroke-hairline);line-height:1.4}
 .fact:first-of-type{border-top:0}
 .empty{color:var(--text-low);font-size:12.5px}

 .cmd{display:flex;gap:var(--s-2);margin:22px 0 4px}
 .cmd input{flex:1;background:var(--surface-0);border:1px solid var(--stroke-hairline);color:var(--text-hi);
   border-radius:var(--r-2);padding:12px 14px;font-family:inherit;font-size:14px;outline:none;transition:border-color var(--t-instant) var(--ease-swift)}
 .cmd input:focus{border-color:var(--accent-ember)}
 .cmd button{background:var(--accent-ember);border:1px solid var(--accent-ember);
   color:var(--text-on-ember);border-radius:var(--r-2);padding:0 20px;font-family:inherit;
   font-size:13px;font-weight:500;letter-spacing:1px;cursor:pointer}
 .cmd button:active{background:var(--accent-press)}
 .cmdmsg{font-family:var(--font-mono);color:var(--text-mid);font-size:12px;min-height:16px;padding-left:4px}
 .compose{background:var(--surface-1);border:1px solid var(--stroke-hairline);border-radius:var(--r-3);
   padding:12px 14px;margin:10px 0 4px;display:flex;flex-direction:column;gap:var(--s-2)}
 .crow{display:flex;gap:var(--s-2);align-items:center;flex-wrap:wrap}
 .compose input,.compose textarea{background:var(--surface-0);border:1px solid var(--stroke-hairline);
   color:var(--text-hi);border-radius:var(--r-2);padding:9px 11px;font-family:inherit;font-size:13px;
   outline:none;width:100%;resize:vertical}
 .compose input:focus,.compose textarea:focus{border-color:var(--accent-ember)}
 .seg{background:var(--surface-0);border:1px solid var(--stroke-hairline);color:var(--text-mid);border-radius:var(--r-pill);
   padding:6px 14px;font-family:inherit;font-size:12px;cursor:pointer}
 .seg.on{color:var(--accent-ember);border-color:var(--accent-ember);background:var(--accent-wash)}
/* Semantic colours are dots, text and hairlines — never fills. The one
    button that sends carries the accent, like every other commit action. */
 .seg.go{color:var(--text-on-ember);background:var(--accent-ember);
   border-color:var(--accent-ember);font-weight:500}
 .chint{color:var(--text-low);font-size:11px}
 .cmsg{font-size:12px;color:var(--text-mid)}
 .cprev{background:var(--surface-0);border:1px solid var(--stroke-hairline);border-radius:var(--r-2);padding:10px;
   color:var(--text-mid);font-size:12px;white-space:pre-wrap;margin:0;font-family:inherit}
 .foot{font-family:var(--font-mono);color:var(--text-low);font-size:11px;margin-top:18px;text-align:center;
   font-variant-numeric:tabular-nums}
 .span2{grid-column:span 2}
 @media(max-width:620px){.span2{grid-column:auto}.greet{font-size:22px}}
</style></head><body>
<div class="top">
 <div class="brand">EL FAGER <span>// OS</span></div>
 <div class="clock" id="clock">--:--:--</div>
</div>
<div class="hero">
 <div class="greet" id="greet">Loading…</div>
 <div class="date"><span class="pulse"></span><span id="date">connecting</span></div>
</div>
<div class="rings" id="rings"></div>
<div class="cmd">
 <input id="cmd" placeholder="Tell El Fager…  e.g. “email Ali the notes”, “remind me to call dad at 6”, “what's due today”" maxlength="500" autocomplete="off">
 <button onclick="sendCmd()">SEND</button>
</div>
<div class="cmdmsg" id="cmdmsg"></div>
<div class="compose">
 <div class="crow">
  <button id="cbEmail" class="seg on" onclick="setChan('email')">✉ Email</button>
  <button id="cbWa" class="seg" onclick="setChan('whatsapp')">💬 WhatsApp</button>
  <span class="chint" id="chint">send from here — you preview, then confirm</span>
 </div>
 <input id="cTo" placeholder="To (email address)" autocomplete="off">
 <input id="cSubj" placeholder="Subject">
 <textarea id="cBody" rows="2" placeholder="Message…"></textarea>
 <div class="crow">
  <button class="seg" onclick="doPreview()">Preview</button>
  <button id="cConfirm" class="seg go" style="display:none" onclick="doConfirm()">✓ Confirm send</button>
  <button id="cCancel" class="seg" style="display:none" onclick="resetCompose()">Cancel</button>
  <span class="cmsg" id="cmsg"></span>
 </div>
 <pre class="cprev" id="cprev" style="display:none"></pre>
</div>
<div class="grid" id="grid"></div>
<div class="foot" id="foot"></div>
<script>
const $=id=>document.getElementById(id);
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

function tick(){const d=new Date();
 $('clock').textContent=d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});}
setInterval(tick,1000);tick();

function token(){
 let t=localStorage.getItem('elf_token');
 if(!t){t=prompt('Dashboard token (data/settings.json → dashboard_token):');
   if(t)localStorage.setItem('elf_token',t);}
 return t;}
async function sendCmd(){
 const el=$('cmd'),text=el.value.trim();if(!text)return;
 const msg=$('cmdmsg');msg.textContent='sending…';
 try{
  const r=await fetch('/api/command',{method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+token()},
    body:JSON.stringify({text})});
  if(r.status===401){localStorage.removeItem('elf_token');msg.textContent='✗ bad token — try again';return;}
  const j=await r.json();
  if(j.queued){msg.textContent='✓ queued — El Fager will act within a minute';el.value='';}
  else msg.textContent='✗ '+(j.error||'unknown error');
 }catch(e){msg.textContent='✗ send failed: '+e;}
}
$('cmd').addEventListener('keydown',e=>{if(e.key==='Enter')sendCmd();});

// ── Compose: preview → confirm send (email / whatsapp) ─────────────────────
let chan='email';
function setChan(c){chan=c;
 $('cbEmail').classList.toggle('on',c==='email');
 $('cbWa').classList.toggle('on',c==='whatsapp');
 $('cSubj').style.display=(c==='email')?'block':'none';
 $('cTo').placeholder=(c==='email')?'To (email address)':'To (WhatsApp number or saved contact)';
 resetCompose();}
function resetCompose(){
 $('cprev').style.display='none';$('cConfirm').style.display='none';$('cCancel').style.display='none';
 $('cmsg').textContent='';}
async function doPreview(){
 const to=$('cTo').value.trim(),body=$('cBody').value.trim();
 const subject=$('cSubj').value.trim();
 if(!to||!body){$('cmsg').textContent='✗ recipient and message required';return;}
 $('cmsg').textContent='staging…';
 try{
  const r=await fetch('/api/send_preview',{method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+token()},
    body:JSON.stringify({channel:chan,to,subject,body})});
  if(r.status===401){localStorage.removeItem('elf_token');$('cmsg').textContent='✗ bad token';return;}
  const j=await r.json();
  if(!j.ok){$('cmsg').textContent='✗ '+(j.error||'failed');return;}
  $('cprev').textContent=j.preview;$('cprev').style.display='block';
  $('cConfirm').style.display='inline-block';$('cCancel').style.display='inline-block';
  $('cmsg').textContent='review below, then confirm';
 }catch(e){$('cmsg').textContent='✗ '+e;}
}
async function doConfirm(){
 $('cmsg').textContent='sending…';
 try{
  const r=await fetch('/api/send_confirm',{method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+token()},
    body:JSON.stringify({channel:chan})});
  const j=await r.json();
  $('cmsg').textContent=(j.ok?'✓ ':'✗ ')+(j.result||j.error||'');
  if(j.ok){$('cTo').value='';$('cSubj').value='';$('cBody').value='';
    $('cprev').style.display='none';$('cConfirm').style.display='none';$('cCancel').style.display='none';}
 }catch(e){$('cmsg').textContent='✗ '+e;}
}
setChan('email');

// ── Whoop-style ring ──────────────────────────────────────────────────────
function ringColor(p){return p>=95?'var(--sem-ok)':p>=60?'var(--accent-ember)':p>=30?'var(--sem-warn)':'var(--sem-bad)';}
function ringSVG(val,tgt){
 const R=34,C=2*Math.PI*R;
 const p=tgt>0?Math.min(val/tgt,1):0;const off=C*(1-p);const col=tgt>0?ringColor(p*100):'var(--text-low)';
 return `<svg width="84" height="84" viewBox="0 0 84 84">
  <circle cx="42" cy="42" r="${R}" fill="none" stroke="var(--stroke-hairline)" stroke-width="7"/>
  <circle cx="42" cy="42" r="${R}" fill="none" stroke="${col}" stroke-width="7"
   stroke-linecap="round" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"
   transform="rotate(-90 42 42)"/></svg>`;
}
function ringCard(label,o){
 const pct=o.target>0?Math.round(Math.min(o.value/o.target,1)*100):0;
 return `<div class="ring">${ringSVG(o.value,o.target)}
  <div class="rv">${o.value}</div><div class="ru">/ ${o.target||'—'}</div>
  <div class="rl">${label}</div><div class="rt">${o.target>0?pct+'%':'no target'}</div></div>`;
}

function card(title,color,inner){
 return `<div class="card"><h2><span class="dot" style="background:${color}"></span>${title}</h2>${inner}</div>`;
}
function stepClass(s){return s==='done'?'done':s==='failed'?'failed':'pending';}

async function load(){
 let s;
 try{const r=await fetch('/api/status');s=await r.json();}
 catch(e){$('date').textContent='offline — '+e;return;}

 // hero
 const p=s.profile||{};
 const w=(s.live||{}).weather||{};
 const wtxt=(w.temp!=null&&w.temp!==undefined&&Object.keys(w).length)?'  ·  '+w.temp+'° '+esc(w.condition||''):'';
 $('greet').innerHTML=esc(p.greeting||'Hello')+', <b>'+esc(p.name||'Mo')+'</b>';
 $('date').textContent=(p.date_str||'')+(p.location?'  ·  '+p.location:'')+wtxt;

 // rings
 const n=s.nutrition||{};
 $('rings').innerHTML=
   ringCard('Calories',n.kcal||{value:0,target:0})+
   ringCard('Protein',n.protein||{value:0,target:0})+
   ringCard('Carbs',n.carbs||{value:0,target:0})+
   ringCard('Fat',n.fat||{value:0,target:0});

 let g='';
 const live=s.live||{};

 // calendar today (live)
 const cal=live.calendar||[];
 g+=card('Calendar','var(--accent-ember)',
   cal.length?cal.map(x=>`<div class="row"><span class="nm">${esc(x)}</span></div>`).join('')
     :'<div class="empty">No events today.</div>');

 // inbox (live unread)
 const mail=live.email||[];
 g+=card('Inbox','var(--sem-warn)',
   `<div class="stat">${mail.length}${mail.length>=5?'+':''} <small>unread</small></div>`+
   mail.map(m=>`<div class="row"><span class="nm">${esc(m.subject)}<small>${esc(m.from)}</small></span></div>`).join(''));

 // news (live)
 const news=live.news||[];
 g+=card('News','var(--state-thinking)',
   news.length?news.map(x=>`<div class="row"><span class="nm">${esc(x)}</span></div>`).join('')
     :'<div class="empty">Fetching headlines…</div>');

 // system
 const c=s.cost||{};
 g+=card('System','var(--accent-ember)',
   `<div class="stat">$${(c.today_usd||0).toFixed(2)} <small>today</small></div>
    <div class="sub">${c.today_requests||0} calls · $${(c.week_usd||0).toFixed(2)} this week ·
    avg ${((c.avg_latency_ms||0)/1000).toFixed(1)}s latency</div>`);

 // today's routine
 const sch=s.schedule||[];
 let sr=sch.length?sch.map(x=>`<div class="row"><span class="t">${esc(x.time||'')}</span>
   <span class="nm">${esc(x.name)}<small>${esc(x.desc)}</small></span></div>`).join('')
   :'<div class="empty">No scheduled routines.</div>';
 g+=card('Today',"var(--state-thinking)",sr);

 // nutrition detail
 g+=card('Nutrition','var(--sem-ok)',
   (n.logged?`<div class="stat">${n.meals} <small>items logged today</small></div>`
     :'<div class="empty">Nothing logged today yet. Say “log a meal”.</div>'));

 // mission
 const m=s.mission||{steps:[]};
 let mr='<div class="sub">'+esc(m.summary||'No active mission.')+'</div>';
 for(const st of (m.steps||[]))
   mr+=`<div class="row"><span class="t ${stepClass(st.status)}">${esc(st.status)}</span>
     <span class="nm">${st.n}. ${esc(st.description)}</span></div>`;
 g+=card('Mission','var(--sem-warn)',mr);

 // reminders (local, token-free to-do)
 const rem=s.reminders||[];
 g+=card('Reminders','var(--sem-ok)',
   rem.length?rem.map(r=>`<div class="row"><span class="t">${esc(r.when)}</span>
     <span class="nm">${esc(r.message)}</span></div>`).join('')
     :'<div class="empty">No reminders. Say “remind me to…”.</div>');

 // background tasks
 const t=s.tasks||{recent:[]};
 let tr=`<div class="stat">${t.pending||0} <small>pending</small></div>`;
 for(const x of (t.recent||[]))
   tr+=`<div class="row"><span class="t ${stepClass(x.status)}">${esc(x.status)}</span>
     <span class="nm">${esc(x.description)}</span></div>`;
 g+=card('Tasks','var(--accent-ember)',tr);

 // skills
 const sk=s.skills||{names:[]};
 g+=card('Skills','var(--state-thinking)',
   `<div class="stat">${sk.count||0} <small>learned · ${sk.scheduled||0} scheduled</small></div>
    <div class="sub">${esc((sk.names||[]).join(' · '))||'—'}</div>`);

 // journal
 const j=s.journal||{};
 g+=card('Journal','var(--sem-ok)',
   `<div class="stat">${j.streak||0} <small>entries</small></div>`+
   (j.latest_date?`<div class="row"><span class="t">${esc(j.latest_date)}</span>
     <span class="nm">${esc(j.snippet)||'—'}</span></div>`
     :'<div class="empty">No journal entries yet.</div>'));

 // memory
 const mem=s.memory||{recent:[]};
 let memr=`<div class="stat">${mem.count||0} <small>things I know about you</small></div>`;
 for(const f of (mem.recent||[]))memr+=`<div class="fact">• ${esc(f)}</div>`;
 g+=card('Memory','var(--accent-ember)',memr);

 $('grid').innerHTML=g;
 $('foot').textContent='snapshot '+(s.generated_at||'')+'  ·  up since '+(s.started_at||'');
}
load();setInterval(load,30000);
</script></body></html>"""


_PAGE = _PAGE.replace("__TOKENS__", _token_css()).replace("__FONTS__", _font_css())

_MAX_COMMAND_CHARS = 500


def _expected_token() -> str:
    return str(_read_json(_SETTINGS_PATH, {}).get("dashboard_token", "") or "")


def queue_command(text: str) -> dict:
    """Queue a phone command as an autonomous task (executed by the
    ProactiveEngine via brain.chat within ~60s — all normal gates apply)."""
    from core.autonomous_tasks import AutonomousTaskManager
    task = AutonomousTaskManager().add(description=text.strip())
    return {"queued": True, "task_id": task["id"]}


def stage_send(payload: dict) -> dict:
    """Stage an email or WhatsApp message and return its preview WITHOUT
    sending. The dashboard runs in-process with the brain, so this reaches the
    same staging tools the voice pipeline uses; nothing goes out until
    confirm_send() is called."""
    channel = str(payload.get("channel", "")).strip()
    to = str(payload.get("to", "")).strip()
    body = str(payload.get("body", "")).strip()
    if not to or not body:
        return {"ok": False, "error": "recipient and message required"}
    if channel == "email":
        from tools.gmail_tool import send_message
        subject = str(payload.get("subject", "")).strip() or "(no subject)"
        return {"ok": True, "preview": send_message(to, subject, body)}
    if channel == "whatsapp":
        from tools.whatsapp_tool import send_to_number, prepare_whatsapp_message
        digits = to.replace("+", "").replace(" ", "").replace("-", "")
        if digits.isdigit():
            return {"ok": True, "preview": send_to_number(to, body)}
        return {"ok": True, "preview": prepare_whatsapp_message(to, body)}
    return {"ok": False, "error": "unknown channel"}


def confirm_send(payload: dict) -> dict:
    """Send the previously staged message (the human clicked Confirm)."""
    channel = str(payload.get("channel", "")).strip()
    if channel == "email":
        from tools.gmail_tool import confirm_send_message
        return {"ok": True, "result": confirm_send_message()}
    if channel == "whatsapp":
        from tools.whatsapp_tool import confirm_whatsapp_send
        return {"ok": True, "result": confirm_whatsapp_send()}
    return {"ok": False, "error": "unknown channel"}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            mark_viewed()
            body = json.dumps(build_snapshot()).encode("utf-8")
            self._send(200, "application/json", body)
        elif self.path in ("/", "/index.html"):
            mark_viewed()
            self._send(200, "text/html; charset=utf-8", _PAGE.encode("utf-8"))
        elif self.path == "/jobs":
            from core.career.review_page import PAGE
            page = PAGE.replace("__TOKENS__", _token_css()).replace("__FONTS__", _font_css())
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
        elif self.path == "/api/jobs":
            # The drafts are Mo's applications: readable only with the token.
            if not self._authorized():
                self._send(401, "application/json", b'{"error": "missing or invalid token"}')
                return
            from core.career.review_page import batch_json
            self._json(200, batch_json())
        elif self.path == "/manifest.webmanifest":
            self._send(200, "application/manifest+json", _MANIFEST.encode("utf-8"))
        elif self.path == "/icon.svg":
            self._send(200, "image/svg+xml", _ICON.encode("utf-8"))
        elif self.path.startswith("/fonts/"):
            self._send_font(self.path[len("/fonts/"):])
        else:
            self._send(404, "text/plain", b"not found")

    def _send_font(self, name: str):
        """The bundled OFL faces. Only the three we ship, by exact name —
        this is the one route that reads a file off disk."""
        if name not in _FONTS:
            self._send(404, "text/plain", b"not found")
            return
        try:
            body = (_FONT_DIR / name).read_bytes()
        except Exception:
            self._send(404, "text/plain", b"not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "font/ttf")
        self.send_header("Content-Length", str(len(body)))
        # Fonts are the one thing here that never changes between requests.
        self.send_header("Cache-Control", "public, max-age=604800, immutable")
        self.end_headers()
        self.wfile.write(body)

    _POST_ROUTES = ("/api/command", "/api/send_preview", "/api/send_confirm",
                    "/api/jobs_approve", "/api/referral_mark", "/api/jobs_answer")

    def _authorized(self) -> bool:
        expected = _expected_token()
        return bool(expected) and self.headers.get("Authorization", "") == f"Bearer {expected}"

    def do_POST(self):
        # Drain the body before replying to anything. A 404 or a 401 that
        # leaves the request body unread desyncs the connection, and the
        # client sees a reset instead of the status — which is what made the
        # rejection test fail intermittently.
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""

        if self.path not in self._POST_ROUTES:
            self._send(404, "text/plain", b"not found")
            return
        if not self._authorized():
            self._send(401, "application/json",
                       b'{"error": "missing or invalid token"}')
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, "application/json", b'{"error": "bad json"}')
            return

        if self.path in ("/api/send_preview", "/api/send_confirm"):
            fn = stage_send if self.path == "/api/send_preview" else confirm_send
            try:
                self._json(200, fn(payload))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)[:120]})
            return

        if self.path == "/api/referral_mark":
            from core.career import referrals
            result = referrals.mark(str(payload.get("id", "")), str(payload.get("status", "")))
            self._json(200, {"ok": not result.startswith("Error"), "result": result})
            return

        if self.path == "/api/jobs_answer":
            from tools import career_tool
            question, answer = str(payload.get("question", "")), str(payload.get("answer", ""))
            result = career_tool.set_application_answer(question, answer)
            self._json(200, {"ok": not result.startswith("Error"), "result": result})
            return

        if self.path == "/api/jobs_approve":
            from core.career import pipeline
            # Only what Mo ticked is sent; the rest stays saved on the page.
            only = [str(i) for i in payload.get("only", [])]
            if not only:
                self._json(400, {"ok": False, "error": "Tick at least one application."})
                return
            try:
                self._json(200, {"ok": True, "result": pipeline.approve(only=only)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)[:120]})
            return

        # /api/command
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > _MAX_COMMAND_CHARS:
            self._send(400, "application/json",
                       b'{"error": "text required, max 500 chars"}')
            return
        try:
            result = queue_command(text)
            self._send(200, "application/json",
                       json.dumps(result).encode("utf-8"))
        except Exception:
            self._send(500, "application/json", b'{"error": "queue failed"}')

    def _json(self, code: int, obj: dict):
        self._send(code, "application/json",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep the console quiet


def _ensure_token(settings: dict) -> dict:
    """Generate dashboard_token on first run so the command channel works
    out of the box. The token stays in data/settings.json (gitignored)."""
    if not settings.get("dashboard_token"):
        import secrets
        settings["dashboard_token"] = secrets.token_urlsafe(24)
        try:
            _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SETTINGS_PATH.write_text(
                json.dumps(settings, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass
    return settings


def start_dashboard() -> ThreadingHTTPServer | None:
    """Start the dashboard in a daemon thread. Returns the server or None."""
    settings = _read_json(_SETTINGS_PATH, {})
    if not settings.get("dashboard_enabled", True):
        return None
    settings = _ensure_token(settings)
    # Default localhost-only: the command channel executes through the brain
    # (including run_powershell), so LAN exposure must be an explicit opt-in —
    # set dashboard_host to "0.0.0.0" in data/settings.json for phone access.
    host = settings.get("dashboard_host", "127.0.0.1")
    port = int(settings.get("dashboard_port", 8765))
    try:
        server = ThreadingHTTPServer((host, port), _Handler)
    except OSError as e:
        print(f"[Dashboard] could not bind {host}:{port}: {e}")
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="Dashboard")
    thread.start()
    print(f"[Dashboard] serving on http://{host}:{port}")
    return server
