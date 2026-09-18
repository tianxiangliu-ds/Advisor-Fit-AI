"""中文邮件草稿的受控生成。生成结果仍需经 validate_draft 校验。"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.models.match import Draft, DraftSentence
from advisor_fit.models.professor import ProfessorProfile
from advisor_fit.models.student import StudentProfile

_DRAFT_INSTRUCTIONS = (
    "写一封中文联系邮件草稿，正文 180–350 字。"
    "结构：身份与目的 1–2 句、最强经历连接 2–3 句、导师真实研究连接 1–2 句、"
    "明确询问与附件说明、收尾。每句显式标注 sentence_type："
    "PROFESSOR_FACT（涉及导师事实，只引用 payload 提供的 evidence_ids）；"
    "STUDENT_FACT（涉及学生经历，只引用 payload 提供的 fact id）；"
    "GENERIC（问候、意图、收尾）。"
    "禁止使用拜读、久仰、震撼等未经证实的恭维措辞；不要声称已阅读某篇论文。"
)


class DraftOutput(BaseModel):
    subject: str = ""
    sentences: list[DraftSentence] = []


def generate_draft(
    llm, student: StudentProfile, professor: ProfessorProfile, match_report
) -> Draft:
    payload = {
        "student_facts": [
            {"id": f.id, "field": f.field, "value": f.value}
            for f in student.draftable_facts()
        ],
        "professor_topics": [
            {"topic": t.topic, "evidence_ids": t.evidence_ids}
            for t in [*professor.observed_recent_topics, *professor.declared_interests]
        ],
        "recommendation": match_report.recommendation.value,
    }
    try:
        output = llm.generate(
            schema=DraftOutput, instructions=_DRAFT_INSTRUCTIONS, payload=payload
        )
    except Exception:  # noqa: BLE001 - LLM 不可用时使用确定性、可校验模板
        return generate_template_draft(student, professor)

    if not isinstance(output, DraftOutput):
        return Draft(subject="", sentences=[], warnings=["LLM 输出格式错误"])

    return Draft(subject=output.subject, sentences=output.sentences, warnings=[])


def generate_template_draft(
    student: StudentProfile, professor: ProfessorProfile
) -> Draft:
    """无 LLM 时的事实锁定模板；所有事实句都绑定来源 ID。"""
    professor_label = professor.name.value or professor.professor_id or "老师"
    student_name = student.name or "一名学生"
    sentences = [
        DraftSentence(text=f"尊敬的{professor_label}老师，您好！", sentence_type="GENERIC"),
        DraftSentence(
            text=f"我是{student_name}，想了解您的研究方向与招生安排。",
            sentence_type="GENERIC",
        ),
    ]

    facts = student.draftable_facts()
    skills = [f for f in facts if f.field == "skill"]
    interests = [f for f in facts if f.field == "interest"]

    if skills:
        sentences.append(
            DraftSentence(
                text=f"我掌握的技能包括{'、'.join(str(f.value) for f in skills)}。",
                sentence_type="STUDENT_FACT",
                fact_ids=[f.id for f in skills],
            )
        )
    if interests:
        sentences.append(
            DraftSentence(
                text=f"我的研究兴趣包括{'、'.join(str(f.value) for f in interests)}。",
                sentence_type="STUDENT_FACT",
                fact_ids=[f.id for f in interests],
            )
        )

    all_topics = [*professor.observed_recent_topics, *professor.declared_interests]
    topics = [t for t in all_topics if t.evidence_ids][:3]
    if topics:
        sentences.append(
            DraftSentence(
                text=f"了解到您的公开研究涉及{'、'.join(t.topic for t in topics)}。",
                sentence_type="PROFESSOR_FACT",
                evidence_ids=[ev for t in topics for ev in t.evidence_ids],
            )
        )
    publications = [p for p in professor.recent_publications if p.source_ids][:3]
    if publications:
        sentences.append(
            DraftSentence(
                text=f"您近年发表了{'、'.join(f'《{p.title}》' for p in publications)}等成果。",
                sentence_type="PROFESSOR_FACT",
                evidence_ids=[ev for p in publications for ev in p.source_ids],
            )
        )

    sentences.append(
        DraftSentence(
            text="想请教您近期是否有相关研究或招生安排，感谢您的时间。",
            sentence_type="GENERIC",
        )
    )
    sentences.append(DraftSentence(text=student_name, sentence_type="GENERIC"))
    return Draft(
        subject="咨询研究与招生机会",
        sentences=sentences,
        warnings=["未配置 LLM，已使用事实锁定模板"],
    )
