"""Agent 运行轨迹在页面上真的会出现（Streamlit AppTest 实测）。

纯 HTML 生成的测试见 `test_ui_trace.py`；这里验的是"挂到页面上"这一步：
没跑过 Agent 时不该有空面板，跑过之后轨迹区、降级说明、工具清单都要出现。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings

APP_PATH = str(Path(__file__).parent.parent / "app.py")

SAMPLE_TRACE = {
    "task": "检索导师「陆伟」的候选论文",
    "tools": [
        {"name": "search_by_author", "description": "按作者查", "permission": "network"},
    ],
    "steps": [
        {"index": 0, "kind": "llm_decision", "name": "decide", "status": "ok",
         "duration_ms": 820, "summary": "tool"},
        {"index": 1, "kind": "tool_call", "name": "search_by_author",
         "args_digest": "name=陆伟", "status": "ok", "duration_ms": 1430,
         "summary": "papers=25"},
    ],
    "budget": {"steps": 2, "tokens": 8120, "external_calls": 3, "elapsed_seconds": 12.4},
    "degraded_reason": "",
}


def _open_papers_page(tmp_path, monkeypatch, trace=None) -> AppTest:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    app = AppTest.from_file(APP_PATH).run(timeout=15)
    if trace is not None:
        app.session_state["_agent_trace"] = trace
    next(button for button in app.button if "论文核验" in button.label).click().run(timeout=15)
    return app


def _markdown_text(app: AppTest) -> str:
    return "\n".join(str(item.value) for item in app.markdown)


def test_no_trace_panel_before_any_run(tmp_path, monkeypatch):
    app = _open_papers_page(tmp_path, monkeypatch)
    assert not app.exception
    assert "本次研究过程" not in _markdown_text(app)


def test_trace_panel_appears_with_steps_and_overview(tmp_path, monkeypatch):
    app = _open_papers_page(tmp_path, monkeypatch, SAMPLE_TRACE)
    assert not app.exception

    rendered = _markdown_text(app)
    assert "RESEARCH PROCESS / 本次研究过程" in rendered
    assert "按导师姓名检索论文" in rendered
    assert "research-progress" in rendered
    assert "search_by_author" not in rendered
    assert "papers=25" in rendered
    # 概览行的六个数字
    for label in ("STEPS", "TOOLS", "TOOL CALLS", "ELAPSED", "TOKENS", "NETWORK"):
        assert label in rendered
    assert "8120" in rendered


def test_trace_panel_explains_degradation(tmp_path, monkeypatch):
    trace = dict(SAMPLE_TRACE, degraded_reason="MAX_EXTERNAL_CALLS")
    app = _open_papers_page(tmp_path, monkeypatch, trace)
    assert not app.exception

    rendered = _markdown_text(app)
    assert "trace-degraded" in rendered
    assert "MAX_EXTERNAL_CALLS" in rendered
    assert "优雅降级" in rendered


def test_trace_can_be_downloaded_as_json(tmp_path, monkeypatch):
    app = _open_papers_page(tmp_path, monkeypatch, SAMPLE_TRACE)
    assert not app.exception

    labels = [item.label for item in app.get("download_button")]
    assert any("轨迹" in label for label in labels), labels


def test_tool_catalog_is_listed(tmp_path, monkeypatch):
    app = _open_papers_page(tmp_path, monkeypatch, SAMPLE_TRACE)
    assert not app.exception

    rendered = _markdown_text(app)
    assert "按作者查" in rendered


def test_history_record_replays_its_agent_trace(tmp_path, monkeypatch):
    """回看历史记录时也要能看到当时 Agent 做了什么。

    轨迹以前只写进数据库、从来没被读回来——刷新一下就没了，回看记录也看不到。
    这条测试盯住"存了要能读回来并显示"。
    """
    from advisor_fit.demo_data import ensure_demo_data  # noqa: PLC0415 - 只在测试里用

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    ensure_demo_data(data_dir)

    app = AppTest.from_file(APP_PATH).run(timeout=20)
    next(button for button in app.button if "研究档案" in button.label).click().run(timeout=20)
    assert not app.exception

    record = next(
        button for button in app.button
        if "·" in button.label and "研究档案" not in button.label
    )
    record.click().run(timeout=20)
    assert not app.exception

    rendered = _markdown_text(app)
    assert "RESEARCH PROCESS / 本次研究过程" in rendered
    assert "示例轨迹，非真实运行" in rendered
