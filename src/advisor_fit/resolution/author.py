"""作者身份消歧。

原则：姓名只用于召回，绝不用于确认。

- 强证据：官方 ORCID/DBLP/OpenAlex 链接一致、官方论文 DOI/题名重合、完整邮箱一致；
- 中证据：当前/历史机构一致、两个以上稳定合作者重合；
- 弱证据：研究主题相似；
- 硬冲突：ORCID/DBLP/OpenAlex 同时存在但指向不同的人。

自动 CONFIRMED 只允许「至少一个强证据且无硬冲突」；其余进入 REVIEW_REQUIRED 或 REJECTED。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from advisor_fit.models.professor import AuthorCandidate, OfficialIdentityAnchor


class ResolutionStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class ResolutionResult(BaseModel):
    status: ResolutionStatus = ResolutionStatus.REVIEW_REQUIRED
    ranked_candidates: list[AuthorCandidate] = []
    selected_author_id: str | None = None
    reasons: list[str] = []
    conflicts: list[str] = []
    confirmed_by: str | None = None  # "auto" | "user"


class ResolutionCase(BaseModel):
    anchor: OfficialIdentityAnchor
    candidates: list[AuthorCandidate]
    expected_author_id: str | None = None


def _contains(a: str, b: str) -> bool:
    return a in b or b in a


def _topic_overlap(interests: list[str], topics: list[str]) -> bool:
    return any(_contains(i, t) for i in interests for t in topics)


def _evidence(
    anchor: OfficialIdentityAnchor, cand: AuthorCandidate
) -> tuple[list[str], list[str], list[str], list[str]]:
    strong: list[str] = []
    medium: list[str] = []
    weak: list[str] = []
    conflicts: list[str] = []

    a_ext, c_ext = anchor.external_ids, cand.external_ids
    if a_ext.orcid and c_ext.orcid:
        (strong if a_ext.orcid == c_ext.orcid else conflicts).append(
            "orcid_match" if a_ext.orcid == c_ext.orcid else "orcid_conflict"
        )
    if a_ext.dblp and c_ext.dblp:
        if a_ext.dblp == c_ext.dblp:
            strong.append("dblp_match")
        else:
            conflicts.append("dblp_conflict")
    if a_ext.openalex and c_ext.openalex:
        if a_ext.openalex == c_ext.openalex:
            strong.append("openalex_match")
        else:
            conflicts.append("openalex_conflict")

    if anchor.official_dois and cand.dois and anchor.official_dois & cand.dois:
        strong.append("official_doi_overlap")

    if anchor.institution:
        aff_match = any(_contains(anchor.institution, aff) for aff in cand.affiliations)
        last_known_match = cand.last_known_institution and _contains(
            anchor.institution, cand.last_known_institution
        )
        if aff_match or last_known_match:
            medium.append("affiliation_match")

    if anchor.official_collaborators and cand.collaborators:
        overlap = set(anchor.official_collaborators) & set(cand.collaborators)
        if len(overlap) >= 2:
            medium.append("collaborator_overlap")

    if anchor.declared_interests and cand.topics and _topic_overlap(
        anchor.declared_interests, cand.topics
    ):
        weak.append("topic_overlap")

    return strong, medium, weak, conflicts


def resolve_author(
    anchor: OfficialIdentityAnchor, candidates: list[AuthorCandidate]
) -> ResolutionResult:
    if not anchor.name:
        return ResolutionResult(status=ResolutionStatus.REJECTED, conflicts=["missing_name"])

    scored: list[tuple[AuthorCandidate, list, list, list, list]] = []
    for cand in candidates:
        strong, medium, weak, conflicts = _evidence(anchor, cand)
        scored.append((cand, strong, medium, weak, conflicts))

    scored.sort(key=lambda item: (len(item[1]), len(item[2]), len(item[3])), reverse=True)
    ranked = [item[0] for item in scored]

    if not scored:
        return ResolutionResult(
            status=ResolutionStatus.REVIEW_REQUIRED, conflicts=["no_candidates"]
        )

    cand, strong, medium, weak, conflicts = scored[0]

    if strong and not conflicts:
        return ResolutionResult(
            status=ResolutionStatus.CONFIRMED,
            ranked_candidates=ranked,
            selected_author_id=cand.id,
            reasons=[*strong, *medium],
            conflicts=[],
            confirmed_by="auto",
        )

    if conflicts and not strong:
        return ResolutionResult(
            status=ResolutionStatus.REJECTED,
            ranked_candidates=ranked,
            selected_author_id=None,
            reasons=[*medium, *weak],
            conflicts=conflicts,
        )

    return ResolutionResult(
        status=ResolutionStatus.REVIEW_REQUIRED,
        ranked_candidates=ranked,
        selected_author_id=None,
        reasons=[*medium, *weak],
        conflicts=conflicts,
    )


def evaluate_resolution(cases: list[ResolutionCase]) -> dict[str, float]:
    results = [resolve_author(case.anchor, case.candidates) for case in cases]
    auto = [
        pair
        for pair in zip(cases, results, strict=True)
        if pair[1].status == ResolutionStatus.CONFIRMED
    ]
    correct = sum(
        1 for case, result in auto if result.selected_author_id == case.expected_author_id
    )
    confirmable = sum(1 for case in cases if case.expected_author_id is not None)
    return {
        "auto_precision": correct / len(auto) if auto else 1.0,
        "auto_coverage": len(auto) / confirmable if confirmable else 1.0,
    }
