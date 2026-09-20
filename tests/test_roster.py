"""导师名册测试：导师去重不重不漏，括号备注单列，评价一条不丢。"""

from __future__ import annotations

import json

from advisor_fit.ingest.supervisor_roster import (
    RosterEntry,
    parse_roster,
    parse_roster_payload,
    roster_summary,
    split_note,
    top_universities,
)
from advisor_fit.storage.roster_repo import RosterRepository

_RAW = [
    {
        "school_cate": "985",
        "university": "武汉大学",
        "department": "信息管理学院",
        "supervisor": "陆伟",
        "rate": 4.8,
        "desc": "一句话总结",
        "description": "第一条评价正文",
    },
    {
        "school_cate": "985",
        "university": "武汉大学",
        "department": "信息管理学院",
        "supervisor": "陆伟",
        "rate": 5.0,
        "desc": "",
        "description": "第二条评价正文（同一导师的另一条评价）",
    },
    {
        "school_cate": "985",
        "university": "武汉大学",
        "department": "前沿交叉学研究院",
        "supervisor": "陆伟",
        "rate": 3,
        "desc": "",
        "description": "同一人在另一个学院挂名，应各留一条",
    },
    {
        "school_cate": "985",
        "university": "武汉大学",
        "department": "计算机学院",
        "supervisor": "周裕(深圳,千人计划)",
        "rate": 2.5,
        "desc": "",
        "description": "带括号备注的姓名",
    },
    {
        "school_cate": "985",
        "university": "武汉大学",
        "department": "计算机学院",
        "supervisor": "Re: 郑荣濠",
        "rate": None,
        "desc": "",
        "description": "带标题前缀的姓名",
    },
]


# -- 姓名与备注拆分 -----------------------------------------------------------


def test_split_note_separates_parenthetical_remark():
    assert split_note("周裕(深圳,千人计划)") == ("周裕", "深圳,千人计划")
    assert split_note("金小刚(AI)") == ("金小刚", "AI")
    assert split_note("陆伟") == ("陆伟", "")


def test_split_note_strips_reply_prefix():
    assert split_note("Re: 郑荣濠") == ("郑荣濠", "")


# -- 名册：去重但不漏 ---------------------------------------------------------


def test_entries_are_deduplicated_per_department():
    entries = parse_roster(_RAW)

    # 陆伟在信管院只出现一次、在交叉院各一条；两人计算机学院；共 4 条
    assert len(entries) == 4
    keys = {(e.university, e.department, e.supervisor) for e in entries}
    assert len(keys) == 4


def test_same_person_in_two_departments_keeps_both_rows():
    entries = parse_roster(_RAW)
    luwei = [e for e in entries if e.supervisor == "陆伟"]

    assert {e.department for e in luwei} == {"信息管理学院", "前沿交叉学研究院"}


def test_note_is_stored_separately_from_the_name():
    entries = parse_roster(_RAW)
    zhou = next(e for e in entries if e.department == "计算机学院" and "周" in e.supervisor)

    assert zhou.supervisor == "周裕"
    assert zhou.note == "深圳,千人计划"


def test_school_category_is_kept():
    entries = parse_roster(_RAW)

    assert {e.school_cate for e in entries} == {"985"}


def test_parse_accepts_wrapped_payload_and_json_string():
    assert len(parse_roster({"data": _RAW})) == 4
    assert parse_roster(json.dumps(_RAW, ensure_ascii=False))[0].university == "武汉大学"


def test_parse_skips_rows_without_required_columns():
    entries = parse_roster([{"university": "武汉大学", "supervisor": "陆伟"}, {"university": ""}])

    assert len(entries) == 1


def test_parse_broken_json_returns_empty():
    assert parse_roster("{ 这不是合法 json") == []


# -- 评价：一条不丢 -----------------------------------------------------------


