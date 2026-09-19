"""LLM 结构化 CV 抽取：把简历文本变成可核验的学生画像候选事实。

规则：
- 只抽取文本中明确写出的内容，不得推断、补全或猜测；
- 抽取结果全部 user_confirmed=False，确认前不得进入邮件草稿。
"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.models.common import FactStatus
from advisor_fit.models.student import StudentFact, StudentProfile

# 与 UI 的 Selectbox 选项保持一致
_ALLOWED_FIELDS = ("skill", "degree", "institution", "interest", "project", "publication")

_EXTRACT_INSTRUCTIONS = (
    "从简历文本中抽取学生的可核验事实与姓名。"
    "name 填学生的真实姓名（通常在简历顶部）；没有明确写出则留空。"
    "只抽取文本中明确写出的内容，不得推断、补全或猜测；没有明确证据的内容不要输出。"
    f"field 只能是以下之一：{', '.join(_ALLOWED_FIELDS)}；"
    "value 填简历中的原文或最简表述。"
)


class LlmStudentFact(BaseModel):
    field: str
    value: str


class StudentProfileOutput(BaseModel):
    name: str = ""
    facts: list[LlmStudentFact] = []


def build_student_profile_llm(
    text: str, llm, *, student_id: str = "student_llm"
) -> StudentProfile:
    """用 LLM 从简历文本抽取候选事实；空文本直接返回空画像。"""
    if not text.strip():
        return StudentProfile(student_id=student_id, facts=[])

    output = llm.generate(
        schema=StudentProfileOutput,
        instructions=_EXTRACT_INSTRUCTIONS,
        payload={"cv_text": text},
    )
    name = output.name.strip() if isinstance(output, StudentProfileOutput) else ""
    facts: list[StudentFact] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(output.facts if isinstance(output, StudentProfileOutput) else []):
        field = item.field.strip()
        value = item.value.strip()
        if field not in _ALLOWED_FIELDS or not value:
            continue
        key = (field, value)
        if key in seen:
            continue
        seen.add(key)
        facts.append(
            StudentFact(
                id=f"fact_llm_{index}",
                field=field,
                value=value,
                status=FactStatus.FACT,
                source_id="cv_llm",
                user_confirmed=False,
            )
        )
    return StudentProfile(student_id=student_id, name=name or None, facts=facts)
