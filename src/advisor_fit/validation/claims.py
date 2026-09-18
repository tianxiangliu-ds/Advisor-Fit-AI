"""Claim 确定性校验器。

规则：
- 引用必须指向存在的 evidence ID；
- 招生 / 团队字段只能为 UNKNOWN（MVP 没有带日期的招生证据，也绝不推断团队规模）；
- 趋势类声明的论文证据作者归属必须为 CONFIRMED。
任何失败返回错误码并从结果中移除该 claim。
"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.models.evidence import Claim, ClaimStatus, Evidence

_AFFIRMATIVE_BLOCKED_TYPES = {"recruiting", "team", "team_size"}
_TREND_TYPES = {"observed_research_trend", "trend", "research_trend"}


class ValidationError(BaseModel):
    code: str
    message: str
    claim_id: str | None = None


class ValidationResult(BaseModel):
    ok: bool = True
    errors: list[ValidationError] = []


def validate_claims(
    claims: list[Claim], evidences: dict[str, Evidence]
) -> ValidationResult:
    errors: list[ValidationError] = []
    for claim in claims:
        for ev_id in claim.evidence_ids:
            if ev_id not in evidences:
                errors.append(
                    ValidationError(
                        code="UNKNOWN_EVIDENCE_ID",
                        message=f"引用不存在的证据 {ev_id}",
                        claim_id=claim.id,
                    )
                )

        if claim.claim_type in _AFFIRMATIVE_BLOCKED_TYPES and claim.status != ClaimStatus.UNKNOWN:
            errors.append(
                ValidationError(
                    code="AFFIRMATIVE_UNKNOWN_FIELD",
                    message=f"{claim.claim_type} 只能为 UNKNOWN，不能作肯定陈述",
                    claim_id=claim.id,
                )
            )

        if claim.claim_type in _TREND_TYPES and claim.status == ClaimStatus.SUPPORTED:
            for ev_id in claim.evidence_ids:
                evidence = evidences.get(ev_id)
                if evidence is not None and evidence.author_resolution_status != "CONFIRMED":
                    errors.append(
                        ValidationError(
                            code="AUTHOR_NOT_CONFIRMED",
                            message=f"证据 {ev_id} 作者归属未确认，不能支持趋势声明",
                            claim_id=claim.id,
                        )
                    )

    return ValidationResult(ok=not errors, errors=errors)
