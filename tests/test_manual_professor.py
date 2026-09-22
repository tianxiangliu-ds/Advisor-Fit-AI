"""手动导师资料输入：验证、证据转换与来源追踪。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from advisor_fit.ingest.manual_professor import (
    ManualPaperInput,
    ManualProfessorInput,
    build_manual_materials,
    professor_identity_key,
    validate_paper_values,
)


def _paper(**overrides) -> ManualPaperInput:
    values = {
        "title": "Knowledge Graphs for Digital Humanities",
        "year": 2025,
        "abstract": "We construct a knowledge graph for cultural heritage collections.",
        "source_url": "https://doi.org/10.1000/example",
        "source_platform": "DOI",
        "keywords": ["知识图谱", "数字人文"],
        "authors": ["王老师", "李同学"],
        "user_confirmed": True,
    }
    values.update(overrides)
    return ManualPaperInput(**values)


def test_manual_paper_requires_title_abstract_and_http_source():
    with pytest.raises(ValidationError):
        ManualPaperInput(
            title="",
            abstract="",
            source_url="not-a-url",
            user_confirmed=True,
        )


def test_unconfirmed_papers_do_not_become_evidence():
    professor = ManualProfessorInput(
        name="王老师",
        institution="武汉大学",
        identity_confirmed=True,
        papers=[_paper(user_confirmed=False), _paper(title="Confirmed paper")],
    )

    materials = build_manual_materials(professor, id_prefix="run123")

    assert len(materials.works) == 1
    assert materials.works[0].title == "Confirmed paper"
    assert len([e for e in materials.evidences if e.source_type == "出版社/DOI"]) == 1


def test_manual_materials_preserve_abstract_link_and_keywords():
    professor = ManualProfessorInput(
        name="王老师",
        institution="武汉大学",
        homepage_url="https://sim.whu.edu.cn/teacher/wang",
        declared_interests=["数字人文"],
        identity_confirmed=True,
        papers=[_paper()],
    )

    materials = build_manual_materials(professor, id_prefix="run123")
    paper = materials.works[0]
    evidence = next(e for e in materials.evidences if e.source_type == "出版社/DOI")

    assert materials.anchor.name == "王老师"
    assert materials.anchor.institution == "武汉大学"
    assert paper.abstract.startswith("We construct")
    assert paper.source_url == "https://doi.org/10.1000/example"
    assert paper.topics == ["知识图谱", "数字人文"]
    assert paper.authors == ["王老师", "李同学"]
    assert evidence.evidence_text == paper.abstract
    assert evidence.source_url == paper.source_url
    assert evidence.author_resolution_status == "CONFIRMED"


def test_manual_materials_require_confirmed_identity_and_one_confirmed_paper():
    unconfirmed_identity = ManualProfessorInput(
        name="王老师",
        institution="武汉大学",
        identity_confirmed=False,
        papers=[_paper()],
    )
    no_confirmed_paper = ManualProfessorInput(
        name="王老师",
        institution="武汉大学",
        identity_confirmed=True,
        papers=[_paper(user_confirmed=False)],
    )

    with pytest.raises(ValueError, match="确认导师身份"):
        build_manual_materials(unconfirmed_identity, id_prefix="run123")
    with pytest.raises(ValueError, match="至少确认一篇论文"):
        build_manual_materials(no_confirmed_paper, id_prefix="run123")


def test_validate_paper_values_reports_missing_required_fields():
    errors = validate_paper_values(
        [
            {"title": "有标题", "abstract": "", "source_url": "https://x"},
            {"title": "", "abstract": "有摘要", "source_url": ""},
        ]
    )
    assert errors == [
        "第 1 篇论文缺少「摘要」",
        "第 2 篇论文缺少「标题」",
        "第 2 篇论文缺少「来源链接」",
    ]


def test_validate_paper_values_returns_empty_when_complete():
    assert validate_paper_values(
        [{"title": "T", "abstract": "A", "source_url": "https://x"}]
    ) == []


def test_professor_identity_key_is_stable_for_the_same_homepage():
    first = professor_identity_key(
        "https://CS.WHU.EDU.CN/teacher/xu/?source=nav",
        "武汉大学",
        "计算机学院",
        "许永超",
    )
    second = professor_identity_key(
        "https://cs.whu.edu.cn/teacher/xu",
        "另一所学校",
        "另一学院",
        "另一姓名",
    )

    assert first == second


def test_professor_identity_key_distinguishes_different_homepages_for_same_name():
    left = professor_identity_key("https://a.example.edu/people/wang", "武汉大学", "A学院", "王伟")
    right = professor_identity_key("https://b.example.edu/people/wang", "武汉大学", "A学院", "王伟")

    assert left != right


def test_professor_identity_key_falls_back_to_school_department_and_name():
    first = professor_identity_key(None, "武汉大学", "计算机学院", "王伟")
    second = professor_identity_key(None, " 武汉大学 ", "计算机学院", "王伟")
    other = professor_identity_key(None, "武汉大学", "信息管理学院", "王伟")

    assert first == second
    assert first != other
