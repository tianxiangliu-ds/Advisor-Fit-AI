"""Agent 运行轨迹的渲染测试。

轨迹是"Agent 可见"的核心展示区：它把一次运行摊开成"调了哪些工具、每步多久、
成功还是失败"。这里测纯 HTML 生成部分；页面上是否真的出现由
`test_ui_trace_panel.py` 用 Streamlit AppTest 覆盖。
"""

from __future__ import annotations

from advisor_fit.ui_trace import (
    format_duration,
    has_content,
    trace_degraded_html,
    trace_overview,
    trace_panel_html,
    trace_step_html,
)

SAMPLE_TRACE = {
    "task": "检索导师「陆伟」的候选论文",
    "tools": [
        {"name": "search_by_author", "description": "按作者查", "permission": "network"},
        {"name": "search_by_title", "description": "按标题查", "permission": "network"},
    ],
    "steps": [
        {"index": 0, "kind": "llm_decision", "name": "decide", "status": "ok",
         "duration_ms": 820, "summary": "tool"},
        {"index": 1, "kind": "tool_call", "name": "search_by_author",
         "args_digest": "institution=武汉大学&name=陆伟", "status": "ok",
         "duration_ms": 1430, "summary": "papers=25"},
    ],
    "budget": {"steps": 2, "tokens": 8120, "external_calls": 3, "elapsed_seconds": 12.4},
    "degraded_reason": "",
}


def test_format_duration_switches_unit_at_one_second():
    assert format_duration(820) == "820ms"
    assert format_duration(1000) == "1.0s"
    assert format_duration(1430) == "1.4s"


def test_overview_reports_the_six_numbers():
    overview = dict(trace_overview(SAMPLE_TRACE))
    assert overview["STEPS"] == "2"
    assert overview["TOOLS"] == "2"
    assert overview["TOOL CALLS"] == "1"
    assert overview["ELAPSED"] == "12.4s"
    assert overview["TOKENS"] == "8120"
    assert overview["NETWORK"] == "3"


def test_overview_falls_back_to_step_durations_without_budget():
    trace = {"steps": [{"duration_ms": 300}, {"duration_ms": 700}]}
    assert dict(trace_overview(trace))["ELAPSED"] == "1.0s"


def test_step_html_shows_name_args_status_and_duration():
    html = trace_step_html(SAMPLE_TRACE["steps"][1])
    assert "search_by_author" in html
    assert "institution=武汉大学&amp;name=陆伟" in html
    assert "成功 · 1.4s" in html
    assert 'class="ok"' in html


def test_step_html_marks_failures_with_error_class():
    html = trace_step_html(
        {"index": 0, "kind": "tool_call", "name": "search_by_author",
         "status": "error", "error": "HTTP 403", "duration_ms": 90}
    )
    assert 'class="error"' in html
    assert "失败" in html
    assert "HTTP 403" in html


def test_step_html_marks_skipped_steps():
    html = trace_step_html(
        {"index": 0, "kind": "llm_decision", "name": "decide", "status": "skipped"}
    )
    assert 'class="skipped"' in html
    assert "已跳过" in html


def test_panel_puts_overview_and_every_step_in_one_panel():
    html = trace_panel_html(SAMPLE_TRACE)
    assert html.count('class="trace-step"') == 2
    assert 'class="trace-meta"' in html
    assert "AGENT" not in html  # 标题由页面负责，HTML 片段里不重复


def test_panel_shows_the_task_so_steps_have_context():
    """只列工具调用看不出"它在干什么"，任务行提供上下文。"""
    html = trace_panel_html(SAMPLE_TRACE)
    assert 'class="trace-task"' in html
    assert "检索导师「陆伟」的候选论文" in html


def test_panel_omits_task_row_when_task_is_empty():
    html = trace_panel_html({"steps": [{"index": 0, "kind": "done"}]})
    assert 'class="trace-task"' not in html


def test_panel_escapes_user_supplied_text():
    html = trace_panel_html(
        {"steps": [{"index": 0, "kind": "tool_call", "name": "<script>alert(1)</script>"}]}
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_degraded_html_explains_what_happened():
    html = trace_degraded_html("MAX_EXTERNAL_CALLS")
    assert "MAX_EXTERNAL_CALLS" in html
    assert "优雅降级" in html
    assert 'class="trace-degraded"' in html


def test_has_content_is_false_before_any_run():
    """没跑过 Agent 时不该出现空面板。"""
    assert has_content(None) is False
    assert has_content({}) is False
    assert has_content({"steps": [], "tools": []}) is False
    assert has_content({"steps": [{"index": 0}]}) is True
    assert has_content({"tools": [{"name": "x"}]}) is True
