"""检索回来 0 篇时，必须说清"是没查成"还是"查了但没有"。

`CLAUDE.md` 要求「既不得报错中断，也不得静默返回空结果」。空着一片什么都不说，
用户会以为工具坏了，或者更糟——以为这位导师没有论文。这两件事对用户的意义完全
不同，处理方式也不同：

- 所有来源都失败 → 系统侧问题，应该重试/换来源；
- 来源正常返回但没有 → 多半姓名或机构对不上，应该换关键词或手动补录。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def _papers_page(tmp_path, monkeypatch, *, sources: str | None = None,
                 health: dict | None = None) -> AppTest:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    app = AppTest.from_file(APP_PATH).run(timeout=15)
    if sources is not None:
        app.session_state["_research_sources"] = sources
    if health is not None:
        app.session_state["_research_health"] = health
    app.session_state["candidate_papers"] = []
    next(button for button in app.button if "论文核验" in button.label).click().run(timeout=15)
    return app


def _messages(app: AppTest) -> str:
    blocks = []
    for kind in ("info", "warning", "error", "success"):
        blocks.extend(str(item.value) for item in app.get(kind))
    return "\n".join(blocks)


def test_no_message_before_any_search(tmp_path, monkeypatch):
    app = _papers_page(tmp_path, monkeypatch)
    assert not app.exception
    assert "来源" not in _messages(app)


def test_all_sources_failed_says_it_is_not_the_advisors_fault(tmp_path, monkeypatch):
    app = _papers_page(
        tmp_path, monkeypatch,
        sources="本次没有检索到论文；未返回：OpenAlex（超时）、Crossref（HTTP 503）",
        health={"total": 2, "searched": 0, "failed": 2,
                "failed_labels": ["OpenAlex", "Crossref"]},
    )
    assert not app.exception

    message = _messages(app)
    assert "一个来源都没查成" in message
    assert "不代表这位导师没有论文" in message
    assert "重试" in message


def test_empty_but_healthy_sources_suggests_other_keywords(tmp_path, monkeypatch):
    app = _papers_page(
        tmp_path, monkeypatch,
        sources="本次没有检索到论文",
        health={"total": 2, "searched": 2, "failed": 0, "failed_labels": []},
    )
    assert not app.exception

    message = _messages(app)
    assert "正常返回" in message
    assert "英文名" in message
    assert "代表论文标题" in message
    assert "手动补录" in message


def test_partial_failure_still_counts_as_having_searched(tmp_path, monkeypatch):
    """只要有来源正常返回，就不该说成"一个都没查成"。"""
    app = _papers_page(
        tmp_path, monkeypatch,
        sources="OpenAlex 0 篇；未返回：DBLP（超时）",
        health={"total": 2, "searched": 1, "failed": 1, "failed_labels": ["DBLP"]},
    )
    assert not app.exception

    message = _messages(app)
    assert "一个来源都没查成" not in message
    assert "正常返回" in message


def test_missing_health_falls_back_to_the_generic_message(tmp_path, monkeypatch):
    """健康度取不到时（老会话、异常）不能崩，退回通用提示。"""
    app = _papers_page(tmp_path, monkeypatch, sources="本次没有检索到论文")
    assert not app.exception

    assert "正常返回" in _messages(app)
