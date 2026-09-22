"""多导师联系碰撞提醒。

只使用已保存的学校、学院和论文作者；缺数据时明确返回 UNKNOWN。
"""

from __future__ import annotations

import re
from collections import Counter
from enum import StrEnum

from pydantic import BaseModel

from advisor_fit.models.professor import RecentPublication


class CollisionLevel(StrEnum):
    HIGH = "HIGH"
    CAUTION = "CAUTION"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class AdvisorContactRecord(BaseModel):
    run_id: str
    name: str
    institution: str = ""
    department: str = ""
    publications: list[RecentPublication] = []


class CollisionAssessment(BaseModel):
    left_run_id: str
    right_run_id: str
    level: CollisionLevel
    reason: str
    evidence: list[str] = []


class ContactGroupAssessment(BaseModel):
    """One decision-oriented contact recommendation for a selected group."""

    level: CollisionLevel
    reason: str
    high_risk_pairs: list[CollisionAssessment] = []


def _normalise(value: str | None) -> str:
    return re.sub(r"[\s·・,，;；]+", "", str(value or "")).casefold()


def normalise_department(value: str | None) -> str:
    """Use a parent college for comparison while retaining raw detail elsewhere."""
    text = re.sub(r"\s+", "", str(value or "")).strip()
    marker = text.find("学院")
    if marker >= 0:
        return text[:marker + len("学院")]
    return text


def _paper_keys(paper: RecentPublication) -> set[str]:
    keys = {_normalise(paper.title)} if paper.title else set()
    if paper.doi:
        keys.add(_normalise(paper.doi))
    if paper.source_url:
        keys.add(_normalise(paper.source_url))
    return {key for key in keys if key}


def _coauthor_counts(record: AdvisorContactRecord) -> Counter[str]:
    own_name = _normalise(record.name)
    counts: Counter[str] = Counter()
    for paper in record.publications:
        for author in {_normalise(author) for author in paper.authors}:
            if author and author != own_name:
                counts[author] += 1
    return counts


def _shared_papers(
    left: AdvisorContactRecord, right: AdvisorContactRecord
) -> list[str]:
    right_keys = {
        key
        for paper in right.publications
        for key in _paper_keys(paper)
    }
    return [
        paper.title
        for paper in left.publications
        if _paper_keys(paper) & right_keys
    ]


def assess_contact_collision(
    left: AdvisorContactRecord, right: AdvisorContactRecord
) -> CollisionAssessment:
    """判断两份已完成档案的联系碰撞风险，不作安全承诺。"""
    same_institution = bool(
        _normalise(left.institution)
        and _normalise(left.institution) == _normalise(right.institution)
    )
    same_department = bool(
        same_institution
        and _normalise(normalise_department(left.department))
        and _normalise(normalise_department(left.department))
        == _normalise(normalise_department(right.department))
    )
    left_counts = _coauthor_counts(left)
    right_counts = _coauthor_counts(right)
    left_name = _normalise(left.name)
    right_name = _normalise(right.name)
    direct = right_name in left_counts or left_name in right_counts
    shared_papers = _shared_papers(left, right)
    stable_overlap = sorted(
        name
        for name in set(left_counts) & set(right_counts)
        if left_counts[name] >= 2 and right_counts[name] >= 2
    )
    author_data_available = any(
        paper.authors for paper in [*left.publications, *right.publications]
    )

    evidence: list[str] = []
    if shared_papers:
        evidence.append("共同论文：" + "、".join(shared_papers[:3]))
    if direct:
        evidence.append("论文作者列表显示两位导师直接合著")
    if stable_overlap:
        evidence.append(
            "双方都多次合作的共同作者：" + "、".join(stable_overlap[:5])
        )

    if direct or shared_papers or (same_department and stable_overlap):
        return CollisionAssessment(
            left_run_id=left.run_id,
            right_run_id=right.run_id,
            level=CollisionLevel.HIGH,
            reason=(
                "已发现直接合著或明确的共同论文，建议错开联系时间，"
                "并确保每封邮件的研究切入点不同。"
            ),
            evidence=evidence,
        )
    if same_department:
        return CollisionAssessment(
            left_run_id=left.run_id,
            right_run_id=right.run_id,
            level=CollisionLevel.CAUTION,
            reason=(
                "两位导师在同一学院；当前已核验论文中未发现直接合著，"
                "但仍建议错开发送并分别定制内容。"
            ),
        )
    if stable_overlap:
        return CollisionAssessment(
            left_run_id=left.run_id,
            right_run_id=right.run_id,
            level=CollisionLevel.CAUTION,
            reason=(
                "两位导师虽不在同一学院，但出现稳定共同合作者，"
                "建议避免在短时间内发送高度相似的邮件。"
            ),
            evidence=evidence,
        )
    if author_data_available and left.department and right.department:
        return CollisionAssessment(
            left_run_id=left.run_id,
            right_run_id=right.run_id,
            level=CollisionLevel.LOW,
            reason=(
                "两位导师属于不同学院，且在当前已核验论文中暂未发现"
                "直接合著或稳定共同合作者；这不等于完全没有私下联系。"
            ),
        )
    return CollisionAssessment(
        left_run_id=left.run_id,
        right_run_id=right.run_id,
        level=CollisionLevel.UNKNOWN,
        reason=(
            "学院或论文作者数据不足，暂时无法判断两位导师是否存在套磁碰撞风险。"
        ),
    )


def assess_contact_group(records: list[AdvisorContactRecord]) -> ContactGroupAssessment:
    """Collapse pairwise evidence into one recommendation for 2–5 advisors."""
    if len(records) < 2:
        return ContactGroupAssessment(
            level=CollisionLevel.UNKNOWN,
            reason="至少选择两位导师后，才能判断联系安排。",
        )
    pairs = [
        assess_contact_collision(left, right)
        for index, left in enumerate(records)
        for right in records[index + 1:]
    ]
    high = [assessment for assessment in pairs if assessment.level == CollisionLevel.HIGH]
    if high:
        return ContactGroupAssessment(
            level=CollisionLevel.HIGH,
            reason=(
                f"所选 {len(records)} 位导师中发现 {len(high)} 组直接合作或共同论文线索；"
                "建议错开发送时间，并为每封邮件选择不同的研究切入点。"
            ),
            high_risk_pairs=high,
        )
    college_counts = Counter(
        normalise_department(record.department)
        for record in records
        if normalise_department(record.department)
    )
    repeated_colleges = sorted(
        college for college, count in college_counts.items() if count >= 2
    )
    if repeated_colleges or any(pair.level == CollisionLevel.CAUTION for pair in pairs):
        college_text = "、".join(repeated_colleges[:3]) or "存在共同合作线索的院系"
        return ContactGroupAssessment(
            level=CollisionLevel.CAUTION,
            reason=(
                f"所选 {len(records)} 位导师中，有多人归在 {college_text}；"
                "建议分批联系，并让每封邮件的研究问题保持明显不同。"
            ),
        )
    if all(pair.level == CollisionLevel.LOW for pair in pairs):
        return ContactGroupAssessment(
            level=CollisionLevel.LOW,
            reason=(
                f"所选 {len(records)} 位导师目前分属不同学院，已核验论文中未发现明显合作线索；"
                "可以考虑分批联系，但这不等于不存在私下联系。"
            ),
        )
    return ContactGroupAssessment(
        level=CollisionLevel.UNKNOWN,
        reason=(
            f"所选 {len(records)} 位导师的学院或作者数据仍不完整，"
            "暂时无法给出可靠的整体联系安排。"
        ),
    )
