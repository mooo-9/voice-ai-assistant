"""
WhatsApp tool — Phase 4A.

Reaches any chat saved in Mo's WhatsApp by name, through WhatsApp Desktop
(tools/whatsapp_desktop.py). Raw numbers, and names WhatsApp doesn't know, go
via the whatsapp:// URI scheme (opens WhatsApp Desktop).
No third-party account required — uses Mo's personal WhatsApp.

Contacts are stored in data/contacts.json: {"Name": "+201XXXXXXXXX"}
Mo adds contacts by voice: "add contact Ahmed plus 201..."

Send flow mirrors Gmail:
  1. prepare_whatsapp_message() / send_to_number()  → stages message, returns preview
  2. confirm_whatsapp_send()                         → opens WhatsApp Desktop + presses Enter
"""

import ctypes
import json
import os
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CONTACTS_PATH = Path("data/contacts.json")
CAIRO_TZ = ZoneInfo("Africa/Cairo")

_pending: dict = {}  # staged message waiting for confirmation


# ── Phone normalisation ───────────────────────────────────────────────────────

def _normalise_phone(phone: str) -> str:
    """
    Return phone in E.164 format (+COUNTRYCODE...).
    Handles:
      +201..., 201..., 0201..., 01... (Egyptian local), 0044.../+44... (international)
    """
    stripped = phone.strip()
    digits = "".join(c for c in stripped if c.isdigit())

    # Strip leading 00 international exit code
    if digits.startswith("00"):
        digits = digits[2:]

    # Explicit + prefix → digits already carry the country code
    if stripped.startswith("+"):
        return "+" + digits

    # Egyptian local mobile: 01X + 8 digits = 11 digits total
    if digits.startswith("01") and len(digits) == 11:
        return "+20" + digits[1:]

    # Leading 0 that's NOT an Egyptian local number (e.g. 0201..., 044...) → strip it
    if digits.startswith("0"):
        digits = digits[1:]

    return "+" + digits


# ── Contacts ──────────────────────────────────────────────────────────────────

def _load_contacts() -> dict:
    try:
        if CONTACTS_PATH.exists():
            return json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))
        return {}
    except Exception:
        return {}


