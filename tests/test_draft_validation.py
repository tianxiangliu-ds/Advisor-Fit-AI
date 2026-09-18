"""Task 10：邮件草稿逐句 provenance 校验测试。"""

from __future__ import annotations

import pytest

from advisor_fit.llm.drafting import generate_draft
from advisor_fit.models.common import FactStatus
from advisor_fit.models.evidence import Evidence
from advisor_fit.models.match import Draft, DraftSentence, MatchReport, SentenceType
from advisor_fit.models.professor import ObservedTopic, ProfessorProfile
from advisor_fit.models.student import StudentFact, StudentProfile
from advisor_fit.validation.draft import validate_draft


def _evidence_map() -> dict[str, Evidence]:
    return {"ev1": Evidence(id="ev1", source_type="openalex", title="RAG Survey")}


def _confirmed_student() -> StudentProfile:
    return StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(
                id="f1",
                field="skill",
                value="Python",
                status=FactStatus.FACT,
                source_id="cv",
                user_confirmed=True,
            )
        ],
    )


def _valid_draft() -> Draft:
    return Draft(
        sentences=[
            DraftSentence(
                text="您的团队近期研究检索增强生成。",
                sentence_type=SentenceType.PROFESSOR_FACT,
                evidence_ids=["ev1"],
            ),
            DraftSentence(
                text="我在课程项目中实现过 BM25 检索。",
                sentence_type=SentenceType.STUDENT_FACT,
                fact_ids=["f1"],
            ),
            DraftSentence(text="请问您是否有招生名额？", sentence_type=SentenceType.GENERIC),
        ]
    )


def test_student_sentence_requires_confirmed_fact_id():
    draft = Draft(
        sentences=[
            DraftSentence(
                text="我曾发表三篇顶会论文。",
                sentence_type=SentenceType.STUDENT_FACT,
                fact_ids=["missing"],
                evidence_ids=[],
            )
        ]
    )
    result = validate_draft(draft, _confirmed_student(), _evidence_map())
    assert not result.ok


@pytest.mark.parametrize("phrase", ["拜读了您的论文", "久仰大名", "深受震撼"])
def test_unconfirmed_flattery_is_rejected(phrase):
    draft = Draft(sentences=[DraftSentence(text=phrase, sentence_type=SentenceType.GENERIC)])
    result = validate_draft(draft, _confirmed_student(), _evidence_map())
    assert not result.ok


def test_valid_draft_has_full_provenance_coverage():
    result = validate_draft(_valid_draft(), _confirmed_student(), _evidence_map())
    assert result.ok
    assert result.professor_fact_coverage == 1.0
    assert result.student_fact_coverage == 1.0


def test_professor_fact_without_evidence_is_rejected():
    draft = Draft(
        sentences=[
            DraftSentence(
                text="您的团队近期研究检索增强生成。",
                sentence_type=SentenceType.PROFESSOR_FACT,
            )
        ]
    )
    result = validate_draft(draft, _confirmed_student(), _evidence_map())
    assert not result.ok


def test_unavailable_llm_falls_back_to_grounded_template():
    class UnavailableLLM:
        def generate(self, **_kwargs):
            raise RuntimeError("not configured")

    professor = ProfessorProfile(
        professor_id="王老师",
        identity_confirmed=True,
        observed_recent_topics=[
            ObservedTopic(topic="检索增强生成", evidence_ids=["ev1"])
        ],
    )

    draft = generate_draft(
        UnavailableLLM(), _confirmed_student(), professor, MatchReport()
    )
    result = validate_draft(draft, _confirmed_student(), _evidence_map())

    assert draft.subject
    assert draft.sentences
    assert result.ok
