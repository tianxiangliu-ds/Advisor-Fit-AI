"""Task 11：端到端 pipeline（fixture，不触真实网络/LLM）。"""

from __future__ import annotations

from advisor_fit.export.report import export_markdown
from advisor_fit.ingest.cv import build_student_profile, extract_pdf_text
from advisor_fit.models.professor import AuthorCandidate
from advisor_fit.pipeline import run_pipeline
from advisor_fit.storage.repository import Repository


def _confirmed_ids(cv_pdf) -> list[str]:
    parsed = extract_pdf_text(cv_pdf)
    profile = build_student_profile(parsed)
    return [f.id for f in profile.facts]


def test_fixture_pipeline_exports_grounded_report(
    tmp_path, make_cv_pdf, fake_provider, fake_llm, fake_fetch
):
    cv_pdf = make_cv_pdf()
    confirmed = _confirmed_ids(cv_pdf)

    repo = Repository(tmp_path / "app.db")
    result = run_pipeline(
        cv_path=cv_pdf,
        professor_url="https://u.edu/faculty/wang",
        academic_provider=fake_provider,
        llm=fake_llm,
        repository=repo,
        fetch_page=fake_fetch,
        confirmed_fact_ids=confirmed,
    )

    assert result.match_report.recommendation
    assert result.claim_validation.ok
    assert result.draft_validation.ok
    assert result.professor.identity_confirmed
    assert "证据" in export_markdown(result)


def test_pipeline_abstains_when_author_unresolved(
    tmp_path, make_cv_pdf, fake_llm, fake_fetch
):
    cv_pdf = make_cv_pdf("Python")
    confirmed = _confirmed_ids(cv_pdf)

    class _NoEvidenceProvider:
        async def search_authors(self, query):
            return [AuthorCandidate(id="A9", name="王伟")]

        async def list_recent_works(self, author_id, since_year):
            return []

    repo = Repository(tmp_path / "app.db")
    result = run_pipeline(
        cv_path=cv_pdf,
        professor_url="https://u.edu/faculty/wang",
        academic_provider=_NoEvidenceProvider(),
        llm=fake_llm,
        repository=repo,
        fetch_page=fake_fetch,
        confirmed_fact_ids=confirmed,
    )

    assert result.match_report.recommendation == "INSUFFICIENT_EVIDENCE"
    assert "作者身份无法确认" in " ".join(result.warnings)
