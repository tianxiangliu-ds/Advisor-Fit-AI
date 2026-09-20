"""身份核对测试：多锚点判断，判断不了就请用户确认，绝不擅自认定。"""

from __future__ import annotations

from advisor_fit.analysis.identity import assess_identity, institutions_agree
from advisor_fit.models.resolution import ResolutionStatus


class FakeProfile:
    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "")
        self.institution = kwargs.get("institution", "")
        self.department = kwargs.get("department", "")
        self.title = kwargs.get("title", "")
        self.email = kwargs.get("email", "")
        self.declared_interests = kwargs.get("declared_interests", [])


# -- 机构比对 -----------------------------------------------------------------


def test_institutions_agree_handles_containment_and_suffix():
    assert institutions_agree("武汉大学信息管理学院", "武汉大学") is True
    assert institutions_agree("武汉大学", "武汉大学") is True
    assert institutions_agree("中国药科大学", "武汉大学") is False
    assert institutions_agree("", "武汉大学") is None
    assert institutions_agree("武汉大学", None) is None


# -- 判断档位 -----------------------------------------------------------------


def test_school_email_plus_matching_institution_is_confirmed():
    result = assess_identity(
        name="陆伟",
        institution="武汉大学",
        homepage_profile=FakeProfile(
            institution="武汉大学信息管理学院", email="luwei@whu.edu.cn"
        ),
    )

    assert result.status == ResolutionStatus.CONFIRMED
    assert result.score >= 2
    assert not result.conflicts


def test_conflicting_institution_asks_the_user_to_review():
    result = assess_identity(
        name="王伟",
        institution="武汉大学",
        homepage_profile=FakeProfile(institution="中国药科大学", email="wang@cpu.edu.cn"),
    )

    assert result.status == ResolutionStatus.REVIEW_REQUIRED
    assert result.conflicts
    assert any("所属机构" in item for item in result.conflicts)


def test_only_conflicts_is_rejected():
    result = assess_identity(
        name="王伟",
        institution="武汉大学",
        homepage_profile=FakeProfile(institution="中国药科大学", email="wang@gmail.com"),
    )

    assert result.status == ResolutionStatus.REJECTED


def test_no_signal_at_all_asks_for_review_with_reason():
    result = assess_identity(name="陆伟")

    assert result.status == ResolutionStatus.REVIEW_REQUIRED
    assert result.signals == []
    assert any("没有可用于核对的线索" in item for item in result.reasons)


# -- 其它锚点 -----------------------------------------------------------------


def test_paper_institution_supports_the_match():
    result = assess_identity(
        name="陆伟",
        institution="武汉大学",
        paper_institutions=["武汉大学信息管理学院", "武汉大学"],
    )

    assert result.get_signal("paper_institution").matched is True
    # 只有一条线索支持、且没有任何冲突：仍然请用户确认，不自动放行
    assert result.status == ResolutionStatus.REVIEW_REQUIRED
    assert result.score == 1


def test_paper_institution_conflict_is_a_conflict():
    result = assess_identity(
        name="王伟",
        institution="武汉大学",
        paper_institutions=["中国药科大学", "南京大学"],
    )

    assert result.get_signal("paper_institution").matched is False


def test_topic_continuity_supports_the_match():
    result = assess_identity(
        name="陆伟",
        institution="武汉大学",
        homepage_profile=FakeProfile(institution="武汉大学", email="luwei@whu.edu.cn"),
        known_directions=["信息检索", "数字人文"],
        paper_topics=["信息检索", "知识图谱"],
    )

    assert result.get_signal("topic_continuity").matched is True


def test_scholar_id_counts_as_support():
    result = assess_identity(
        name="陆伟",
        institution="武汉大学",
        homepage_profile=FakeProfile(institution="武汉大学", email="luwei@whu.edu.cn"),
        scholar_ids={"orcid": "0000-0002-1825-0097", "openalex": ""},
    )

    assert result.get_signal("scholar_id").matched is True
    assert result.status == ResolutionStatus.CONFIRMED


def test_unmatched_department_is_reported_but_does_not_reject_alone():
    result = assess_identity(
        name="陆伟",
        institution="武汉大学",
        department="计算机学院",
        homepage_profile=FakeProfile(
            institution="武汉大学", department="信息管理学院", email="luwei@whu.edu.cn"
        ),
    )

    assert result.status == ResolutionStatus.REVIEW_REQUIRED
    assert any("院系" in item for item in result.conflicts)


def test_result_is_serialisable():
    result = assess_identity(
        name="陆伟", institution="武汉大学", email="luwei@whu.edu.cn"
    )
    payload = result.model_dump(mode="json")

    assert payload["signals"][0]["key"] == "email_domain"
    assert payload["status"] in {"CONFIRMED", "REVIEW_REQUIRED", "REJECTED"}
