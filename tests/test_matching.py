"""Task 8：可解释匹配引擎测试。"""

from __future__ import annotations

from advisor_fit.analysis.matching import build_match_report
from advisor_fit.models.common import FactStatus
from advisor_fit.models.match import Recommendation
from advisor_fit.models.professor import (
    ObservedTopic,
    ProfessorProfile,
    Recruiting,
)
from advisor_fit.models.student import StudentFact, StudentProfile


def _professor(recruiting: str = "UNKNOWN") -> ProfessorProfile:
    return ProfessorProfile(
        professor_id="p1",
        identity_confirmed=True,
        declared_interests=[ObservedTopic(topic="信息检索", trend="DECLARED")],
        observed_recent_topics=[
            ObservedTopic(topic="检索增强生成", trend="EMERGING", window="2024-2026")
        ],
        recruiting=Recruiting(status=recruiting),
    )


def _professor_topic(topic: str) -> ProfessorProfile:
    return ProfessorProfile(
        professor_id="p1",
        identity_confirmed=True,
        declared_interests=[ObservedTopic(topic=topic, trend="DECLARED", evidence_ids=["ev1"])],
    )


def _strong_student() -> StudentProfile:
    return StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(
                id="f1",
                field="skill",
                value="信息检索",
                status=FactStatus.FACT,
                source_id="cv",
                user_confirmed=True,
            ),
            StudentFact(
                id="f2",
                field="skill",
                value="Python",
                status=FactStatus.FACT,
                source_id="cv",
                user_confirmed=True,
            ),
        ],
    )


def _student_interest_only(interest: str) -> StudentProfile:
    return StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(
                id="f1",
                field="interest",
                value=interest,
                status=FactStatus.FACT,
                source_id="cv",
                user_confirmed=True,
            )
        ],
    )


def test_match_report_separates_fit_from_recruiting():
    report = build_match_report(_strong_student(), _professor(recruiting="UNKNOWN"))
    assert report.research_fit == "STRONG"
    assert report.opportunity_signal == "UNKNOWN"


def test_public_report_has_no_percentage_score():
    report = build_match_report(_strong_student(), _professor())
    assert not hasattr(report, "match_percentage")
    assert report.recommendation in {
        "WORTH_CONTACTING",
        "LEARN_MORE",
        "LOW_PRIORITY",
        "INSUFFICIENT_EVIDENCE",
    }


def test_keyword_overlap_without_project_evidence_is_not_strong():
    report = build_match_report(_student_interest_only("LLM"), _professor_topic("LLM"))
    assert report.research_fit != "STRONG"
    assert report.recommendation != Recommendation.WORTH_CONTACTING


def test_unconfirmed_student_is_insufficient():
    student = StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(
                id="f1",
                field="skill",
                value="信息检索",
                status=FactStatus.FACT,
                source_id="cv",
                user_confirmed=False,
            )
        ],
    )
    report = build_match_report(student, _professor())
    assert report.recommendation == Recommendation.INSUFFICIENT_EVIDENCE
