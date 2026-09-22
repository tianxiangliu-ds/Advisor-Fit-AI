"""Task 7：导师画像组装测试。"""

from __future__ import annotations

from advisor_fit.analysis.professor_profile import assemble_professor_profile
from advisor_fit.models.evidence import Evidence
from advisor_fit.models.professor import OfficialIdentityAnchor
from advisor_fit.providers.academic import Work


def _anchor() -> OfficialIdentityAnchor:
    return OfficialIdentityAnchor(
        name="王伟",
        institution="武汉大学",
        department="计算机学院",
        title="教授",
        email="wangwei@university.example.edu",
        declared_interests=["信息检索"],
    )


def _works() -> list[Work]:
    return [
        Work(
            id="W1",
            title="RAG Survey",
            year=2026,
            topics=["检索增强生成"],
            authors=["王伟", "李四"],
        ),
        Work(id="W2", title="RAG Eval", year=2025, topics=["检索增强生成"]),
        Work(id="W3", title="RAG Robustness", year=2025, topics=["检索增强生成"]),
    ]


def _evidences() -> list[Evidence]:
    return [Evidence(id="ev_official", source_type="official_page", source_url="https://u.edu/faculty/wang")]


def test_non_unknown_professor_fields_have_evidence():
    profile = assemble_professor_profile(_anchor(), _works(), _evidences(), current_year=2026)
    fields = list(profile.iter_asserted_fields())
    assert fields, "应至少有一个已断言字段"
    for field in fields:
        assert field.evidence_ids, f"{field.key} 缺少证据"


def test_declared_and_observed_topics_are_separate():
    profile = assemble_professor_profile(_anchor(), _works(), _evidences(), current_year=2026)
    declared = [t.topic for t in profile.declared_interests]
    observed = [t.topic for t in profile.observed_recent_topics]
    assert "信息检索" in declared
    assert "检索增强生成" in observed


def test_recruiting_unknown_when_no_dated_statement():
    profile = assemble_professor_profile(_anchor(), _works(), _evidences(), current_year=2026)
    assert profile.recruiting.status == "UNKNOWN"


def test_recent_publications_keep_authors_for_collaboration_checks():
    profile = assemble_professor_profile(_anchor(), _works(), _evidences(), current_year=2026)

    assert profile.recent_publications[0].authors == ["王伟", "李四"]


def test_profile_uses_homepage_based_identity_key_when_available():
    anchor = _anchor().model_copy(update={"homepage": "https://u.edu/faculty/wang"})

    profile = assemble_professor_profile(anchor, _works(), _evidences(), current_year=2026)

    assert profile.professor_id.startswith("prof_")
    assert profile.professor_id != "王伟"
