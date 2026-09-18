"""确定性邮件模板测试。"""

from advisor_fit.llm.drafting import generate_template_draft
from advisor_fit.models.common import FactStatus
from advisor_fit.models.match import SentenceType
from advisor_fit.models.professor import (
    FactValue,
    ObservedTopic,
    ProfessorProfile,
    RecentPublication,
)
from advisor_fit.models.student import StudentFact, StudentProfile


def _student(name: str = "张三") -> StudentProfile:
    return StudentProfile(
        student_id="s1",
        name=name,
        facts=[
            StudentFact(
                id="sk1", field="skill", value="Python",
                status=FactStatus.FACT, user_confirmed=True,
            ),
            StudentFact(
                id="sk2", field="skill", value="PyTorch",
                status=FactStatus.FACT, user_confirmed=True,
            ),
            StudentFact(
                id="int1", field="interest", value="自然语言处理",
                status=FactStatus.FACT, user_confirmed=True,
            ),
        ],
    )


def _professor() -> ProfessorProfile:
    return ProfessorProfile(
        professor_id="p1",
        name=FactValue(value="王伟", status=FactStatus.FACT),
        declared_interests=[ObservedTopic(topic="信息检索", evidence_ids=["ev1"])],
        recent_publications=[RecentPublication(id="w1", title="RAG Survey", source_ids=["ev2"])],
    )


def test_template_uses_all_confirmed_skills():
    draft = generate_template_draft(_student(), _professor())
    skill_sentences = [
        s for s in draft.sentences
        if s.sentence_type == SentenceType.STUDENT_FACT and "技能" in s.text
    ]
    assert skill_sentences, "应有技能句"
    sentence = skill_sentences[0]
    assert "Python" in sentence.text
    assert "PyTorch" in sentence.text
    assert set(sentence.fact_ids) == {"sk1", "sk2"}


def test_template_includes_student_name_and_signoff():
    draft = generate_template_draft(_student("张三"), _professor())
    full_text = "".join(s.text for s in draft.sentences)
    assert "张三" in full_text
    assert draft.sentences[-1].text == "张三"


def test_template_cites_professor_evidence_ids():
    draft = generate_template_draft(_student(), _professor())
    professor_sentences = [
        s for s in draft.sentences if s.sentence_type == SentenceType.PROFESSOR_FACT
    ]
    all_evidence = [ev for s in professor_sentences for ev in s.evidence_ids]
    assert "ev1" in all_evidence
    assert "ev2" in all_evidence


def test_template_mentions_reading_only_when_confirmed():
    draft = generate_template_draft(_student(), _professor(), paper_read_confirmed=False)
    assert "已阅读" not in "".join(s.text for s in draft.sentences)

    draft2 = generate_template_draft(_student(), _professor(), paper_read_confirmed=True)
    assert "已阅读" in "".join(s.text for s in draft2.sentences)
