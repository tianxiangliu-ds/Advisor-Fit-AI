"""把 Harness 的一次运行渲染成可读的 HTML（见 design.md 4.10）。

为什么单独成模块：Agent 的"自主性"如果不可见，用户只看得到结论，无法判断它是
查出来的还是编出来的。这里负责把 RunTrace 摊开成"调了哪些工具、每步多久、
成功还是失败、有没有触发资源上限"。

本模块**不依赖 Streamlit**，只产出 HTML 字符串，便于单测；真正落地到页面由
`app.py` 负责。
"""

from __future__ import annotations

from html import escape
from typing import Any

# 轨迹步骤类型 → 中文标签
KIND_LABELS: dict[str, str] = {
    "llm_decision": "模型决策",
    "tool_call": "工具调用",
    "confirmation": "人工确认",
    "error": "异常",
    "done": "结束",
}
STATUS_LABELS: dict[str, str] = {"ok": "成功", "error": "失败", "skipped": "已跳过"}

# 单个参数/结果摘要在界面上的最大长度
_MAX_DETAIL_CHARS = 72


def format_duration(ms: int) -> str:
    """毫秒转可读时长：不足 1 秒显示 ms，否则保留一位小数。"""
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"


def trace_overview(trace: dict[str, Any]) -> list[tuple[str, str]]:
    """概览行的数字：步数 / 工具数 / 工具调用 / 耗时 / token / 外部请求。

    规则模式（没配大模型）不显示「工具数」——没有模型就谈不上"提供了哪些工具"，
    显示 0 会让旁边"工具调用 3 次"看起来自相矛盾。
    """
    steps = trace.get("steps") or []
    tools = trace.get("tools") or []
    budget = trace.get("budget") or {}
    tool_calls = sum(1 for step in steps if step.get("kind") == "tool_call")
    elapsed = budget.get("elapsed_seconds")
    if isinstance(elapsed, (int, float)):
        elapsed_text = f"{float(elapsed):.1f}s"
    else:
        elapsed_text = format_duration(
            sum(int(step.get("duration_ms") or 0) for step in steps)
        )
    overview = [("STEPS", str(len(steps)))]
    if not is_rule_mode(trace):
        overview.append(("TOOLS", str(len(tools))))
    overview.extend([
        ("TOOL CALLS", str(tool_calls)),
        ("ELAPSED", elapsed_text),
        ("TOKENS", str(budget.get("tokens", 0))),
        ("NETWORK", str(budget.get("external_calls", 0))),
    ])
    return overview


def is_rule_mode(trace: dict[str, Any]) -> bool:
    """这次运行是不是"没配大模型"的确定性规则路径。"""
    return str(trace.get("mode") or "agent") == "rule"


def trace_mode_note(trace: dict[str, Any]) -> str:
    """规则模式的说明。

    必须说清楚：下面这些步骤是**真的**发生的检索，只是由固定流程而非模型决策驱动。
    否则用户会以为"这就是 Agent 在思考"，那是误导。
    """
    if not is_rule_mode(trace):
        return ""
    return (
        "本次未配置大模型，走的是**确定性规则路径**：按「机构 → 备选机构 → 代表论文标题」"
        "依次检索，再由机构规则做消歧。下面是它真实执行的每一步——但这次没有模型参与决策，"
        "所以不显示「可用工具数」。配上 LLM_API_KEY 后，同样的界面上会看到模型自己决定"
        "调哪个工具、如何扩搜的过程。"
    )


def trace_step_html(step: dict[str, Any]) -> str:
    """一行轨迹。参数与结果只放摘要，长文本不进界面。"""
    index = int(step.get("index") or 0) + 1
    kind = KIND_LABELS.get(str(step.get("kind", "")), str(step.get("kind", "")))
    status = str(step.get("status") or "ok")
    status_label = STATUS_LABELS.get(status, status)

    parts = [f"<i>{index}</i>", f"<em>{escape(kind)}</em>"]
    if step.get("name"):
        parts.append(f"<b>{escape(str(step['name']))}</b>")
    if step.get("args_digest"):
        parts.append(f"<code>{escape(str(step['args_digest']))}</code>")
    detail = str(step.get("summary") or step.get("error") or "")
    if detail:
        parts.append(f"<code>{escape(detail[:_MAX_DETAIL_CHARS])}</code>")
    duration = format_duration(int(step.get("duration_ms") or 0))
    parts.append(f'<span class="{escape(status)}">{escape(status_label)} · {duration}</span>')
    return '<div class="trace-step">' + "".join(parts) + "</div>"


def trace_panel_html(trace: dict[str, Any]) -> str:
    """任务行 + 概览行 + 全部步骤。调用方需保证 trace 里确实有内容。"""
    task = str(trace.get("task") or "").strip()
    task_html = f'<div class="trace-task">{escape(task)}</div>' if task else ""
    meta = "".join(
        f"<div><span>{escape(label)}</span><b>{escape(value)}</b></div>"
        for label, value in trace_overview(trace)
    )
    rows = "".join(trace_step_html(step) for step in (trace.get("steps") or []))
    return (
        f'<div class="trace-panel">{task_html}'
        f'<div class="trace-meta">{meta}</div>{rows}</div>'
    )


def trace_degraded_html(reason: str) -> str:
    """触发资源上限时的说明条：讲清"发生了什么"和"系统怎么处理的"。"""
    return (
        f'<div class="trace-degraded">本次触发了资源上限（{escape(str(reason))}）。'
        "Harness 按约定优雅降级：停止继续调用工具，保留已经拿到的证据，"
        "并在结论里标注来源不可用。</div>"
    )


def has_content(trace: dict[str, Any] | None) -> bool:
    """轨迹是否有可展示的东西（没跑过 Agent 时不显示空面板）。"""
    if not trace:
        return False
    return bool(trace.get("steps") or trace.get("tools"))
