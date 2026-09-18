"""将用户已核实的导师与论文资料转换为统一证据模型。"""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from advisor_fit.models.common import SourceRecord
from advisor_fit.models.evidence import Evidence
from advisor_fit.models.professor import OfficialIdentityAnchor
from advisor_fit.providers.academic import Work


def _clean_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不得为空")
    return value


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _validate_http_url(value: str) -> str:
    value = _clean_required(value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("必须是 http 或 https 链接")
    return value


class ManualPaperInput(BaseModel):
    title: str = Field(min_length=1)
    year: int | None = Field(default=None, ge=1900, le=2100)
    abstract: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_platform: str = "其他"
    keywords: list[str] = []
    user_confirmed: bool = False

    _clean_title = field_validator("title")(_clean_required)
    _clean_abstract = field_validator("abstract")(_clean_required)
    _clean_source_url = field_validator("source_url")(_validate_http_url)

    @field_validator("source_platform")
    @classmethod
    def clean_platform(cls, value: str) -> str:
        return value.strip() or "其他"

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            value = value.strip()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned


class ManualProfessorInput(BaseModel):
    name: str = Field(min_length=1)
    institution: str = Field(min_length=1)
    department: str | None = None
    title: str | None = None
    homepage_url: str | None = None
    email: str | None = None
    declared_interests: list[str] = []
    identity_confirmed: bool = False
    papers: list[ManualPaperInput] = []

    _clean_name = field_validator("name")(_clean_required)
    _clean_institution = field_validator("institution")(_clean_required)
    _clean_department = field_validator("department")(_clean_optional)
    _clean_title = field_validator("title")(_clean_optional)
    _clean_email = field_validator("email")(_clean_optional)

    @field_validator("homepage_url")
    @classmethod
    def clean_homepage(cls, value: str | None) -> str | None:
        value = _clean_optional(value)
        return _validate_http_url(value) if value else None

    @field_validator("declared_interests")
    @classmethod
    def clean_interests(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            value = value.strip()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned


class ManualMaterials(BaseModel):
    anchor: OfficialIdentityAnchor
    works: list[Work]
    evidences: list[Evidence]
    sources: list[SourceRecord]


def _paper_digest(paper: ManualPaperInput) -> str:
    value = f"{paper.title}\n{paper.source_url}".encode()
    return hashlib.sha256(value).hexdigest()[:10]


def build_manual_materials(
    professor: ManualProfessorInput, *, id_prefix: str
) -> ManualMaterials:
    """只接纳用户明确确认的身份与论文，并生成可追溯证据。"""
    if not professor.identity_confirmed:
        raise ValueError("请先确认导师身份")

    confirmed = [paper for paper in professor.papers if paper.user_confirmed]
    if not confirmed:
        raise ValueError("请至少确认一篇论文")

    anchor = OfficialIdentityAnchor(
        name=professor.name,
        institution=professor.institution,
        department=professor.department,
        title=professor.title,
        email=professor.email,
        homepage=professor.homepage_url,
        declared_interests=professor.declared_interests,
    )

    profile_evidence_id = f"ev_{id_prefix}_profile"
    profile_source_id = f"src_{id_prefix}_profile"
    profile_text = "；".join(
        part
        for part in (
            professor.name,
            professor.institution,
            professor.department,
            professor.title,
            "、".join(professor.declared_interests),
        )
        if part
    )
    evidences = [
        Evidence(
            id=profile_evidence_id,
            source_type="user_confirmed_profile",
            source_url=professor.homepage_url,
            title=f"{professor.name}的导师资料",
            evidence_text=profile_text,
            author_resolution_status="CONFIRMED",
            source_tier=1 if professor.homepage_url else 3,
        )
    ]
    sources = [
        SourceRecord(
            id=profile_source_id,
            source_type="user_confirmed_profile",
            url=professor.homepage_url,
            title=f"{professor.name}的导师资料",
            text=profile_text,
        )
    ]
    works: list[Work] = []

    for index, paper in enumerate(confirmed, start=1):
        paper_id = f"manual_{id_prefix}_{index}_{_paper_digest(paper)}"
        evidence_id = f"ev_{paper_id}"
        source_id = f"src_{paper_id}"
        works.append(
            Work(
                id=paper_id,
                title=paper.title,
                year=paper.year,
                topics=paper.keywords,
                abstract=paper.abstract,
                source_url=paper.source_url,
                source_platform=paper.source_platform,
            )
        )
        evidences.append(
            Evidence(
                id=evidence_id,
                source_type="user_confirmed_paper",
                source_url=paper.source_url,
                title=paper.title,
                published_date=str(paper.year) if paper.year else None,
                evidence_text=paper.abstract,
                author_resolution_status="CONFIRMED",
                source_tier=2,
            )
        )
        sources.append(
            SourceRecord(
                id=source_id,
                source_type="user_confirmed_paper",
                url=paper.source_url,
                title=paper.title,
                text=paper.abstract,
            )
        )

    return ManualMaterials(anchor=anchor, works=works, evidences=evidences, sources=sources)
