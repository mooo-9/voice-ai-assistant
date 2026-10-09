"""Mo's job-hunt target: AI engineering and data engineering first, with data
analyst/BI, SAP/ERP and graduate programmes as backups. Business analyst
roles are no longer sought."""
from pathlib import Path

import pytest

from core.career import pipeline, store

RUBRIC = Path(pipeline.__file__).with_name("rubric.md")


@pytest.mark.parametrize("title", [
    "Junior Data Engineer", "Big Data Engineer", "ETL Developer", "Analytics Engineer",
    "Data Platform Engineer", "AI Engineer", "Machine Learning Engineer", "Data Analyst",
    "Power BI Developer",
])
def test_target_titles_are_in_field(title):
    assert pipeline._in_field({"title": title, "description": ""})


@pytest.mark.parametrize("title", ["Business Analyst", "Junior Business Analyst", "HR Analyst"])
def test_business_analyst_titles_are_not_sought(title):
    assert not pipeline._in_field({"title": title, "description": ""})


def test_search_terms_cover_data_engineering_and_drop_business_analyst():
    terms = [t.lower() for t in store.DEFAULTS["search_terms"]]
    assert "data engineer" in terms and "junior data engineer" in terms
    assert "ai engineer" in terms and "data analyst" in terms and "graduate program" in terms
    assert not any("business analyst" in t for t in terms)


def test_rubric_targets_ai_and_data_engineering_first():
    text = RUBRIC.read_text(encoding="utf-8")
    target = text.splitlines()[2]
    assert "AI engineering and data engineering" in target
    assert "business analyst" not in text.lower()
