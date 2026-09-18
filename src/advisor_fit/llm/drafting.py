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
    except Exception:  # noqa: BLE001 - LLM 不可用时返回空草稿，交由上层降级
        return Draft(subject="", sentences=[], warnings=["LLM 不可用，未生成邮件草稿"])

    if not isinstance(output, DraftOutput):
        return Draft(subject="", sentences=[], warnings=["LLM 输出格式错误"])

    return Draft(subject=output.subject, sentences=output.sentences, warnings=[])
