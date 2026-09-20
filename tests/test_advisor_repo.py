"""统一导师库测试：合并只补空、不覆盖；字段来源可追溯；同院同名只留一行。"""

from __future__ import annotations

from advisor_fit.models.advisor import Advisor, merge_into
from advisor_fit.storage.advisor_repo import AdvisorRepository


def _base(**overrides) -> Advisor:
    data = {"university": "武汉大学", "department": "信息管理学院", "name": "陆伟"}
    data.update(overrides)
    return Advisor(**data)


# -- 合并规则 -----------------------------------------------------------------


def test_merge_fills_only_empty_fields():
    target = _base(title="教授")
    extra = _base(title="副教授", email="luwei@whu.edu.cn")

    merged = merge_into(target, extra, "faculty")

    assert merged.title == "教授", "已有值不能被覆盖"
    assert merged.email == "luwei@whu.edu.cn"
    assert merged.field_sources["email"] == "faculty"
    assert "title" not in merged.field_sources


def test_merge_replaces_non_url_source_placeholder_only():
    target = _base(source_url="官网院系师资页", title="教授")
    extra = _base(source_url="https://sim.whu.edu.cn/info/1549/12919.htm", title="副教授")

    merged = merge_into(target, extra, "official")

    assert merged.source_url == "https://sim.whu.edu.cn/info/1549/12919.htm"
    assert merged.title == "教授"


def test_merge_records_which_source_filled_each_field():
    merged = merge_into(_base(), _base(research_directions=["信息检索"]), "official")

    assert merged.field_sources["research_directions"] == "official"


def test_merge_unions_sources():
    target = _base(sources=["community"])
    extra = _base(sources=["faculty"])

    merged = merge_into(target, extra, "official")

    assert set(merged.sources) == {"community", "faculty", "official"}


def test_merge_does_not_mutate_original():
    target = _base()
    merge_into(target, _base(email="a@b.edu.cn"), "faculty")

    assert target.email == ""


def test_missing_fields_reports_gaps():
    assert set(_base().missing_fields()) == {
        "title",
        "email",
        "research_directions",
        "homepage_url",
        "orcid",
    }
    assert _base(title="教授", email="a@b.cn", research_directions=["x"],
                 homepage_url="https://a", orcid="0000").missing_fields() == []


# -- 仓储 ---------------------------------------------------------------------


def test_upsert_creates_then_enriches_without_overwriting(tmp_path):
    repo = AdvisorRepository(tmp_path / "advisors.db")
    repo.upsert(_base(title="教授"), source="official")
    repo.upsert(_base(title="讲师", email="luwei@whu.edu.cn"), source="faculty")

    saved = repo.get("武汉大学", "信息管理学院", "陆伟")

    assert repo.count() == 1
    assert saved is not None
    assert saved.title == "教授"
    assert saved.email == "luwei@whu.edu.cn"
    assert set(saved.sources) == {"official", "faculty"}


def test_same_person_in_two_departments_keeps_two_rows(tmp_path):
    repo = AdvisorRepository(tmp_path / "advisors.db")
    repo.upsert(_base(), source="official")
    repo.upsert(_base(department="前沿交叉学科研究院"), source="official")

    assert repo.count() == 2
    assert set(repo.departments("武汉大学")) == {"信息管理学院", "前沿交叉学科研究院"}


def test_search_by_university_and_department(tmp_path):
    repo = AdvisorRepository(tmp_path / "advisors.db")
    repo.upsert_many([_base(), _base(name="马费成")], source="official")
    repo.upsert(_base(department="计算机学院", name="张三"), source="official")

    assert len(repo.search("武汉大学", "信息管理学院")) == 2
    assert len(repo.search("武汉大学")) == 3
    assert repo.search("不存在大学") == []


def test_reviews_are_stored_separately(tmp_path):
    repo = AdvisorRepository(tmp_path / "advisors.db")
    repo.import_reviews(
        [
            {"university": "武汉大学", "department": "信息管理学院", "supervisor": "陆伟",
             "rate": 4.5, "summary": "很好", "detail": "详情一"},
            {"university": "武汉大学", "department": "信息管理学院", "supervisor": "陆伟",
             "rate": 3.0, "summary": "", "detail": "详情二"},
        ]
    )

    reviews = repo.reviews_for("武汉大学", "陆伟")

    assert repo.review_count() == 2
    assert len(reviews) == 2
    assert {r["detail"] for r in reviews} == {"详情一", "详情二"}


def test_field_fill_rates_and_source_breakdown(tmp_path):
    repo = AdvisorRepository(tmp_path / "advisors.db")
    repo.upsert(_base(title="教授", email="a@b.cn"), source="faculty")
    repo.upsert(_base(name="马费成"), source="community")

    rates = repo.field_fill_rates()

    assert rates["_total"] == 2
    assert rates["name"] == 1.0
    assert rates["title"] == 0.5
    assert repo.source_breakdown() == {"faculty": 1, "community": 1}


def test_lists_and_json_fields_round_trip(tmp_path):
    repo = AdvisorRepository(tmp_path / "advisors.db")
    repo.upsert(
        _base(
            research_directions=["信息检索", "知识图谱"],
            publications=["论文A"],
            field_sources={"title": "faculty"},
        ),
        source="faculty",
    )

    saved = repo.get("武汉大学", "信息管理学院", "陆伟")

    assert saved is not None
    assert saved.research_directions == ["信息检索", "知识图谱"]
    assert saved.publications == ["论文A"]
    # 写入时会给所有有值的字段补上来源标记
    assert saved.field_sources["title"] == "faculty"
    assert saved.field_sources["research_directions"] == "faculty"
