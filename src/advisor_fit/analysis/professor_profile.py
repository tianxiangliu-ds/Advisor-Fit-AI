"""组装导师画像：官方声明与论文观察分层。"""

from __future__ import annotations

from datetime import UTC, datetime

from advisor_fit.analysis.topics import group_works_by_topic, infer_trend
from advisor_fit.models.common import Confidence, FactStatus
from advisor_fit.models.evidence import Evidence
from advisor_fit.models.professor import (
    FactValue,
    Freshness,
    ObservedTopic,
    OfficialIdentityAnchor,
    ProfessorProfile,
    RecentPublication,
    Recruiting,
    RecruitingStatus,
    Team,
)
from advisor_fit.providers.academic import Work


def _official_evidence_id(evidences: list[Evidence]) -> str:
    for evidence in evidences:
        if evidence.source_type in {"official_page", "user_confirmed_profile"}:
            return evidence.id
    return "ev_official_profile"


def _fact(value: str | None, evidence_id: str) -> FactValue:
    if not value:
        return FactValue()
    return FactValue(value=value, status=FactStatus.FACT, evidence_ids=[evidence_id])


def assemble_professor_profile(
    anchor: OfficialIdentityAnchor,
    works: list[Work],
    evidences: list[Evidence],
    current_year: int | None = None,
) -> ProfessorProfile:
    current_year = current_year or datetime.now(UTC).year
    official_ev = _official_evidence_id(evidences)

    profile = ProfessorProfile(
        professor_id=anchor.name or "professor",
        identity_confirmed=True,
        name=_fact(anchor.name, official_ev),
        institution=_fact(anchor.institution, official_ev),
        department=_fact(anchor.department, official_ev),
        title=_fact(anchor.title, official_ev),
        email=_fact(anchor.email, official_ev),
        homepage=anchor.homepage,
    )

    # declared：官网明确声明的研究方向
    for interest in anchor.declared_interests:
        profile.declared_interests.append(
            ObservedTopic(topic=interest, trend="DECLARED", evidence_ids=[official_ev])
        )

    # observed：从已确认论文观察到的主题（满足证据门槛才输出趋势）
    for topic, cluster in group_works_by_topic(works).items():
        trend = infer_trend(cluster, current_year, topic=topic)
        if trend.status != "INSUFFICIENT_EVIDENCE":
            profile.observed_recent_topics.append(
                ObservedTopic(
                    topic=trend.topic,
                    window=trend.window,
                    trend=trend.status,
                    confidence=Confidence.MEDIUM,
                    evidence_ids=trend.evidence_ids,
                )
            )

    for work in works:
        profile.recent_publications.append(
            RecentPublication(
                id=work.id,
                title=work.title,
                year=work.year,
                doi=work.doi,
                author_resolution_status="CONFIRMED",
                source_ids=[f"ev_{work.id}"],
                abstract=work.abstract,
                source_url=work.source_url,
                keywords=work.topics,
            )
        )

    # 招生：无带日期声明 → UNKNOWN；团队：只存公开名单数，不推断真实规模
    profile.recruiting = Recruiting(status=RecruitingStatus.UNKNOWN)
    profile.team = Team()

    latest = max((w.year for w in works if w.year is not None), default=None)
    profile.freshness = Freshness(
        latest_confirmed_publication_date=str(latest) if latest else None
    )
    profile.sources = [official_ev, *[f"ev_{work.id}" for work in works]]
    profile.overall_confidence = Confidence.MEDIUM

    return profile
