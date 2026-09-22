"""多导师套磁碰撞风险：只用已保存的学院与论文作者证据。"""

from advisor_fit.analysis.outreach_collision import (
    AdvisorContactRecord,
    CollisionLevel,
    assess_contact_collision,
    assess_contact_group,
    normalise_department,
)
from advisor_fit.models.professor import RecentPublication


def _record(name: str, department: str, papers: list[RecentPublication]):
    return AdvisorContactRecord(
        run_id=name,
        name=name,
        institution="武汉大学",
        department=department,
        publications=papers,
    )


def _paper(title: str, authors: list[str]):
    return RecentPublication(id=title, title=title, authors=authors)


def test_direct_coauthors_are_high_risk_even_across_departments():
    left = _record("王教授", "计算机学院", [_paper("论文 A", ["王教授", "李教授"])])
    right = _record("李教授", "网络安全学院", [_paper("论文 B", ["李教授", "王教授"])])

    risk = assess_contact_collision(left, right)

    assert risk.level == CollisionLevel.HIGH
    assert "直接合著" in risk.reason


def test_same_department_without_collaboration_is_caution_not_claimed_collaboration():
    left = _record("王教授", "计算机学院", [_paper("论文 A", ["王教授", "甲"])])
    right = _record("李教授", "计算机学院", [_paper("论文 B", ["李教授", "乙"])])

    risk = assess_contact_collision(left, right)

    assert risk.level == CollisionLevel.CAUTION
    assert "同一学院" in risk.reason
    assert "未发现直接合著" in risk.reason


def test_different_departments_with_author_data_have_no_obvious_collision():
    left = _record("王教授", "计算机学院", [_paper("论文 A", ["王教授", "甲"])])
    right = _record("李教授", "网络安全学院", [_paper("论文 B", ["李教授", "乙"])])

    risk = assess_contact_collision(left, right)

    assert risk.level == CollisionLevel.LOW
    assert "暂未发现" in risk.reason


def test_missing_authors_stays_unknown_instead_of_being_called_safe():
    left = _record("王教授", "计算机学院", [_paper("论文 A", [])])
    right = _record("李教授", "网络安全学院", [_paper("论文 B", [])])

    risk = assess_contact_collision(left, right)

    assert risk.level == CollisionLevel.UNKNOWN
    assert "作者数据不足" in risk.reason


def test_normalise_department_groups_college_and_its_subdepartments():
    assert normalise_department("信息管理学院大数据管理与保密系") == "信息管理学院"
    assert normalise_department("信息管理学院信息管理科学系") == "信息管理学院"


def test_group_assessment_returns_one_overall_recommendation():
    records = [
        _record("甲教授", "信息管理学院大数据管理与保密系", [_paper("A", ["甲教授", "甲同事"])]),
        _record("乙教授", "信息管理学院信息管理科学系", [_paper("B", ["乙教授", "乙同事"])]),
        _record("丙教授", "计算机学院", [_paper("C", ["丙教授", "丙同事"])]),
    ]

    assessment = assess_contact_group(records)

    assert assessment.level == CollisionLevel.CAUTION
    assert "3 位导师" in assessment.reason
    assert "信息管理学院" in assessment.reason
