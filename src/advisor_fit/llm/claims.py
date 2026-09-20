"""受控声明生成：LLM 只消费已确认证据，生成后立即确定性校验并移除违规声明。"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.llm.prompts import prompt_text
from advisor_fit.models.evidence import Claim
from advisor_fit.validation.claims import validate_claims

_CLAIMS_INSTRUCTIONS = prompt_text("claims")


class ClaimsOutput(BaseModel):
    claims: list[Claim] = []


def generate_and_validate_claims(llm, payload: dict) -> list[Claim]:
    try:
        output = llm.generate(
            schema=ClaimsOutput, instructions=_CLAIMS_INSTRUCTIONS, payload=payload
        )
    except Exception:  # noqa: BLE001 - LLM 不可用时返回空，交由上层降级
        return []

    if not isinstance(output, ClaimsOutput):
        return []

    evidences = payload.get("evidences", {})
    result = validate_claims(output.claims, evidences)
    if result.ok:
        return output.claims

    invalid = {e.claim_id for e in result.errors if e.claim_id is not None}
    return [c for c in output.claims if c.id not in invalid]
