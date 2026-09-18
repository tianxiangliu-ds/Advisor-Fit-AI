"""导师画像、作者候选与身份锚点。

身份消歧原则：姓名只用于召回，绝不用于确认；确认需要强证据（官方 ORCID/DBLP/OpenAlex
链接、官方论文 DOI/题名重合、完整邮箱一致）或无冲突的中证据。
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum

from pydantic import BaseModel

from advisor_fit.models.common import Confidence, FactStatus


class RecruitingStatus(StrEnum):
    CONFIRMED_OPEN = "CONFIRMED_OPEN"
    HISTORICAL_SIGNAL = "HISTORICAL_SIGNAL"
    UNKNOWN = "UNKNOWN"
    CONFIRMED_CLOSED = "CONFIRMED_CLOSED"


class ExternalIds(BaseModel):
    openalex: str | None = None
    semantic_scholar: str | None = None
    dblp: str | None = None
    orcid: str | None = None


class OfficialIdentityAnchor(BaseModel):
    """从官方页抽取、经用户确认的身份锚点。"""

    name: str
    aliases: list[str] = []
    institution: str | None = None
    department: str | None = None
    title: str | None = None
    email: str | None = None
    email_domain: str | None = None
    homepage: str | None = None
    external_ids: ExternalIds = ExternalIds()
    official_dois: set[str] = set()
    official_paper_titles: list[str] = []
    declared_interests: list[str] = []


class AuthorCandidate(BaseModel):
    """学术 API 召回的候选作者。"""

    id: str
    name: str
    external_ids: ExternalIds = ExternalIds()
    affiliations: list[str] = []
    works_count: int | None = None
    dois: set[str] = set()
    topics: list[str] = []
    last_known_institution: str | None = None


class FactValue(BaseModel):
    """带证据引用的单值字段。status=FACT 时必须有 evidence_ids。"""

    value: str | int | None = None
    status: FactStatus = FactStatus.UNKNOWN
    evidence_ids: list[str] = []


class AssertedField(BaseModel):
    """iter_asserted_fields 产出的已断言字段。"""

    key: str
    value: str | int | None
    evidence_ids: list[str] = []


class ObservedTopic(BaseModel):
    topic: str
    window: str
    trend: str = "INSUFFICIENT_EVIDENCE"  # EMERGING | SUSTAINED | DECLINING | INSUFFICIENT_EVIDENCE
    confidence: Confidence = Confidence.MEDIUM
    evidence_ids: list[str] = []


class RecentPublication(BaseModel):
    id: str
    title: str
    year: int | None = None
    doi: str | None = None
    author_resolution_status: str = "CONFIRMED"
    source_ids: list[str] = []


class Recruiting(BaseModel):
    status: RecruitingStatus = RecruitingStatus.UNKNOWN
    degree_levels: list[str] = []
    statement: str | None = None
    evidence_ids: list[str] = []


class Team(BaseModel):
    public_roster_count: int | None = None
    actual_team_size: str = "UNKNOWN"  # 永不推断数字
    members: list[str] = []
    evidence_ids: list[str] = []


class Freshness(BaseModel):
    official_page_retrieved_at: str | None = None
    latest_confirmed_publication_date: str | None = None
    stale_warnings: list[str] = []


class ProfessorProfile(BaseModel):
    """组装后的导师画像：官方声明与论文观察分层。"""

    professor_id: str
    name: FactValue = FactValue()
    institution: FactValue = FactValue()
    department: FactValue = FactValue()
    title: FactValue = FactValue()
    email: FactValue = FactValue()
    homepage: str | None = None
    declared_interests: list[ObservedTopic] = []
    observed_recent_topics: list[ObservedTopic] = []
    recent_publications: list[RecentPublication] = []
    recruiting: Recruiting = Recruiting()
    team: Team = Team()
    freshness: Freshness = Freshness()
    sources: list[str] = []
    overall_confidence: Confidence = Confidence.MEDIUM

    def iter_asserted_fields(self) -> Iterator[AssertedField]:
        """产出所有已断言（FACT）的标量字段；每个都必须带 evidence_ids。"""
        for key in ("name", "institution", "department", "title", "email"):
            field = getattr(self, key)
            if field.status == FactStatus.FACT:
                yield AssertedField(key=key, value=field.value, evidence_ids=field.evidence_ids)
