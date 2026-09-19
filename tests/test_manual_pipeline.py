"""人工证据输入版端到端流程：不联网、不依赖 LLM。"""

from __future__ import annotations

from advisor_fit.export.report import export_markdown
from advisor_fit.ingest.manual_professor import ManualPaperInput, ManualProfessorInput
from advisor_fit.manual_pipeline import run_manual_pipeline
from advisor_fit.models.common import FactStatus
from advisor_fit.models.student import StudentFact, StudentProfile
from advisor_fit.storage.repository import Repository


class UnavailableLLM:
    def generate(self, **_kwargs):
        raise RuntimeError("not configured")


def _student() -> StudentProfile:
    return StudentProfile(
        student_id="student_manual",
        facts=[
            StudentFact(
                id="fact_python",
                field="skill",
                value="Python",
                status=FactStatus.FACT,
            ),
            StudentFact(
                id="fact_kg",
                field="interest",
                value="知识图谱",
                status=FactStatus.FACT,
            ),
        ],
    )


def _professor() -> ManualProfessorInput:
    papers = [
        ManualPaperInput(
            title=f"数字人文知识图谱研究 {year}",
            year=year,
            abstract="使用 Python 构建文化遗产知识图谱并开展数字人文研究。",
            source_url=f"https://example.edu/papers/{year}",
            source_platform="出版社",
            keywords=["知识图谱", "数字人文"],
            user_confirmed=True,
        )
        for year in (2024, 2025, 2026)
    ]
    return ManualProfessorInput(
        name="王老师",
        institution="武汉大学",
        homepage_url="https://sim.whu.edu.cn/teacher/wang",
        declared_interests=["数字人文"],
        identity_confirmed=True,
        papers=papers,
    )


def test_manual_pipeline_runs_offline_and_generates_grounded_template(tmp_path):
    repository = Repository(tmp_path / "app.db")

    result = run_manual_pipeline(
        student=_student(),
        confirmed_fact_ids={"fact_python", "fact_kg"},
        professor_input=_professor(),
        llm=UnavailableLLM(),
        repository=repository,
    )

    assert result.professor.identity_confirmed
    assert len(result.professor.recent_publications) == 3
    assert result.match_report.research_fit in {"STRONG", "PARTIAL"}
    assert result.draft.subject
    assert result.draft.sentences
    assert result.draft_validation.ok
    assert all(e.source_url for e in result.evidences if e.source_type == "出版社/DOI")
    assert repository.load_run(result.run_id)["status"] == "COMPLETED"
    markdown = export_markdown(result)
    assert "王老师" in markdown
    assert "https://example.edu/papers/2024" in markdown


def test_manual_pipeline_uses_title_and_abstract_when_keywords_missing(tmp_path):
    professor = _professor().model_copy(
        update={
            "declared_interests": [],
            "papers": [
                ManualPaperInput(
                    title="A knowledge graph method",
                    abstract="本研究关注知识图谱和信息组织。",
                    source_url="https://example.edu/paper",
                    keywords=[],
                    user_confirmed=True,
                )
            ],
        }
    )
    repository = Repository(tmp_path / "app.db")

    result = run_manual_pipeline(
        student=_student(),
        confirmed_fact_ids={"fact_kg"},
        professor_input=professor,
        llm=UnavailableLLM(),
        repository=repository,
    )

    assert result.match_report.research_fit == "PARTIAL"


def test_export_docx_contains_sections(tmp_path):
    import io

    from docx import Document

    from advisor_fit.export.report import export_docx

    repository = Repository(tmp_path / "app.db")
    result = run_manual_pipeline(
        student=_student(),
        confirmed_fact_ids={"fact_python", "fact_kg"},
        professor_input=_professor(),
        llm=UnavailableLLM(),
        repository=repository,
    )
    docx_bytes = export_docx(result)
    assert docx_bytes.startswith(b"PK")  # ZIP 文件头
    doc = Document(io.BytesIO(docx_bytes))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "导师研究报告" in text
    assert "王老师" in text


def test_manual_pipeline_ignores_unconfirmed_student_facts(tmp_path):
    repository = Repository(tmp_path / "app.db")

    result = run_manual_pipeline(
        student=_student(),
        confirmed_fact_ids=set(),
        professor_input=_professor(),
        llm=UnavailableLLM(),
        repository=repository,
    )

    assert result.match_report.recommendation == "INSUFFICIENT_EVIDENCE"
    assert not any(s.sentence_type == "STUDENT_FACT" for s in result.draft.sentences)
