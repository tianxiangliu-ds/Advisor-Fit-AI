"""首次使用：数据目录全空时，每个页面都不能报错。

这是 1.0 的底线——新用户克隆仓库、还没建导师库、什么都没跑过的时候打开应用，
必须能看到"该先做什么"的页面，而不是一堆异常。演示数据那条路径已经有人守着，
这里守的是**什么都没准备**的那条路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings

APP_PATH = str(Path(__file__).parent.parent / "app.py")

SIDEBAR_PAGES = (
    "方向找导师", "学生事实", "导师档案", "论文核验", "匹配简报", "联系邮件", "研究档案",
)


@pytest.fixture
def empty_app(tmp_path, monkeypatch):
    """一个全新的空数据目录——不生成演示数据、不预置任何记录。"""
    data_dir = tmp_path / "empty-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "empty-uploads")
    return AppTest.from_file(APP_PATH).run(timeout=20)


def test_landing_page_renders_without_data(empty_app):
    """首屏用的是自定义 HTML 大标题（不是 st.title），所以查 markdown。"""
    assert not empty_app.exception
    rendered = "\n".join(str(item.value) for item in empty_app.markdown)
    assert "landing-hero" in rendered
    assert rendered.strip(), "首屏不能是空白"


def test_advisor_library_page_handles_an_empty_library(empty_app):
    """导师库为空时要给提示，而不是抛异常。"""
    next(b for b in empty_app.button if "方向找导师" in b.label).click().run(timeout=20)

    assert not empty_app.exception


@pytest.mark.parametrize("label", SIDEBAR_PAGES)
def test_every_page_opens_on_a_fresh_install(empty_app, label):
    next(button for button in empty_app.button if label in button.label).click().run(timeout=20)

    assert not empty_app.exception, f"「{label}」页在空数据下报错了"


def test_research_page_offers_a_starting_point_without_history(empty_app):
    """没有任何历史记录时，研究档案页要说明"这里以后会出现什么"。"""
    next(b for b in empty_app.button if "研究档案" in b.label).click().run(timeout=20)

    assert not empty_app.exception
    messages = "\n".join(str(item.value) for item in empty_app.get("info"))
    assert messages.strip(), "空历史时应当有提示，而不是空白"
