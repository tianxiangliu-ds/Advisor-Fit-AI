"""隐私不变量：导出与日志不得包含直接 PII 或 API Key。"""

from __future__ import annotations

from advisor_fit.export.report import export_json, export_markdown
from advisor_fit.ingest.cv import build_student_profile, extract_pdf_text
from advisor_fit.ingest.manual_professor import ManualPaperInput, ManualProfessorInput
from advisor_fit.manual_pipeline import run_manual_pipeline
from advisor_fit.storage.repository import Repository


class UnavailableLLM:
    def generate(self, **_kwargs):
        raise RuntimeError("not configured")


def _professor() -> ManualProfessorInput:
    return ManualProfessorInput(
        name="王老师",
        institution="武汉大学",
        identity_confirmed=True,
        papers=[
            ManualPaperInput(
                title="知识图谱研究",
                abstract="构建文化遗产知识图谱。",
                source_url="https://example.edu/paper",
                keywords=["知识图谱"],
                user_confirmed=True,
            )
        ],
    )


def _run_with_pii_cv(tmp_path, make_cv_pdf):
    cv_pdf = make_cv_pdf("Python student@example.com 13800138000")
    parsed = extract_pdf_text(cv_pdf)
    student = build_student_profile(parsed)
    confirmed = {f.id for f in student.facts}
    repo = Repository(tmp_path / "app.db")
    return run_manual_pipeline(
        student=student,
        confirmed_fact_ids=confirmed,
        professor_input=_professor(),
        llm=UnavailableLLM(),
        repository=repo,
    )


def test_export_does_not_contain_direct_pii(tmp_path, make_cv_pdf):
    result = _run_with_pii_cv(tmp_path, make_cv_pdf)
    combined = export_json(result) + export_markdown(result)

    assert "13800138000" not in combined
    assert "student@example.com" not in combined
    assert "LLM_API_KEY" not in combined


def test_export_does_not_contain_api_key(tmp_path, make_cv_pdf):
    result = _run_with_pii_cv(tmp_path, make_cv_pdf)
    assert "sk-" not in export_json(result)
