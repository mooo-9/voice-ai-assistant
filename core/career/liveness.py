"""Whether a posting is closed, from its text: the banners boards and careers
sites put on a filled or expired job (the patterns career-ops' liveness check
uses). A closed posting isn't scored: that's a Claude call spent on a job Mo
can't apply to."""
import re

_CLOSED = re.compile("|".join([
    r"no longer accepting applications",
    r"job (?:is )?no longer (?:available|open)",
    # A job filled, not an application form ("once the form has been filled out").
    r"\b(?:job|position|role|posting|opening|vacancy)\b.{0,60}?(?<!application )(?<!form )"
    r"has been filled\b(?!\s+out)",
    r"(?:this job|job posting) has expired",
    r"this (?:job|role|position)(?: listing)? is closed\b(?!-)",
    r"this (?:job|role|position) (?:is )?no longer",
    r"applications? (?:(?:have|are|is) )?closed",
    r"job (?:listing )?not found",
]), re.IGNORECASE | re.DOTALL)


def closed(text: str) -> bool:
    return bool(_CLOSED.search((text or "").replace("’", "'")))