def test_reviews_keep_every_entry_for_the_same_advisor():
    payload = parse_roster_payload(_RAW)
    luwei_reviews = [r for r in payload.reviews if r.supervisor == "陆伟"]

    assert len(luwei_reviews) == 3
    details = {r.detail for r in luwei_reviews}
    assert "第一条评价正文" in details
    assert "第二条评价正文（同一导师的另一条评价）" in details


def test_review_keeps_rate_summary_and_detail():
    payload = parse_roster_payload(_RAW)
    first = payload.reviews[0]

    assert first.rate == 4.8
    assert first.summary == "一句话总结"
    assert first.detail == "第一条评价正文"


def test_entries_and_reviews_have_different_counts():
    payload = parse_roster_payload(_RAW)

    assert len(payload.entries) == 4
    assert len(payload.reviews) == 5


# -- 统计 ---------------------------------------------------------------------


def test_summary_counts_universities_and_departments():
    assert roster_summary(parse_roster(_RAW)) == {
        "entries": 4,
        "universities": 1,
        "departments": 3,
    }


def test_top_universities_sorted_by_count():
    assert top_universities(parse_roster(_RAW), 5) == [("武汉大学", 4)]


# -- 本地数据库 ---------------------------------------------------------------


def _repo(tmp_path) -> RosterRepository:
    repo = RosterRepository(tmp_path / "roster.db")
    repo.import_community(parse_roster_payload(_RAW))
    return repo


def test_repo_stores_advisors_and_reviews(tmp_path):
    repo = _repo(tmp_path)

    assert repo.count() == 4
    assert repo.review_count() == 5


def test_repo_lookup_by_university_and_department(tmp_path):
    repo = _repo(tmp_path)

    assert set(repo.lookup("武汉大学", "信息管理学院")) == {"陆伟"}
    assert set(repo.lookup("武汉大学")) == {"陆伟", "周裕", "郑荣濠"}
    assert repo.lookup("不存在大学") == []


def test_repo_lists_universities_and_departments(tmp_path):
    repo = _repo(tmp_path)

    assert repo.universities() == ["武汉大学"]
    assert set(repo.departments("武汉大学")) == {
        "信息管理学院",
        "前沿交叉学研究院",
        "计算机学院",
    }


def test_repo_returns_reviews_for_one_advisor(tmp_path):
    repo = _repo(tmp_path)
    reviews = repo.reviews_for("武汉大学", "陆伟")

    assert len(reviews) == 3
    assert all(item.supervisor == "陆伟" for item in reviews)


def test_repo_entries_carry_note_and_school_category(tmp_path):
    repo = _repo(tmp_path)
    entries = repo.entries("武汉大学", "计算机学院")
    zhou = next(e for e in entries if e.supervisor == "周裕")

    assert zhou.note == "深圳,千人计划"
    assert zhou.school_cate == "985"


def test_official_import_does_not_wipe_community_data(tmp_path):
    repo = _repo(tmp_path)

    repo.upsert_official(
        [RosterEntry(university="武汉大学", department="信息管理学院", supervisor="新导师")],
        source_url="https://example.com/list",
    )

    stats = repo.stats()
    assert stats["advisors"] == 5
    assert stats["from_community"] == 4
    assert stats["from_official"] == 1
    assert repo.review_count() == 5, "官网导入不应清掉社区评价"


def test_community_and_official_can_overlap(tmp_path):
    repo = _repo(tmp_path)

    repo.upsert_official(
        [RosterEntry(university="武汉大学", department="信息管理学院", supervisor="陆伟")]
    )

    stats = repo.stats()
    assert stats["advisors"] == 4, "同一个人不应产生第二行"
    assert stats["from_both"] == 1


def test_reimporting_community_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    repo.import_community(parse_roster_payload(_RAW))

    assert repo.count() == 4
    assert repo.review_count() == 5


def test_entry_model_defaults():
    entry = RosterEntry(university="武汉大学", supervisor="陆伟")

    assert entry.department == ""
    assert entry.note == ""
    assert entry.school_cate == ""
