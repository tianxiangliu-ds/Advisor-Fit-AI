"""金标准评测脚本。

输出：claim 证据覆盖与未支持声明数、草稿 provenance coverage。
任一发布闸门（G1 证据覆盖 / G2 降级）不通过时退出码为 1。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from advisor_fit.models.common import FactStatus
from advisor_fit.models.evidence import Claim, Evidence
from advisor_fit.models.match import Draft, DraftSentence, SentenceType
from advisor_fit.models.student import StudentFact, StudentProfile
from advisor_fit.validation.claims import validate_claims
from advisor_fit.validation.draft import validate_draft

ROOT = Path(__file__).parent.parent


def _eval_claims() -> tuple[bool, int]:
    path = ROOT / "evals" / "gold_claims.jsonl"
    unsupported = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        claim = Claim(**case["claim"])
        evidences = {e["id"]: Evidence(**e) for e in case["evidences"]}
        result = validate_claims([claim], evidences)
        if bool(result.ok) != bool(case["expected_valid"]):
            unsupported += 1
    return unsupported == 0, unsupported


def _eval_draft() -> dict[str, float]:
    student = StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(
                id="f1",
                field="skill",
                value="Python",
                status=FactStatus.FACT,
                source_id="cv",
                user_confirmed=True,
            )
        ],
    )
    evidences = {"ev1": Evidence(id="ev1", source_type="user_confirmed_paper", title="RAG Survey")}
    draft = Draft(
        sentences=[
            DraftSentence(
                text="您的团队近期研究检索增强生成。",
                sentence_type=SentenceType.PROFESSOR_FACT,
                evidence_ids=["ev1"],
            ),
            DraftSentence(
                text="我在课程项目中实践过相关技术。",
                sentence_type=SentenceType.STUDENT_FACT,
                fact_ids=["f1"],
            ),
            DraftSentence(text="请问您是否有招生名额？", sentence_type=SentenceType.GENERIC),
        ]
    )
    result = validate_draft(draft, student, evidences)
    return {
        "draft_professor_fact_coverage": result.professor_fact_coverage,
        "draft_student_fact_coverage": result.student_fact_coverage,
    }


def main() -> int:
    claims_ok, unsupported = _eval_claims()
    draft = _eval_draft()

    g1 = claims_ok
    g2 = (
        draft["draft_professor_fact_coverage"] >= 1.0
        and draft["draft_student_fact_coverage"] >= 1.0
    )

    report = {
        "unsupported_claim_count": unsupported,
        **draft,
        "gates": {"g1_evidence": g1, "g2_degrade": g2},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if (g1 and g2) else 1


if __name__ == "__main__":
    sys.exit(main())