def _save_contacts(contacts: dict) -> None:
    CONTACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTACTS_PATH.write_text(
        json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_contact(name: str, phone_number: str) -> str:
    """Store (or update) a WhatsApp contact."""
    try:
        contacts = _load_contacts()
        normalised = _normalise_phone(phone_number)
        contacts[name.strip()] = normalised
        _save_contacts(contacts)
        return f"Saved {name} as {normalised}"
    except Exception as e:
        return f"[WhatsApp error: {e}]"


def delete_contact(name: str) -> str:
    """Remove a WhatsApp contact by name (partial match)."""
    try:
        contacts = _load_contacts()
        name_lower = name.lower()
        keys_to_delete = [k for k in contacts if name_lower in k.lower()]
        if not keys_to_delete:
            return f"No contact found matching '{name}'."
        for k in keys_to_delete:
            del contacts[k]
        _save_contacts(contacts)
        deleted = ", ".join(keys_to_delete)
        return f"Deleted contact(s): {deleted}"
    except Exception as e:
        return f"[WhatsApp error: {e}]"


def list_contacts() -> str:
    """List all saved WhatsApp contacts."""
    contacts = _load_contacts()
    if not contacts:
        return "No WhatsApp contacts saved yet. Say 'add WhatsApp contact [name] [number]'."
    lines = [f"{i+1}. {name}: {phone}" for i, (name, phone) in enumerate(contacts.items())]
    return "WhatsApp contacts:\n" + "\n".join(lines)


def _find_contact(name: str) -> tuple[str, str] | tuple[None, None]:
    """Case-insensitive substring match. Returns (display_name, phone) or (None, None)."""
    contacts = _load_contacts()
    name_lower = name.lower()
    for stored_name, phone in contacts.items():
        if name_lower in stored_name.lower():
            return stored_name, phone
    return None, None


# Spellings WhatsApp's own search already treats as one letter: a name saved
# as "العائله" is found by "العائلة", and "امازون" finds "أمازون".
_ARABIC_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي"})
_TASHKEEL = re.compile("[\u064B-\u0652\u0640]")        # vowel marks, tatweel
_JOINED = ("وال", "ال", "و")                              # "and", "the", written onto the word


# "Doctor" is saved as "د" as often as it is spelled out.
_TITLES = {"دكتور": "د", "دكتوره": "د"}


def _name_words(text: str) -> list[str]:
    words = _TASHKEEL.sub("", text).translate(_ARABIC_FOLD).casefold().split()
    words = [w.rstrip(".") for w in words]
    return [_TITLES.get(w, w) for w in words if w]


def _bare(word: str) -> str:
    """The word without an Arabic "و" or "ال" joined to its front."""
    for lead in _JOINED:
        if word.startswith(lead) and len(word) - len(lead) >= 2:
            return word[len(lead):]
    return word


def _starts(word: str, prefix: str) -> bool:
    return word.startswith(prefix) or _bare(word).startswith(_bare(prefix))


def _pick_chat(query: str, titles: list[str]) -> tuple["str | None", list[str]]:
    """Which of WhatsApp's search results the spoken name means.

    Returns (chosen, the other name matches). A full-name match wins outright
    so "Seif" can reach the chat saved as just "Seif" beside "Seif Magdy";
    otherwise every spoken word has to start a word of the name, and only a
    single such chat is chosen. WhatsApp's search also matches numbers and
    profile text, so results that don't match by name are dropped.
    """
    q = _name_words(query)
    matches = [t for t in titles
               if all(any(_starts(w, p) for w in _name_words(t)) for p in q)]
    for t in matches:
        if [_bare(w) for w in _name_words(t)] == [_bare(p) for p in q]:
            return t, [m for m in matches if m != t]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def _learn_name(name: str) -> None:
    """Someone Mo messaged is a name Whisper should know."""
    try:
        from core import voice_learned
        voice_learned.contacted(name)
    except Exception:
        pass                  # learning a name never gets in the way of a send


# ── Send helpers ──────────────────────────────────────────────────────────────

def _stage_pending(display_name: str, phone: "str | None", message: str,
                   photo: "bytes | None" = None) -> str:
    """Stage a message and return the preview string. phone=None means the
    chat is reached by its name in WhatsApp Desktop."""
    from core import staging

    _pending.clear()
    _pending.update({
        "name": display_name,
        "phone": phone,
        "message": message,
        "expires_at": datetime.now(CAIRO_TZ) + timedelta(seconds=300),
    })
    # Announce it so the surfaces can show what is armed and confirm it.
    staging.stage(
        medium="whatsapp",
        target=display_name,
        body=message,
        expires_at=_pending["expires_at"],
        confirm=confirm_whatsapp_send,
        cancel=_pending.clear,
        photo=photo,
    )
    preview = message if len(message) <= 80 else message[:77] + "..."
    return (
        f"Ready to send to {display_name} ({phone or 'WhatsApp chat'}):\n"
        f"  \"{preview}\"\n"
        f"Say 'yes send it' to confirm."
    )


def _wait_for_whatsapp_focus(timeout: float = 8.0) -> bool:
    """
    Poll the foreground window every 300ms until WhatsApp is active or timeout.
    Uses Win32 API via ctypes — no extra dependencies.
    """
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.3)
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
            if "whatsapp" in buf.value.lower():
                return True
        except Exception:
            pass
    return False


# ── Send flow ─────────────────────────────────────────────────────────────────

def _search_chats(name: str) -> list:
    from tools import whatsapp_desktop
    try:
        return whatsapp_desktop.find_chats(name)
    except Exception as e:
        print(f"[El Fager] WhatsApp Desktop search failed: {e}")
        return []


