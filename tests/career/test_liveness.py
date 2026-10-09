"""A closed posting is told from a live one by its text (career-ops' patterns)."""
import pytest

from core.career import liveness


@pytest.mark.parametrize("text", [
    "No longer accepting applications",
    "Sorry, this job is no longer available.",
    "The position you are trying to apply for has been filled.",
    "This job has expired.",
    "This role is closed.",
    "Applications have closed for this vacancy.",
    "This position is no longer open",
    "Job not found",
    "This job isn’t here: this position is no longer listed.",
])
def test_closed(text):
    assert liveness.closed(text)


@pytest.mark.parametrize("text", [
    "Build SQL reports. Fresh graduates welcome. Apply now.",
    "Once the application form has been filled out, HR will call you.",
    "This role is closed-loop control of the production line.",
    "",
])
def test_live(text):
    assert not liveness.closed(text)
