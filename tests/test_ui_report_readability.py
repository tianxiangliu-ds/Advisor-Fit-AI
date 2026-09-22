"""匹配简报应把判断依据与原文入口放在结论附近。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings
from advisor_fit.llm.analysis import AnalysisPoint, DeepAnalysis, DirectionSummary
from advisor_fit.manual_pipeline import PipelineResult
from advisor_fit.models.common import FactStatus
from advisor_fit.models.evidence import Evidence
from advisor_fit.models.match import Draft, MatchDimension, MatchReport
from advisor_fit.models.professor import FactValue, ProfessorProfile, RecentPublication
from advisor_fit.models.resolution import ResolutionResult
from advisor_fit.models.student import StudentProfile
from advisor_fit.validation.draft import DraftValidationResult

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def _result():
    evidence = Evidence(
        id="ev-paper",
        source_type="出版社/DOI",
        title="可追溯的推荐论文",
        source_url="https://example.edu/recommended-paper",
        published_date="2025",
    )
    professor = ProfessorProfile(
        professor_id="p1",
        name=FactValue(value="王老师", status=FactStatus.FACT, evidence_ids=["ev-profile"]),
        recent_publications=[
            RecentPublication(
                id="paper-1",
                title="可追溯的推荐论文",
                year=2025,
                source_ids=["ev-paper"],
                source_url=evidence.source_url,
            )
        ],
    )
    report = MatchReport(
        recommendation="LEARN_MORE",
        research_fit="PARTIAL",
        evidence_sufficiency="MEDIUM",
        strengths=[
            MatchDimension(
                key="method",
                label="Python 工程能力",
                level="STRONG",
                summary="学生事实与论文方法存在可迁移交集",
                student_fact_ids=["f1"],
                professor_evidence_ids=["ev-paper"],
            )
        ],
        gaps=["尚未确认密码学项目经历"],
    )
    return PipelineResult(
        run_id="report-readability",
        student=StudentProfile(student_id="s1"),
        professor=professor,
        match_report=report,
        resolution=ResolutionResult(status="CONFIRMED"),
        deep_analysis=DeepAnalysis(
            recommended_papers=[
                AnalysisPoint(text="建议优先阅读这篇论文", evidence_ids=["ev-paper"])
            ]
        ),
        direction_summary=DirectionSummary(),
        evidences=[evidence],
        draft=Draft(),
        draft_validation=DraftValidationResult(),
    )


def test_report_explains_yellow_status_and_links_recommended_paper(tmp_path, monkeypatch):
    """若黄色仍无解释或推荐论文无法直达原文，这个测试会失败。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    app = AppTest.from_file(APP_PATH).run(timeout=15)
    app.session_state["result"] = _result()
    app.session_state["active_page"] = "report"
    app.run(timeout=15)

    assert not app.exception
    rendered = "\n".join(
        str(item.value) for item in [*app.markdown, *app.caption]
    )
    assert "黄色表示" in rendered
    assert "为什么是黄色" in rendered
    assert "https://example.edu/recommended-paper" in rendered
    assert not any(item.value.startswith("01") for item in app.subheader)