def prepare_whatsapp_message(contact_name: str, message: str,
                             contact_name_arabic: "str | None" = None) -> str:
    """
    Stage a WhatsApp message for confirmation.
    Looks the name up among Mo's WhatsApp chats first, then data/contacts.json.
    Whisper writes every name in English letters, so a chat saved in Arabic is
    looked up by contact_name_arabic when the English spelling finds nothing.
    """
    # Arabic spellings come as alternatives — "كينجز | الملوك" — because a name
    # can be saved as it sounds or as it means; each is tried until one lands.
    spellings = [contact_name] + [
        a.strip() for a in (contact_name_arabic or "").split("|") if a.strip()]
    for query in spellings:
        chats = _search_chats(query)
        chosen, others = _pick_chat(query, [c.title for c in chats])
        if chosen or others:
            break
    if chosen:
        photo = next(c.photo for c in chats if c.title == chosen)
        # Near misses ("Seif Magdy" beside "Seif") aren't listed: any mention
        # of them had the model asking Mo to pick while the card already showed
        # the draft. The card's photo and full name are the check instead.
        return _stage_pending(chosen, None, message, photo=photo)
    if others:
        return (
            f"Several WhatsApp chats match '{query}': "
            f"{', '.join(others[:6])}. Nothing is staged — read these names "
            f"to Mo and ask which one."
        )

    display_name, phone = _find_contact(contact_name)
    if phone is None:
        return (
            f"I don't have a WhatsApp number for '{contact_name}'. "
            f"Say 'add WhatsApp contact {contact_name} [number]' first, "
            f"or say 'send WhatsApp to [number]' to message a number directly."
        )
    return _stage_pending(display_name, phone, message)


def send_to_number(phone: str, message: str) -> str:
    """
    Stage a WhatsApp message to a raw phone number (not in contacts).
    Useful for one-off messages without saving the contact first.
    """
    try:
        normalised = _normalise_phone(phone)
    except Exception:
        normalised = phone
    return _stage_pending(normalised, normalised, message)


def confirm_whatsapp_send() -> str:
    """
    Execute a pending WhatsApp send.
    Opens WhatsApp Desktop via whatsapp:// URI, waits for it to focus,
    then presses Enter to send.
    """
    from core import staging

    if not _pending:
        return "No pending WhatsApp message to confirm."

    expires_at = _pending.get("expires_at")
    if expires_at and datetime.now(CAIRO_TZ) > expires_at:
        _pending.clear()
        staging.resolve("expired")
        return "WhatsApp send expired — say your message again to retry."

    name = _pending["name"]
    phone = _pending["phone"]
    message = _pending["message"]
    _pending.clear()

    if phone is None:
        from tools import whatsapp_desktop
        try:
            whatsapp_desktop.send(name, message)
        except Exception as e:
            staging.resolve("failed")
            return f"[WhatsApp: not sent to {name} — {e}]"
        staging.resolve("sent", f"whatsapp → {name} · sent")
        _learn_name(name)
        return f"Sent to {name} on WhatsApp"

    # Phone for URI: digits only, no leading +
    phone_digits = "".join(c for c in phone if c.isdigit())
    encoded_msg = urllib.parse.quote(message)
    uri = f"whatsapp://send?phone={phone_digits}&text={encoded_msg}"

    try:
        os.startfile(uri)
    except Exception:
        try:
            subprocess.run(f'start "" "{uri}"', shell=True, check=False)
        except Exception as e:
            staging.resolve("failed")       # nothing can send it now
            return (
                f"[WhatsApp error: couldn't open WhatsApp Desktop — "
                f"make sure it's installed from whatsapp.com/download. ({e})]"
            )

    # Wait for WhatsApp Desktop to become the foreground window
    focused = _wait_for_whatsapp_focus(timeout=8.0)
    if not focused:
        # Enter goes to whatever window is in front: it used to be pressed
        # anyway, into Mo's own app. Hand the message to him instead.
        staging.resolve("handoff")
        return (
            f"WhatsApp opened with message to {name} — "
            "press Enter to send (took longer than expected to load)."
        )

    # Small extra pause so the text field settles before pressing Enter
    time.sleep(0.4)

    try:
        import keyboard
        keyboard.press_and_release("enter")
        # Only here did the message actually leave — anything below is a
        # hand-off to Mo, so it clears the stage without writing a receipt.
        staging.resolve("sent", f"whatsapp → {name} · sent")
        _learn_name(name)
        return f"Sent to {name}"
    except Exception as e:
        staging.resolve("handoff")
        return (
            f"WhatsApp opened with the message to {name} — "
            f"press Enter in WhatsApp to send. ({e})"
        )
