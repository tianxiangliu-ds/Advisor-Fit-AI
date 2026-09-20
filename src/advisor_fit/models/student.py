"""学生画像与学生事实。

规则：
- 只有 status=FACT 且 user_confirmed=True 的事实才能进入邮件草稿；
- INFERRED/UNKNOWN 即使被错误置为 user_confirmed=True 也会被模型校验拒绝。
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from advisor_fit.models.common import FactStatus


class StudentFact(BaseModel):
    id: str
    field: str
    value: str | float | int | None
    status: FactStatus
    source_id: str | None = None
    user_confirmed: bool = False

    @model_validator(mode="after")
    def prevent_confirmed_inference(self) -> StudentFact:
        if self.status != FactStatus.FACT and self.user_confirmed:
            raise ValueError("only FACT can be confirmed for drafting")
        return self


class Education(BaseModel):
    """结构化教育经历（用于更精准的匹配与邮件）。"""

    degree: str = ""        # 学历，如「硕士」
    institution: str = ""   # 学校
    major: str = ""         # 专业/方向
    start_year: str = ""    # 起（允许「2022」或「2022.9」）
    end_year: str = ""      # 止


class Project(BaseModel):
    name: str = ""
    description: str = ""
    role: str = ""


class Publication(BaseModel):
    title: str = ""
    venue: str = ""
    year: str = ""


class StudentProfile(BaseModel):
    student_id: str
    name: str | None = None
    facts: list[StudentFact] = []
    education: list[Education] = []
    projects: list[Project] = []
    publications: list[Publication] = []

    def draftable_facts(self) -> list[StudentFact]:
        """返回可以进入邮件草稿的、已确认的 FACT 事实。"""
        return [f for f in self.facts if f.status == FactStatus.FACT and f.user_confirmed]

    def confirmed_fact_ids(self) -> set[str]:
        return {f.id for f in self.draftable_facts()}
