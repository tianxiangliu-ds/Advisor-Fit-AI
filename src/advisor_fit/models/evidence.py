"""证据与声明（Claim）模型。

- Evidence 是证据账本的最小单位：每个来源片段带 URL、日期、摘录、来源等级与作者归属状态。
- Claim 必须引用至少一个存在的 evidence ID，除非其为 UNKNOWN 状态——这是防幻觉的第一道硬约束。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, model_validator

from advisor_fit.models.common import Confidence


class ClaimStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    CONFLICTED = "CONFLICTED"
    UNKNOWN = "UNKNOWN"


class Evidence(BaseModel):
    id: str
    source_type: str
    source_url: str | None = None
    canonical_id: str | None = None
    title: str | None = None
    published_date: str | None = None
    retrieved_at: str | None = None
    evidence_text: str = ""
    author_resolution_status: str = "CONFIRMED"  # CONFIRMED | UNCERTAIN | REJECTED
    source_tier: int = 2
    content_hash: str | None = None


class Claim(BaseModel):
    id: str
    subject_id: str | None = None
    text: str
    claim_type: str
    status: ClaimStatus = ClaimStatus.SUPPORTED
    confidence: Confidence = Confidence.MEDIUM
    generated_by: str = "rule_plus_llm"
    evidence_ids: list[str] = []
    checked_at: str | None = None

    @model_validator(mode="after")
    def require_evidence_unless_unknown(self) -> Claim:
        if self.status != ClaimStatus.UNKNOWN and not self.evidence_ids:
            raise ValueError("non-UNKNOWN claim must cite at least one evidence id")
        return self
