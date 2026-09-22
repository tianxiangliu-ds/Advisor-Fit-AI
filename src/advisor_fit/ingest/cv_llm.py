"""LLM 结构化 CV 抽取：把简历文本变成可核验的学生画像（事实 + 教育/项目/论文）。"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.llm.prompts import prompt_text
from advisor_fit.models.common import FactStatus
from advisor_fit.models.student import (
    Education,
    Project,
    Publication,
    StudentFact,
    StudentProfile,
)

# 与 UI 的 Selectbox 选项保持一致
_ALLOWED_FIELDS = ("skill", "degree", "institution", "interest", "project", "publication")

_EXTRACT_INSTRUCTIONS = prompt_text("cv_extract")


class LlmStudentFact(BaseModel):
    field: str
    value: str


class LlmEducation(BaseModel):
    degree: str = ""
    institution: str = ""
    major: str = ""
    start_year: str = ""
    end_year: str = ""


class LlmProject(BaseModel):
    name: str = ""
    description: str = ""
    role: str = ""


class LlmPublication(BaseModel):
    title: str = ""
    venue: str = ""
    year: str = ""


class StudentProfileOutput(BaseModel):
    name: str = ""
    facts: list[LlmStudentFact] = []
    education: list[LlmEducation] = []
    projects: list[LlmProject] = []
    publications: list[LlmPublication] = []


def build_student_profile_llm(
    text: str, llm, *, student_id: str = "student_llm"
) -> StudentProfile:
    """用 LLM 从简历文本抽取结构化画像；空文本直接返回空画像。"""
    if not text.strip():
        return StudentProfile(student_id=student_id, facts=[])

    output = llm.generate(
        schema=StudentProfileOutput,
        instructions=_EXTRACT_INSTRUCTIONS,
        payload={"cv_text": text},
    )
    if not isinstance(output, StudentProfileOutput):
        output = StudentProfileOutput()

    facts: list[StudentFact] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(output.facts):
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

    # 页面上的“学生事实”以 facts 为唯一来源。不能只把项目/论文放进
    # 隐藏的结构化字段，否则用户会误以为 PDF 没有被解析到这些经历。
    def add_experience_fact(field: str, value: str) -> None:
        value = value.strip()
        key = (field, value)
        if not value or key in seen:
            return
        seen.add(key)
        facts.append(
            StudentFact(
                id=f"fact_llm_{len(facts)}",
                field=field,
                value=value,
                status=FactStatus.FACT,
                source_id="cv_llm",
                user_confirmed=False,
            )
        )

    for project in output.projects:
        pieces = [project.name.strip(), project.description.strip()]
        add_experience_fact("project", "：".join(piece for piece in pieces if piece))
    for publication in output.publications:
        details = "，".join(
            piece for piece in (publication.venue.strip(), publication.year.strip()) if piece
        )
        title = publication.title.strip()
        add_experience_fact("publication", f"{title}（{details}）" if details else title)

    return StudentProfile(
        student_id=student_id,
        name=output.name.strip() or None,
        facts=facts,
        education=[
            Education(
                degree=e.degree.strip(),
                institution=e.institution.strip(),
                major=e.major.strip(),
                start_year=e.start_year.strip(),
                end_year=e.end_year.strip(),
            )
            for e in output.education
            if e.institution.strip() or e.degree.strip()
        ],
        projects=[
            Project(name=p.name.strip(), description=p.description.strip(), role=p.role.strip())
            for p in output.projects
            if p.name.strip() or p.description.strip()
        ],
        publications=[
            Publication(title=p.title.strip(), venue=p.venue.strip(), year=p.year.strip())
            for p in output.publications
            if p.title.strip()
        ],
    )
