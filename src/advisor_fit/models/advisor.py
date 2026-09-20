"""统一导师模型：全项目唯一的「一位导师」表示。

字段分四组：
- 身份：学校 / 学院 / 姓名 / 姓名备注
- 了解他：职称 / 邮箱 / 研究方向 / 研究领域 / 代表论文 / 个人主页 / 简介正文
- 唯一标识：ORCID / OpenAlex 学者编号（解决同名同姓的关键）
- 可追溯：来源集合 / 来源链接 / 抓取时间 / 内容指纹 / 每个字段来自哪个来源

同一人因挂名出现在多个学院时，**每个学院各存一行**（不合并），保证不漏。
合并不同来源时按"字段为单位补空"：已有值不覆盖，只填空缺，并记下该字段的来源。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AdvisorSource(StrEnum):
    COMMUNITY = "community"  # 社区开源名册
    OFFICIAL = "official"  # 官网院系师资页
    FACULTY_DB = "faculty"  # 早期官网采集库
    MANUAL = "manual"  # 用户手动填写


class Advisor(BaseModel):
    university: str
    name: str
    department: str = ""
    note: str = ""

    title: str = ""
    email: str = ""
    research_directions: list[str] = Field(default_factory=list)
    research_areas: list[str] = Field(default_factory=list)
    publications: list[str] = Field(default_factory=list)
    homepage_url: str = ""
    profile_text: str = ""

    orcid: str = ""
    openalex_id: str = ""

    school_cate: str = ""
    sources: list[str] = Field(default_factory=list)
    source_url: str = ""
    retrieved_at: str = ""
    content_hash: str = ""
    field_sources: dict[str, str] = Field(default_factory=dict)

    def key(self) -> tuple[str, str, str]:
        return (self.university, self.department, self.name)

    def missing_fields(self) -> list[str]:
        """还缺哪些「用来了解这位导师」的字段。"""
        checks = {
            "title": self.title,
            "email": self.email,
            "research_directions": self.research_directions,
            "homepage_url": self.homepage_url,
            "orcid": self.orcid or self.openalex_id,
        }
        return [name for name, value in checks.items() if not value]


# 「了解他」这一组字段的合并顺序：值越全的来源越优先
ENRICHABLE_FIELDS: tuple[str, ...] = (
    "title",
    "email",
    "research_directions",
    "research_areas",
    "publications",
    "homepage_url",
    "profile_text",
    "orcid",
    "openalex_id",
    "school_cate",
    "note",
)


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | dict | set):
        return len(value) == 0
    return False


def merge_into(target: Advisor, extra: Advisor, source: str) -> Advisor:
    """把 extra 里"target 还没有的字段"补进 target，并记录每个字段的来源。"""
    merged = target.model_copy(deep=True)

    for field in ENRICHABLE_FIELDS:
        current = getattr(merged, field)
        incoming = getattr(extra, field)
        if _is_empty(current) and not _is_empty(incoming):
            setattr(merged, field, incoming)
            merged.field_sources[field] = source

    for field in ("source_url", "retrieved_at", "content_hash"):
        current = getattr(merged, field)
        incoming = getattr(extra, field)
        source_placeholder = (
            field == "source_url" and incoming.startswith(("http://", "https://"))
            and not current.startswith(("http://", "https://"))
        )
        if (_is_empty(current) or source_placeholder) and not _is_empty(incoming):
            setattr(merged, field, getattr(extra, field))

    for item in extra.sources or [source]:
        if item not in merged.sources:
            merged.sources.append(item)
    if source not in merged.sources:
        merged.sources.append(source)

    return merged


def fill_rate(advisors: list[Advisor], field: str) -> float:
    if not advisors:
        return 0.0
    filled = sum(1 for item in advisors if not _is_empty(getattr(item, field)))
    return filled / len(advisors)
