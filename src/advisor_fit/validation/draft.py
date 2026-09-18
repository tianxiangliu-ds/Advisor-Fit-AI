"""邮件草稿逐句 provenance 校验。

- 每个 PROFESSOR_FACT 句必须引用存在的 evidence ID；
- 每个 STUDENT_FACT 句必须引用用户已确认的 fact ID；
- 禁止拜读/久仰/震撼等未经证实的恭维措辞；
- 未勾选 paper_read_confirmed 时禁止声称已阅读论文；
- 分别输出导师事实与学生经历的 provenance coverage。
"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.models.evidence import Evidence
from advisor_fit.models.match import Draft, SentenceType
from advisor_fit.models.student import StudentProfile

_BANNED_PHRASES = ["拜读", "久仰", "震撼"]


class DraftValidationError(BaseModel):
    code: str
    message: str
    sentence_index: int | None = None


class DraftValidationResult(BaseModel):
    ok: bool = True
    errors: list[DraftValidationError] = []
    professor_fact_coverage: float = 1.0
    student_fact_coverage: float = 1.0


def _claims_paper_read(text: str) -> bool:
    if "论文" not in text:
        return False
    return "已阅读" in text or "读过" in text or "拜读" in text


def validate_draft(
    draft: Draft,
    student: StudentProfile,
    evidences: dict[str, Evidence],
    paper_read_confirmed: bool = False,
) -> DraftValidationResult:
    errors: list[DraftValidationError] = []
    confirmed_ids = student.confirmed_fact_ids()

    professor_count = 0
    professor_valid = 0
    student_count = 0
    student_valid = 0

    for idx, sentence in enumerate(draft.sentences):
        for phrase in _BANNED_PHRASES:
            if phrase in sentence.text:
                errors.append(
                    DraftValidationError(
                        code="UNCONFIRMED_FLATTERY",
                        message=f"含未经证实的恭维措辞「{phrase}」",
                        sentence_index=idx,
                    )
                )
        if not paper_read_confirmed and _claims_paper_read(sentence.text):
            errors.append(
                DraftValidationError(
                    code="PAPER_READ_UNCONFIRMED",
                    message="声称已阅读论文但未确认",
                    sentence_index=idx,
                )
            )

        if sentence.sentence_type == SentenceType.PROFESSOR_FACT:
            professor_count += 1
            if not sentence.evidence_ids:
                errors.append(
                    DraftValidationError(
                        code="PROFESSOR_FACT_WITHOUT_EVIDENCE",
                        message="导师事实句缺少 evidence_ids",
                        sentence_index=idx,
                    )
                )
            elif all(ev in evidences for ev in sentence.evidence_ids):
                professor_valid += 1
            else:
                errors.append(
                    DraftValidationError(
                        code="UNKNOWN_EVIDENCE_ID",
                        message="导师事实句引用不存在的证据",
                        sentence_index=idx,
                    )
                )

        elif sentence.sentence_type == SentenceType.STUDENT_FACT:
            student_count += 1
            if not sentence.fact_ids:
                errors.append(
                    DraftValidationError(
                        code="STUDENT_FACT_WITHOUT_FACT",
                        message="学生经历句缺少 fact_ids",
                        sentence_index=idx,
                    )
                )
            elif all(fid in confirmed_ids for fid in sentence.fact_ids):
                student_valid += 1
            else:
                errors.append(
                    DraftValidationError(
                        code="UNCONFIRMED_STUDENT_FACT",
                        message="学生经历句引用未确认事实",
                        sentence_index=idx,
                    )
                )

    professor_coverage = professor_valid / professor_count if professor_count else 1.0
    student_coverage = student_valid / student_count if student_count else 1.0

    return DraftValidationResult(
        ok=not errors,
        errors=errors,
        professor_fact_coverage=professor_coverage,
        student_fact_coverage=student_coverage,
    )
