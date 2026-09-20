"""「方向找导师」页面的端到端 UI 测试：建库 → 搜索 → 并排比较 → 带入导师档案。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings
from advisor_fit.models.advisor import Advisor
from advisor_fit.storage.advisor_repo import AdvisorRepository


def _seed_library(tmp_path) -> None:
    repo = AdvisorRepository(tmp_path / "data" / "advisors.db")
    repo.upsert_many(
        [
            Advisor(
                university="武汉大学",
                department="信息管理学院",
                name="陆伟",
                title="教授",
                email="luwei@whu.edu.cn",
                research_directions=["知识图谱", "信息检索"],
                homepage_url="https://sim.whu.edu.cn/luwei",
                sources=["official"],
            ),
            Advisor(
                university="南京大学",
                department="计算机科学与技术系",
                name="周志华",
                title="教授",
                research_directions=["机器学习", "知识图谱"],
                sources=["official"],
            ),
            Advisor(
                university="北京大学",
                department="地球与空间科学学院",
                name="王海洋",
                title="教授",
                research_directions=["海洋地质"],
                sources=["official"],
            ),
        ],
        source="official",
    )


def _app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    return AppTest.from_file(str(Path(__file__).parent.parent / "app.py")).run(timeout=20)


def _open_direction_page(app: AppTest) -> AppTest:
    next(button for button in app.button if "方向找导师" in button.label).click().run()
    return app


def test_direction_page_is_reachable_and_shows_library_size(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    app = _open_direction_page(_app(tmp_path, monkeypatch))

    assert not app.exception
    assert any("先找方向" in item.value for item in app.title)
    assert any("3 位导师" in item.value for item in app.caption)


def test_direction_page_explains_how_to_build_an_empty_library(tmp_path, monkeypatch):
    app = _open_direction_page(_app(tmp_path, monkeypatch))

    assert not app.exception
    assert any("本地导师库还是空的" in item.value for item in app.info)
    # 采集工具已经拆到独立项目，提示里不能再指向本仓库里不存在的脚本
    markdown = " ".join(item.value for item in app.markdown)
    assert "advisor-fit-crawl" in markdown
    assert "scripts\\crawl_university.py" not in markdown


def test_search_ranks_candidates_and_shows_why(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    app = _open_direction_page(_app(tmp_path, monkeypatch))

    next(item for item in app.text_input if "研究方向关键词" in item.label).set_value("知识图谱")
    next(button for button in app.button if "找候选导师" in button.label).click().run()

    assert not app.exception
    frame = app.dataframe[0].value
    assert list(frame["姓名"]) == ["陆伟", "周志华"]
    assert "海洋地质" not in "、".join(frame["研究方向"])
    assert "研究方向命中 知识图谱" in frame["匹配理由"].iloc[0]


def test_university_scope_narrows_candidates(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    app = _open_direction_page(_app(tmp_path, monkeypatch))

    next(item for item in app.text_input if "研究方向关键词" in item.label).set_value("知识图谱")
    next(item for item in app.multiselect if "限定学校" in item.label).set_value(["南京大学"])
    next(button for button in app.button if "找候选导师" in button.label).click().run()

    assert not app.exception
    assert list(app.dataframe[0].value["姓名"]) == ["周志华"]


def test_no_match_warns_instead_of_showing_an_empty_table(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    app = _open_direction_page(_app(tmp_path, monkeypatch))

    next(item for item in app.text_input if "研究方向关键词" in item.label).set_value("量子引力")
    next(button for button in app.button if "找候选导师" in button.label).click().run()

    assert not app.exception
    assert any("没有找到匹配的导师" in item.value for item in app.warning)
    assert not app.dataframe


def test_empty_query_is_rejected(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    app = _open_direction_page(_app(tmp_path, monkeypatch))

    next(button for button in app.button if "找候选导师" in button.label).click().run()

    assert not app.exception
    assert any("至少填一个研究方向关键词" in item.value for item in app.warning)


def test_side_by_side_comparison_lists_picked_candidates(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    app = _open_direction_page(_app(tmp_path, monkeypatch))

    next(item for item in app.text_input if "研究方向关键词" in item.label).set_value("知识图谱")
    next(button for button in app.button if "找候选导师" in button.label).click().run()

    picker = next(item for item in app.multiselect if "并排比较" in item.label)
    picker.set_value([picker.options[0], picker.options[1]]).run()

    assert not app.exception
    subtitles = [item.value for item in app.subheader]
    assert "陆伟" in subtitles
    assert "周志华" in subtitles


def test_starting_research_fills_the_professor_page(tmp_path, monkeypatch):
    _seed_library(tmp_path)
    app = _open_direction_page(_app(tmp_path, monkeypatch))

    next(item for item in app.text_input if "研究方向关键词" in item.label).set_value("知识图谱")
    next(button for button in app.button if "找候选导师" in button.label).click().run()
    next(button for button in app.button if "用这位导师开始研究" in button.label).click().run()

    assert not app.exception
    name = next(item for item in app.text_input if item.label == "导师姓名（必填）")
    institution = next(item for item in app.text_input if item.label == "学校/单位（必填）")
    interests = next(item for item in app.text_area if "研究方向" in item.label)
    assert name.value == "陆伟"
    assert institution.value == "武汉大学"
    assert "知识图谱" in interests.value
