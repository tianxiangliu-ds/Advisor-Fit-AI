"""导师名册测试：只取学校/学院/姓名，评价与评分必须被丢弃。"""

from __future__ import annotations

import json

from advisor_fit.ingest.supervisor_roster import (
    RosterEntry,
    parse_roster,
    roster_summary,
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
        "description": "这段评价内容不应该被保留",
    },
    {
        "university": "武汉大学",
        "department": "信息管理学院",
        "supervisor": "马费成",
        "rate": 5,
        "description": "另一段评价",
    },
    {
        "university": "武汉大学",
        "department": "计算机学院",
        "supervisor": "张三",
        "rate": 3,
        "description": "",
    },
]


# -- 解析 ---------------------------------------------------------------------


def test_parse_keeps_only_three_columns():
    entries = parse_roster(_RAW)

    assert len(entries) == 3
    first = entries[0]
    assert first.university == "武汉大学"
    assert first.department == "信息管理学院"
    assert first.supervisor == "陆伟"
    # 评价与评分不得出现在模型里
    dumped = first.model_dump()
    assert set(dumped) == {"university", "department", "supervisor"}
    assert "评价" not in json.dumps(dumped, ensure_ascii=False)


def test_parse_accepts_a_wrapped_payload():
    entries = parse_roster({"data": _RAW})

    assert len(entries) == 3


def test_parse_accepts_a_json_string():
    entries = parse_roster(json.dumps(_RAW, ensure_ascii=False))

    assert entries[0].supervisor == "陆伟"


def test_parse_skips_entries_without_required_columns():
    entries = parse_roster(
        [
            {"university": "武汉大学", "supervisor": "陆伟"},
            {"university": "", "supervisor": "无学校"},
            {"university": "武汉大学", "supervisor": ""},
            "不是字典",
        ]
    )

    assert len(entries) == 1


def test_parse_deduplicates():
    entries = parse_roster(_RAW + _RAW)

    assert len(entries) == 3


def test_parse_broken_json_returns_empty_list():
    assert parse_roster("{ 这不是合法 json") == []


# -- 统计 ---------------------------------------------------------------------


def test_summary_counts_universities_and_departments():
    summary = roster_summary(parse_roster(_RAW))

    assert summary == {"entries": 3, "universities": 1, "departments": 2}


def test_top_universities_sorted_by_count():
    assert top_universities(parse_roster(_RAW), 5) == [("武汉大学", 3)]


# -- 本地存储 -----------------------------------------------------------------


def _repo(tmp_path) -> RosterRepository:
    repo = RosterRepository(tmp_path / "roster.db")
    repo.replace_all(parse_roster(_RAW))
    return repo


def test_repo_lookup_by_university_and_department(tmp_path):
    repo = _repo(tmp_path)

    # 中文排序按字节序，不用断言顺序，只断言内容
    assert set(repo.lookup("武汉大学", "信息管理学院")) == {"陆伟", "马费成"}
    assert set(repo.lookup("武汉大学")) == {"陆伟", "马费成", "张三"}
    assert repo.lookup("不存在大学") == []


def test_repo_lists_universities_and_departments(tmp_path):
    repo = _repo(tmp_path)

    assert repo.universities() == ["武汉大学"]
    assert set(repo.departments("武汉大学")) == {"信息管理学院", "计算机学院"}


def test_repo_replace_all_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    before = repo.count()

    repo.replace_all(parse_roster(_RAW))

    assert repo.count() == before


def test_repo_is_empty_before_import(tmp_path):
    repo = RosterRepository(tmp_path / "empty.db")

    assert repo.count() == 0
    assert repo.universities() == []
    assert repo.lookup("武汉大学") == []


def test_entry_model_defaults_department_to_empty_string():
    assert RosterEntry(university="武汉大学", supervisor="陆伟").department == ""
