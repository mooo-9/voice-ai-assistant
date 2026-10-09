"""What the scored jobs keep asking for that Mo lacks: grouped by Claude,
counted here (one job counts once per skill)."""
from unittest.mock import patch

from core.career import gaps, tracker


def _scored(n, missing):
    tracker.add({"url": f"u{n}", "title": "Analyst", "company": "Co", "score": 50,
                 "missing": missing, "status": "skipped"})


def test_counts_jobs_not_mentions():
    for n in range(5):
        _scored(n, ["Tableau", "Tableau dashboards"] if n < 3 else ["3+ years"])
    # items 0-5 are the first three jobs' Tableau mentions; 6-7 the years.
    groups = {"groups": [{"skill": "Tableau", "items": [0, 1, 2, 3, 4, 5]},
                         {"skill": "Lone", "items": [6]}]}
    with patch("core.career.claude.ask", return_value=groups):
        out = gaps.summary()
    assert out == ("What the 5 scored jobs ask for that your CV doesn't show, most asked "
                   "first: Tableau (3 jobs).")


def test_too_few_jobs_asks_no_claude():
    _scored(1, ["Tableau"])
    with patch("core.career.claude.ask") as ask:
        assert gaps.summary().startswith("Only 1 scored jobs")
    ask.assert_not_called()


def test_an_index_claude_invents_is_ignored():
    for n in range(5):
        _scored(n, ["SAP"])
    with patch("core.career.claude.ask",
               return_value={"groups": [{"skill": "SAP", "items": [0, 1, 99, -1]}]}):
        assert "SAP (2 jobs)" in gaps.summary()


def test_the_nightly_answer_is_kept_and_given_at_once():
    """Through Claude Code the grouping took a minute: too long to wait by voice."""
    for n in range(5):
        _scored(n, ["SAP"])
    with patch("core.career.claude.ask",
               return_value={"groups": [{"skill": "SAP", "items": [0, 1]}]}) as ask:
        gaps.refresh()
        assert "SAP (2 jobs)" in gaps.summary()
    assert ask.call_count == 1
