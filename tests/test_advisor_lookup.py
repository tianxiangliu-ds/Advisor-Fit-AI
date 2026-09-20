"""统一导师库的新查询：按姓名+学校取资料、按研究方向关键词粗筛。"""

from __future__ import annotations

from advisor_fit.models.advisor import Advisor
from advisor_fit.storage.advisor_repo import AdvisorRepository


def _advisor(
    name: str,
    *,
    university: str = "武汉大学",
    department: str = "信息管理学院",
    directions: list[str] | None = None,
    areas: list[str] | None = None,
    title: str = "教授",
    publications: list[str] | None = None,
    profile_text: str = "",
) -> Advisor:
    return Advisor(
        university=university,
        department=department,
        name=name,
        title=title,
        research_directions=directions or [],
        research_areas=areas or [],
        publications=publications or [],
        profile_text=profile_text,
    )


def _repo(tmp_path) -> AdvisorRepository:
    return AdvisorRepository(tmp_path / "advisors.db")


def test_lookup_finds_by_exact_name(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_advisor("陆伟"), source="official")

    found = repo.lookup("陆伟")

    assert [item.name for item in found] == ["陆伟"]


def test_lookup_filters_by_university_but_never_hides_same_name(tmp_path):
    """学校对不上时不能假装"查无此人"——同名记录仍要返回，交给用户核对。"""
    repo = _repo(tmp_path)
    repo.upsert(_advisor("张伟", university="武汉大学"), source="official")

    assert [item.university for item in repo.lookup("张伟", "武汉大学")] == ["武汉大学"]
    # 学校填错/导师调动单位时，仍返回同名记录
    assert [item.university for item in repo.lookup("张伟", "北京大学")] == ["武汉大学"]


def test_lookup_requires_a_name(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_advisor("陆伟"), source="official")
    assert repo.lookup("") == []
    assert repo.lookup("   ") == []


def test_search_by_terms_matches_directions_and_department(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_advisor("甲", directions=["知识图谱", "数字人文"]), source="official")
    repo.upsert(_advisor("乙", department="计算机学院", directions=["软件工程"]), source="official")
    repo.upsert(_advisor("丙", directions=["海洋地质"]), source="official")

    by_direction = repo.search_by_terms(["知识图谱"])
    by_department = repo.search_by_terms(["计算机"])
    assert [item.name for item in by_direction] == ["甲"]
    assert [item.name for item in by_department] == ["乙"]
    assert repo.search_by_terms(["不存在的方向"]) == []


def test_search_by_terms_matches_publications_and_profile_text(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_advisor("甲", publications=["Knowledge Graph Embedding"]), source="official")
    repo.upsert(_advisor("乙", profile_text="主要做数字人文与文化遗产"), source="official")

    assert [item.name for item in repo.search_by_terms(["Knowledge Graph"])] == ["甲"]
    assert [item.name for item in repo.search_by_terms(["文化遗产"])] == ["乙"]


def test_search_by_terms_limits_universities(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_advisor("甲", university="武汉大学", directions=["知识图谱"]), source="official")
    repo.upsert(_advisor("乙", university="北京大学", directions=["知识图谱"]), source="official")

    scoped = repo.search_by_terms(["知识图谱"], universities=["北京大学"])
    assert [item.name for item in scoped] == ["乙"]


def test_search_by_terms_needs_real_terms(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_advisor("甲", directions=["知识图谱"]), source="official")

    assert repo.search_by_terms([]) == []
    # 只输入通配符时不能把整库捞出来
    assert repo.search_by_terms(["%"]) == []


def test_search_by_terms_respects_limit(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_many(
        [_advisor(f"导师{i:02d}", directions=["知识图谱"]) for i in range(10)], source="official"
    )
    assert len(repo.search_by_terms(["知识图谱"], limit=3)) == 3
