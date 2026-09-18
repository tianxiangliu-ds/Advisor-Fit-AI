"""金标准评测脚本。

输出：作者消歧 auto precision/coverage、人工复核率、claim 证据覆盖与未支持声明数、草稿覆盖。
任一发布闸门（G1 身份精度 / G2 证据覆盖 / G3 学生事实 / G4 降级）不通过时退出码为 1。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from advisor_fit.models.common import FactStatus
from advisor_fit.models.evidence import Claim, Evidence
from advisor_fit.models.match import Draft, DraftSentence, SentenceType
from advisor_fit.models.student import StudentFact, StudentProfile
from advisor_fit.resolution.author import ResolutionCase, evaluate_resolution
from advisor_fit.validation.claims import validate_claims
from advisor_fit.validation.draft import validate_draft

ROOT = Path(__file__).parent.parent


def _load_author_cases() -> list[ResolutionCase]:
    path = ROOT / "tests" / "fixtures" / "author_resolution_cases.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ResolutionCase(**case) for case in raw]


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
    evidences = {"ev1": Evidence(id="ev1", source_type="openalex", title="RAG Survey")}
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
    metrics = evaluate_resolution(_load_author_cases())
    auto_precision = metrics["auto_precision"]
    auto_coverage = metrics["auto_coverage"]
    manual_review_rate = round(1 - auto_coverage, 3)

    claims_ok, unsupported = _eval_claims()
    draft = _eval_draft()

    g1 = auto_precision >= 0.95
    g2 = claims_ok
    g3 = unsupported == 0
    g4 = (
        draft["draft_professor_fact_coverage"] >= 1.0
        and draft["draft_student_fact_coverage"] >= 1.0
    )

    report = {
        "author_auto_precision": round(auto_precision, 3),
        "author_auto_coverage": round(auto_coverage, 3),
        "manual_review_rate": manual_review_rate,
        "unsupported_claim_count": unsupported,
        **draft,
        "gates": {"g1_identity": g1, "g2_evidence": g2, "g3_student_facts": g3, "g4_degrade": g4},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if (g1 and g2 and g3 and g4) else 1


if __name__ == "__main__":
    sys.exit(main())
