"""Turning what the model wrote into what a surface should show.

The model is told to answer in plain spoken English, and mostly does — but on
a summary it reaches for markdown anyway and writes "**Personal facts:** ..."
Those asterisks then went straight into a QLabel, so the Cockpit displayed
literal `**` and the transcript read as a wall of syntax.

core/voice_out.py already strips markdown, but for the *ear*: it also expands
"e.g." to "for example" and turns dashes into pauses, which is wrong for text
on screen. This is the display-side counterpart.

`sections()` is the useful half. Rather than deleting the structure the model
reached for, it reads it: "**Academic:** you've finished..." becomes a labelled
section a surface can set as a kicker over body text, which is the shape the
design language already uses everywhere else.
"""
import re

# **bold**, __bold__, *italic*, _italic_ — captured rather than deleted so the
# words inside survive.
_BOLD = re.compile(r"\*{2,3}([^*]+)\*{2,3}|_{2}([^_]+)_{2}")
_ITALIC = re.compile(r"(?<!\w)[*_]([^*_\n]+)[*_](?!\w)")
_CODE = re.compile(r"`{1,3}([^`]*)`{1,3}")
_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^[ \t]*[-*+]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^[ \t]*\d+\.\s+", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RULE = re.compile(r"^[-_*]{3,}\s*$", re.MULTILINE)

# "**Label:** body" or "**Label**: body" at the start of a block is always a
# label. A plain "Label: body" is one only in a run of them — a meal plan's
# "Breakfast: ... / Lunch: ..." — because on its own it is usually a sentence:
# "Tomorrow in Cairo: sunny" put "TOMORROW IN CAIRO" over "sunny". Either way
# the label is short — a heading, not a clause that has a colon.
_BOLD_MARK = r"(?:\*{2,3}|_{2})"
_BOLD_LABELLED = re.compile(
    rf"^\s*{_BOLD_MARK}\s*([A-Z][^:*_\n]{{1,40}}?)\s*"
    rf"(?::\s*{_BOLD_MARK}|{_BOLD_MARK}\s*:)\s*(.*)$",
    re.DOTALL)
_PLAIN_LABELLED = re.compile(r"^\s*([A-Z][^:*\n]{1,40}?)\s*:\s*(.*)$", re.DOTALL)


def plain(text: str) -> str:
    """Markdown out, words intact. For display, not for speech."""
    if not text:
        return ""
    text = _HEADING.sub("", text)
    text = _LINK.sub(r"\1", text)
    # Two alternatives, so the replacement has to pick whichever matched —
    # r"\1" silently yields an empty string for the __bold__ branch.
    text = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _CODE.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _RULE.sub("", text)
    text = _BULLET.sub("• ", text)
    text = _NUMBERED.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sections(text: str) -> "list[tuple[str, str]]":
    """Split an answer into (label, body) pairs.

    A block written as "**Academic:** you've finished ..." comes back as
    ("Academic", "you've finished ..."), so a surface can set the label as a
    kicker and the body as prose — the same kicker-over-value shape the rest
    of the design uses. A block with no label comes back with an empty one.

    Returns [] for empty input, never None, so callers can loop without a
    guard.
    """
    def labelled(match, plain_style: bool):
        if not match:
            return None
        label, body = match.group(1).strip(), plain(match.group(2))
        # A heading, not the first half of a sentence: a body, a short label,
        # and for plain labels no trailing digit ("It's 2:30", "3:1").
        if not body or len(label.split()) > 5 or (plain_style and label[-1].isdigit()):
            return None
        return label, " ".join(body.split())

    # Labels are read before the markdown goes: once the asterisks are
    # stripped, a bold heading and a plain sentence with a colon look alike.
    blocks = []
    for block in re.split(r"\n\s*\n", text or ""):
        bold = labelled(_BOLD_LABELLED.match(block), plain_style=False)
        loose = None if bold else labelled(_PLAIN_LABELLED.match(plain(block)),
                                           plain_style=True)
        blocks.append((block, bold, loose))
    run_of_plain = sum(1 for _, _, loose in blocks if loose) >= 2

    out: list[tuple[str, str]] = []
    for block, bold, loose in blocks:
        if bold or (loose and run_of_plain):
            out.append(bold or loose)
            continue
        body = plain(block)
        if body:
            out.append(("", " ".join(body.split())))
    return out
