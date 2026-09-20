"""按方向找候选导师的排序逻辑测试（纯函数，不联网、不碰数据库）。"""

from __future__ import annotations

from advisor_fit.analysis.direction_search import (
    completeness,
    completeness_text,
    match_reasons,
    rank_candidates,
    safe_like_terms,
    split_terms,
)
from advisor_fit.models.advisor import Advisor


def make_advisor(
    name: str,
    *,
    university: str = "武汉大学",
    department: str = "信息管理学院",
    directions: list[str] | None = None,
    areas: list[str] | None = None,
    title: str = "教授",
    email: str = "a@whu.edu.cn",
    homepage: str = "https://example.edu/a",
    profile_text: str = "",
    publications: list[str] | None = None,
) -> Advisor:
    return Advisor(
        university=university,
        name=name,
        department=department,
        title=title,
        email=email,
        research_directions=directions if directions is not None else ["知识图谱"],
        research_areas=areas or [],
        publications=publications or [],
        homepage_url=homepage,
        profile_text=profile_text,
    )


# ---------- 关键词切分 ----------


def test_split_terms_supports_chinese_and_english_separators():
    assert split_terms("知识图谱，数字人文; 文化遗产、语义网") == [
        "知识图谱",
        "数字人文",
        "文化遗产",
        "语义网",
    ]
    assert split_terms("") == []
    assert split_terms(None) == []
    # 重复词只留一个，保持顺序
    assert split_terms("RAG, rag".replace("rag", "RAG")) == ["RAG"]


def test_split_terms_keeps_multiword_phrases_with_spaces():
    assert split_terms("machine learning, knowledge graph") == [
        "machine learning",
        "knowledge graph",
    ]


def test_safe_like_terms_strips_wildcards():
    """用户输入 % 时不能把整库捞出来。"""
    assert safe_like_terms(["知识图谱", "%"]) == ["知识图谱"]
    assert safe_like_terms(["a_b", "c%d"]) == ["ab", "cd"]


# ---------- 命中理由 ----------


def test_match_reasons_report_field_and_weight():
    advisor = make_advisor("张三", directions=["知识图谱与语义计算"], areas=["数字人文"])
    reasons = match_reasons(advisor, ["知识图谱", "数字人文"])
    labels = {reason.label for reason in reasons}
    assert labels == {"研究方向", "研究领域"}
    assert all(reason.weight >= 3 for reason in reasons)


def test_match_reasons_are_case_insensitive_for_english():
    advisor = make_advisor("Wei", directions=["Knowledge Graph"])
    assert match_reasons(advisor, ["knowledge graph"])


def test_match_reasons_count_a_term_once_per_field():
    """研究方向常写成 ["知识图谱", "知识图谱与语义计算"]，同一个词不能算两遍。"""
    advisor = make_advisor("张三", directions=["知识图谱", "知识图谱与语义计算"])
    reasons = match_reasons(advisor, ["知识图谱"])
    assert len(reasons) == 1
    assert reasons[0].weight == 3
    hits = rank_candidates([advisor], ["知识图谱"])
    assert hits[0].match_score == 3


def test_completeness_counts_filled_fields():
    full = make_advisor("A")
    assert completeness(full) == 1.0
    partial = make_advisor("B", title="", email="", homepage="")
    assert completeness(partial) == 0.25
    assert "研究方向 ✓" in completeness_text(full)
    assert "邮箱 ✗" in completeness_text(partial)


# ---------- 排序 ----------


def test_rank_candidates_orders_by_match_score_then_completeness():
    strong = make_advisor("强匹配", directions=["知识图谱", "数字人文"])
    medium = make_advisor("中匹配", directions=["知识图谱"], title="", email="", homepage="")
    irrelevant = make_advisor("无关", directions=["海洋地质"])
    hits = rank_candidates([irrelevant, medium, strong], ["知识图谱", "数字人文"])
    assert [hit.advisor.name for hit in hits] == ["强匹配", "中匹配"]


def test_rank_candidates_ignores_profile_text_only_matches():
    """只在简介正文里出现过关键词的不算——那多半是页面噪声。"""
    noisy = make_advisor("噪声", directions=["海洋地质"], profile_text="我们学院也做知识图谱")
    hits = rank_candidates([noisy], ["知识图谱"])
    assert hits == []


def test_rank_candidates_accepts_department_matches():
    advisor = make_advisor("李四", department="计算机学院", directions=["软件工程"])
    hits = rank_candidates([advisor], ["计算机"])
    assert len(hits) == 1
    assert "院系" in hits[0].reason_text


def test_rank_candidates_filters_by_university_scope():
    whu = make_advisor("甲", university="武汉大学")
    pku = make_advisor("乙", university="北京大学")
    hits = rank_candidates([whu, pku], ["知识图谱"], universities=["北京大学"])
    assert [hit.advisor.name for hit in hits] == ["乙"]


def test_rank_candidates_is_stable_for_equal_scores():
    first = make_advisor("同一分A", university="武汉大学")
    second = make_advisor("同一分B", university="武汉大学")
    hits = rank_candidates([second, first], ["知识图谱"])
    assert [hit.advisor.name for hit in hits] == ["同一分A", "同一分B"]


def test_rank_candidates_respects_limit():
    advisors = [make_advisor(f"导师{i:02d}") for i in range(30)]
    assert len(rank_candidates(advisors, ["知识图谱"], limit=5)) == 5


def test_hit_row_is_ready_for_side_by_side_table():
    hits = rank_candidates([make_advisor("张三", directions=["知识图谱"])], ["知识图谱"])
    row = hits[0].to_row()
    assert row["姓名"] == "张三"
    assert row["学校"] == "武汉大学"
    assert row["研究方向"] == "知识图谱"
    assert "研究方向命中 知识图谱" in row["匹配理由"]
    assert row["资料完整度"] == "100%"


def test_empty_terms_produce_no_hits():
    assert rank_candidates([make_advisor("张三")], []) == []
