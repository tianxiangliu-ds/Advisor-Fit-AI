"""Task 12：隐私不变量——导出与日志不得包含直接 PII 或 API Key。"""

from __future__ import annotations

from advisor_fit.export.report import export_json, export_markdown
from advisor_fit.ingest.cv import build_student_profile, extract_pdf_text
from advisor_fit.pipeline import run_pipeline
from advisor_fit.storage.repository import Repository


def _run_with_pii_cv(tmp_path, make_cv_pdf, fake_provider, fake_llm, fake_fetch):
    cv_pdf = make_cv_pdf("Python student@example.com 13800138000")
    parsed = extract_pdf_text(cv_pdf)
    confirmed = [f.id for f in build_student_profile(parsed).facts]

    repo = Repository(tmp_path / "app.db")
    return run_pipeline(
        cv_path=cv_pdf,
        professor_url="https://u.edu/faculty/wang",
        academic_provider=fake_provider,
        llm=fake_llm,
        repository=repo,
        fetch_page=fake_fetch,
        confirmed_fact_ids=confirmed,
    )


def test_export_does_not_contain_direct_pii(
    tmp_path, make_cv_pdf, fake_provider, fake_llm, fake_fetch
):
    result = _run_with_pii_cv(tmp_path, make_cv_pdf, fake_provider, fake_llm, fake_fetch)
    combined = export_json(result) + export_markdown(result)

    assert "13800138000" not in combined
    assert "student@example.com" not in combined
    assert "LLM_API_KEY" not in combined


def test_export_does_not_contain_api_key(
    tmp_path, make_cv_pdf, fake_provider, fake_llm, fake_fetch
):
    result = _run_with_pii_cv(tmp_path, make_cv_pdf, fake_provider, fake_llm, fake_fetch)
    assert "sk-" not in export_json(result)
