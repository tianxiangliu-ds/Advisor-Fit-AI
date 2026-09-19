"""导师库采集与存储测试（不触真实网络/LLM）。"""

from __future__ import annotations

from advisor_fit.ingest.faculty import (
    FacultyPageOutput,
    extract_links,
    parse_faculty_page,
)
from advisor_fit.llm.provider import NullLLM
from advisor_fit.models.faculty import FacultyRecord
from advisor_fit.storage.faculty_repo import FacultyRepository

_HTML = """
<html><body>
  <a href="/info/1019/2473.htm">蔡朝晖</a>
  <a href="http://example.com/news/1">学院新闻</a>
  <a href="javascript:void(0)">忽略</a>
  <a href="mailto:x@y.com">邮件</a>
</body></html>
"""


def _record(**overrides) -> FacultyRecord:
    values = {
        "id": "id1",
        "name": "蔡朝晖",
        "university": "武汉大学",
        "college": "计算机学院",
        "title": "副教授",
        "homepage_url": "https://cs.whu.edu.cn/info/1019/2473.htm",
        "email": "zhcai@whu.edu.cn",
        "research_areas": ["数字媒体"],
        "research_directions": ["数字图像处理"],
        "publications": [],
        "profile_text": "简介",
        "source_url": "https://cs.whu.edu.cn/szdw/zrjs.htm",
        "retrieved_at": "2026-01-01",
    }
    values.update(overrides)
    return FacultyRecord(**values)


def test_extract_links_filters_and_resolves():
    links = extract_links(_HTML, "https://cs.whu.edu.cn/szdw/zrjs.htm")
    texts = {link["text"] for link in links}
    assert "蔡朝晖" in texts
    assert "学院新闻" in texts
    # javascript 和 mailto 被过滤
    assert "忽略" not in texts
    assert "邮件" not in texts
    # 相对路径被解析为绝对
    cai = next(link for link in links if link["text"] == "蔡朝晖")
    assert cai["href"] == "https://cs.whu.edu.cn/info/1019/2473.htm"


def test_faculty_repo_upsert_and_list(tmp_path):
    repo = FacultyRepository(tmp_path / "faculty.db")
    repo.upsert(_record())
    assert repo.count() == 1
    listed = repo.list_by_university("武汉大学")
    assert listed[0].name == "蔡朝晖"
    assert listed[0].research_areas == ["数字媒体"]


def test_faculty_repo_upsert_dedupes_by_id(tmp_path):
    repo = FacultyRepository(tmp_path / "faculty.db")
    repo.upsert(_record())
    repo.upsert(_record(title="教授"))
    assert repo.count() == 1
    assert repo.list_by_university("武汉大学")[0].title == "教授"


def test_parse_faculty_page_falls_back_to_email_regex():
    text = "蔡朝晖 副教授\n研究方向：数字媒体\n邮箱 zhcai@whu.edu.cn"
    record = parse_faculty_page(
        text, NullLLM(), name="蔡朝晖", university="武汉大学",
        college="计算机学院", homepage_url="https://x/y.htm",
    )
    assert record.name == "蔡朝晖"
    assert record.email == "zhcai@whu.edu.cn"


def test_parse_faculty_page_uses_llm_output():
    class FakeLLM:
        def generate(self, *, schema, instructions, payload):
            return FacultyPageOutput(
                title="教授",
                email="a@b.com",
                research_areas=["AI"],
                research_directions=["NLP"],
                publications=["某论文"],
            )

    record = parse_faculty_page(
        "text", FakeLLM(), name="蔡朝晖", university="武汉大学",
        college="计算机学院", homepage_url="https://x/y.htm",
    )
    assert record.title == "教授"
    assert record.research_directions == ["NLP"]
    assert record.publications == ["某论文"]
