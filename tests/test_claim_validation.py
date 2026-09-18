"""Task 9：Claim 校验器与受控生成测试。"""

from __future__ import annotations

from advisor_fit.llm.claims import ClaimsOutput, generate_and_validate_claims
from advisor_fit.models.evidence import Claim, ClaimStatus, Evidence
from advisor_fit.validation.claims import ValidationResult, validate_claims


def test_claim_with_unknown_evidence_id_is_rejected():
    claim = Claim(
        id="c1",
        text="导师转向大模型",
        claim_type="trend",
        status=ClaimStatus.SUPPORTED,
        evidence_ids=["missing"],
    )
    result = validate_claims([claim], {})
    assert not result.ok
    assert result.errors[0].code == "UNKNOWN_EVIDENCE_ID"


def _recruiting_claim(text: str, ev_ids: list[str]) -> Claim:
    return Claim(
        id="c1",
        text=text,
        claim_type="recruiting",
        status=ClaimStatus.SUPPORTED,
        evidence_ids=ev_ids,
    )


def _evidence_map() -> dict[str, Evidence]:
    return {
        "ev_no_recruiting": Evidence(
            id="ev_no_recruiting",
            source_type="official_page",
            evidence_text="未发现公开招生信息",
        )
    }


def test_unknown_recruiting_cannot_become_open():
    result = validate_claims([_recruiting_claim("正在招生", ["ev_no_recruiting"])], _evidence_map())
    assert not result.ok


class FakeLLM:
    def generate(self, **_kwargs):
        return ClaimsOutput(
            claims=[
                Claim(
                    id="c_halluc",
                    text="导师目前招收 6 名博士",
                    claim_type="team_size",
                    status=ClaimStatus.SUPPORTED,
                    evidence_ids=["ev_profile"],
                )
            ]
        )


def _safe_payload() -> dict:
    return {
        "evidences": {
            "ev_profile": Evidence(
                id="ev_profile", source_type="official_page", evidence_text="..."
            )
        }
    }


def test_hallucinated_team_size_is_removed():
    claims = generate_and_validate_claims(FakeLLM(), _safe_payload())
    assert claims == []


def test_valid_unknown_claim_is_accepted():
    result = validate_claims(
        [
            Claim(
                id="c2",
                text="未发现公开招生信息",
                claim_type="recruiting",
                status=ClaimStatus.UNKNOWN,
            )
        ],
        {},
    )
    assert result.ok


def test_validation_result_shape():
    result = validate_claims([], {})
    assert isinstance(result, ValidationResult)
    assert result.ok
