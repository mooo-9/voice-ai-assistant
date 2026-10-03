"""JSON files under data/career/, and the pipeline's settings."""
import json
import threading
from pathlib import Path

DIR = Path(__file__).parent.parent.parent / "data" / "career"

_lock = threading.RLock()

# Search terms cover Mo's targets: internships and entry-level AI engineering
# and AI, data analyst, business analyst and SAP/ERP roles, plus graduate
# programmes; the Big 4's data & analytics line comes in through them.
DEFAULTS = {
    # Practice mode until Mo's CV is final: everything is found, scored and
    # drafted, nothing is sent.
    "live": False,
    # Enough to reach the good fits each day and still read every letter.
    "daily_target": 20,
    "min_score": 60,
    # LinkedIn restricts accounts that apply at machine pace.
    "linkedin_daily_cap": 20,
    # People at target companies found and drafted a note for, per night.
    "referrals_per_day": 5,
    "search_terms": [
        "data analyst", "business analyst", "business intelligence", "power bi",
        "data analytics", "data science", "machine learning", "artificial intelligence",
        "AI engineer", "data analyst intern", "business analyst intern",
        "junior data analyst", "junior business analyst", "fresh graduate data analyst",
        "graduate program", "erp", "erp consultant", "sap", "odoo",
        "machine learning engineer", "generative ai", "sap consultant", "junior sap",
        "erp fresh graduate",
    ],
}


def load(name: str, default):
    with _lock:
        try:
            return json.loads((DIR / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default


def save(name: str, obj) -> None:
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = DIR / (name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(DIR / name)


def settings() -> dict:
    return {**DEFAULTS, **load("settings.json", {})}


def update_settings(**changes) -> dict:
    with _lock:
        saved = load("settings.json", {})
        saved.update({k: v for k, v in changes.items() if k in DEFAULTS})
        save("settings.json", saved)
    return settings()
