"""Mo keeps three CVs: the main one (AI and data engineering), an analyst one
(data analyst/BI roles and graduate programmes) and an ERP one. Each job is
written and sent from the right one."""
from unittest.mock import patch

import docx
import pytest

from core.career import profile

MAIN = {"name": "Mo", "cv_path": "main.pdf", "cv_text": "main"}
ANALYST = {"cv_path": "analyst.pdf", "cv_text": "analyst"}
ERP = {"cv_path": "erp.pdf", "cv_text": "erp"}


def _profile():
    return {**MAIN, "analyst": dict(ANALYST), "erp": dict(ERP)}


@pytest.mark.parametrize("title,cv", [
    ("AI Engineer", "main.pdf"),
    ("Machine Learning Engineer", "main.pdf"),
    ("Junior Data Engineer", "main.pdf"),
    ("ETL Developer", "main.pdf"),
    ("Data Scientist", "main.pdf"),
    ("AI Data Analyst", "main.pdf"),               # names AI work: the main CV wins
    ("Data Analyst", "analyst.pdf"),
    ("Power BI Developer", "analyst.pdf"),
    ("Business Intelligence Analyst", "analyst.pdf"),
    ("Graduate Program 2026", "analyst.pdf"),
    ("SAP Consultant", "erp.pdf"),
    ("Odoo Developer", "erp.pdf"),
])
def test_each_role_gets_its_cv(title, cv):
    assert profile.for_job(_profile(), {"title": title})["cv_path"] == cv


def test_analyst_roles_fall_back_to_the_main_cv_until_one_is_imported():
    p = {**MAIN}
    assert profile.for_job(p, {"title": "Data Analyst"})["cv_path"] == "main.pdf"


def _cv(tmp_path, name):
    path = tmp_path / name
    d = docx.Document()
    for _ in range(8):
        d.add_paragraph(f"{name} experience with SQL, Power BI, Python and dashboards for analysis.")
    d.save(path)
    return path


FIELDS = {"name": "Mo", "headline": "H", "education": "GUC", "skills": ["SQL"],
          "experience": ["Intern"], "projects": [], "certifications": [], "languages": [],
          "email": "", "phone": "", "linkedin_url": "", "graduation_year": "", "gpa": ""}


def test_importing_the_analyst_cv_keeps_the_main_and_erp_cvs(tmp_path):
    main, analyst = _cv(tmp_path, "main.docx"), _cv(tmp_path, "analyst.docx")
    with patch("core.career.claude.ask", return_value=dict(FIELDS)):
        profile.import_cv(str(main))
        p = profile.load()
        p["erp"] = dict(ERP)
        profile.save(p)
        out = profile.import_cv(str(analyst), analyst=True)
    p = profile.load()
    assert p["cv_path"] == str(main)
    assert p["analyst"]["cv_path"] == str(analyst)
    assert p["erp"] == ERP
    assert "analyst" in out.lower()
