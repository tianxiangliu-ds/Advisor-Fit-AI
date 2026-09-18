"""Task 1：领域模型约束测试。"""

import pytest
from pydantic import ValidationError

from advisor_fit.models.common import Confidence, FactStatus
from advisor_fit.models.evidence import Claim, ClaimStatus
from advisor_fit.models.match import DraftSentence, MatchReport, SentenceType
from advisor_fit.models.student import StudentFact, StudentProfile


def test_inferred_fact_cannot_be_confirmed_for_drafting():
    with pytest.raises(ValidationError):
        StudentFact(
            id="fact_1",
            field="experience",
            value="做过大模型训练",
            status=FactStatus.INFERRED,
            source_id="cv_1",
            user_confirmed=True,
        )


def test_profile_returns_only_draftable_facts():
    profile = StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(
                id="fact_1",
                field="skill",
                value="Python",
                status=FactStatus.FACT,
                source_id="cv_1",
                user_confirmed=True,
            ),
            StudentFact(
                id="fact_2",
                field="skill",
                value="PyTorch",
                status=FactStatus.FACT,
                source_id="cv_1",
                user_confirmed=False,
            ),
            StudentFact(
                id="fact_3",
                field="interest",
                value="RAG",
                status=FactStatus.INFERRED,
                source_id="cv_1",
            ),
        ],
    )
    assert [f.id for f in profile.draftable_facts()] == ["fact_1"]


def test_non_unknown_claim_requires_evidence():
    with pytest.raises(ValidationError):
        Claim(id="c1", text="导师转向大模型", claim_type="trend", status=ClaimStatus.SUPPORTED)


def test_unknown_claim_may_have_no_evidence():
    claim = Claim(
        id="c2",
        text="招生情况未找到公开信息",
        claim_type="recruiting",
        status=ClaimStatus.UNKNOWN,
    )
    assert claim.status == ClaimStatus.UNKNOWN


def test_draft_sentence_requires_explicit_type():
    with pytest.raises(ValidationError):
        DraftSentence(text="您的团队近期研究检索增强生成。")  # 未声明 sentence_type


def test_supported_claim_accepts_evidence():
    claim = Claim(
        id="c3",
        text="近三年持续研究检索增强生成",
        claim_type="trend",
        status=ClaimStatus.SUPPORTED,
        confidence=Confidence.HIGH,
        evidence_ids=["ev1", "ev2"],
    )
    assert claim.evidence_ids == ["ev1", "ev2"]


def test_match_report_has_no_percentage_field():
    report = MatchReport()
    assert not hasattr(report, "match_percentage")


def test_professor_fact_sentence_carries_evidence_ids():
    s = DraftSentence(
        text="您的团队近期研究检索增强生成。",
        sentence_type=SentenceType.PROFESSOR_FACT,
        evidence_ids=["ev_paper_2026"],
    )
    assert s.sentence_type == SentenceType.PROFESSOR_FACT
    assert s.evidence_ids == ["ev_paper_2026"]
