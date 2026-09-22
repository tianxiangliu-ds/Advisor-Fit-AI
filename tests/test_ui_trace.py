"""Agent 运行轨迹的渲染测试。

轨迹是"Agent 可见"的核心展示区：它把一次运行摊开成"调了哪些工具、每步多久、
成功还是失败"。这里测纯 HTML 生成部分；页面上是否真的出现由
`test_ui_trace_panel.py` 用 Streamlit AppTest 覆盖。
"""

from __future__ import annotations

import advisor_fit.ui_trace as ui_trace
from advisor_fit.ui_trace import (
    format_duration,
    has_content,
    is_rule_mode,
    trace_degraded_html,
    trace_mode_note,
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
    assert "按导师姓名检索论文" in html
    assert "search_by_author" not in html
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
    # 未登记的内部动作名不会原样输出到用户界面，因此恶意字符串既不执行也不展示。
    assert "script" not in html


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


# -- 规则模式（没配大模型） ----------------------------------------------------


RULE_TRACE = {
    "task": "检索导师「陆伟」的候选论文",
    "mode": "rule",
    "tools": [],
    "steps": [
        {"index": 0, "kind": "tool_call", "name": "search_by_author",
         "args_digest": "institution=武汉大学&name=陆伟", "status": "ok",
         "duration_ms": 900, "summary": "count=25"},
        {"index": 1, "kind": "done", "name": "rule_pipeline", "status": "ok",
         "summary": "papers=25（规则路径，未使用大模型）"},
    ],
    "budget": {"steps": 2, "tokens": 0, "external_calls": 1, "elapsed_seconds": 1.4},
}


def test_rule_mode_is_detected():
    assert is_rule_mode(RULE_TRACE) is True
    assert is_rule_mode(SAMPLE_TRACE) is False
    assert is_rule_mode({}) is False


def test_rule_mode_hides_the_tool_count():
    """没有模型就谈不上"提供了哪些工具"；显示 0 会和"工具调用 2 次"自相矛盾。"""
    labels = [label for label, _ in trace_overview(RULE_TRACE)]

    assert "TOOLS" not in labels
    assert "TOOL CALLS" in labels
    assert dict(trace_overview(RULE_TRACE))["TOOL CALLS"] == "1"


def test_agent_mode_still_shows_the_tool_count():
    labels = [label for label, _ in trace_overview(SAMPLE_TRACE)]

    assert "TOOLS" in labels


def test_rule_mode_note_says_no_model_was_involved():
    note = trace_mode_note(RULE_TRACE)

    assert "未配置大模型" in note
    assert "确定性规则路径" in note
    assert "没有模型参与决策" in note


def test_agent_mode_has_no_rule_note():
    assert trace_mode_note(SAMPLE_TRACE) == ""


def test_user_steps_translate_internal_tool_names_into_plain_language():
    """若主界面再次暴露 search_by_author 这类内部名称，这个测试会失败。"""
    steps = ui_trace.trace_user_steps(SAMPLE_TRACE)

    assert steps == [
        {
            "label": "按导师姓名检索论文",
            "status": "已完成",
            "detail": "papers=25",
        }
    ]


def test_progress_uses_readable_stage_cards_instead_of_an_arrow_sentence():
    """若阶段退回到挤在一行的箭头文本，这个测试会失败。"""
    html = ui_trace.trace_progress_html(
        {"stages": ["seeding", "searching", "disambiguating", "done"]}
    )

    assert html.count('class="research-stage') == 4
    assert "开始检索" in html
    assert "扩展来源" in html
    assert "核对归属" in html
    assert "研究完成" in html
    assert "→" not in html
