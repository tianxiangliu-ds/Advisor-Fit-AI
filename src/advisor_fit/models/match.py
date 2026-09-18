"""匹配报告与邮件草稿模型。

匹配结论使用四档建议，不含伪精确百分比（因此 MatchReport 明确不提供 match_percentage 字段）。
邮件草稿的每一句都显式声明类型：PROFESSOR_FACT / STUDENT_FACT / GENERIC，
并分别绑定 evidence_ids / fact_ids。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class FitLevel(StrEnum):
    STRONG = "STRONG"
    PARTIAL = "PARTIAL"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class Recommendation(StrEnum):
    WORTH_CONTACTING = "WORTH_CONTACTING"
    LEARN_MORE = "LEARN_MORE"
    LOW_PRIORITY = "LOW_PRIORITY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class MatchDimension(BaseModel):
    key: str
    label: str
    level: FitLevel
    summary: str = ""
    student_fact_ids: list[str] = []
    professor_evidence_ids: list[str] = []


class MatchReport(BaseModel):
    recommendation: Recommendation = Recommendation.INSUFFICIENT_EVIDENCE
    research_fit: FitLevel = FitLevel.UNKNOWN
    opportunity_signal: str = "UNKNOWN"  # 招生信号独立于学术匹配
    dimensions: list[MatchDimension] = []
    strengths: list[MatchDimension] = []
    weaknesses: list[MatchDimension] = []
    gaps: list[str] = []
    questions_to_ask: list[str] = []
    evidence_sufficiency: str = "LOW"  # HIGH | MEDIUM | LOW


class SentenceType(StrEnum):
    PROFESSOR_FACT = "PROFESSOR_FACT"
    STUDENT_FACT = "STUDENT_FACT"
    GENERIC = "GENERIC"


class DraftSentence(BaseModel):
    text: str
    sentence_type: SentenceType
    fact_ids: list[str] = []
    evidence_ids: list[str] = []


class Draft(BaseModel):
    subject: str = ""
    sentences: list[DraftSentence] = []
    warnings: list[str] = []
