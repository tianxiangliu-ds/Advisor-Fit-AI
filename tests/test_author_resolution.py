"""Task 6：作者身份消歧与人工确认门控测试。"""

from __future__ import annotations

import json

from advisor_fit.models.professor import AuthorCandidate, ExternalIds, OfficialIdentityAnchor
from advisor_fit.resolution.author import (
    ResolutionCase,
    ResolutionStatus,
    evaluate_resolution,
    resolve_author,
)


def test_same_name_only_requires_manual_review():
    result = resolve_author(
        OfficialIdentityAnchor(name="王伟"),
        [AuthorCandidate(id="A1", name="王伟")],
    )
    assert result.status == ResolutionStatus.REVIEW_REQUIRED
    assert result.selected_author_id is None


def test_exact_official_doi_overlap_can_confirm_without_conflict():
    result = resolve_author(
        OfficialIdentityAnchor(name="王伟", official_dois={"10.1/a"}),
        [AuthorCandidate(id="A1", name="王伟", dois={"10.1/a"}, affiliations=["武汉大学"])],
    )
    assert result.status == ResolutionStatus.CONFIRMED
    assert result.selected_author_id == "A1"


def test_two_medium_evidence_still_requires_review():
    anchor = OfficialIdentityAnchor(
        name="王伟", institution="武汉大学", official_collaborators=["甲", "乙"]
    )
    cand = AuthorCandidate(
        id="A1", name="Wei Wang", affiliations=["武汉大学"], collaborators=["甲", "乙"]
    )
    result = resolve_author(anchor, [cand])
    assert result.status == ResolutionStatus.REVIEW_REQUIRED


def test_conflicting_orcid_is_rejected():
    anchor = OfficialIdentityAnchor(
        name="王伟", external_ids=ExternalIds(orcid="0000-0000-0000-0001")
    )
    cand = AuthorCandidate(
        id="A1", name="王伟", external_ids=ExternalIds(orcid="0000-0000-0000-0002")
    )
    result = resolve_author(anchor, [cand])
    assert result.status == ResolutionStatus.REJECTED
    assert result.selected_author_id is None


def test_golden_precision_at_least_95(fixtures_dir):
    raw = json.loads(
        (fixtures_dir / "author_resolution_cases.json").read_text(encoding="utf-8")
    )
    cases = [ResolutionCase(**case) for case in raw]
    metrics = evaluate_resolution(cases)
    assert metrics["auto_precision"] >= 0.95
