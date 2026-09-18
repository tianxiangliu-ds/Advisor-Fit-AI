"""最小 Agent 循环（照 pi-agent-core 思想）。

LLM 负责决策（下一步调哪个工具 / 请求人工确认 / 结束），
循环负责执行工具并把结果喂回，直到结束或达到步数上限。
工具执行失败不阻断循环，错误结果回传给 LLM。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from advisor_fit.llm.provider import LLMUnavailable

Tool = tuple[str, str, Callable[..., dict]]  # (name, description, callable)


class AgentDecision(BaseModel):
    action: str  # tool | confirm | done
    tool: str | None = None
    args: dict[str, Any] = {}
    message: str = ""


_DECIDE_INSTRUCTIONS = (
    "你是导师研究 Agent 的决策层。根据任务和已执行步骤，决定下一步："
    "调用一个可用工具（action=tool，给出 tool 名与 args）、"
    "请求人工确认（action=confirm，给出 message 说明要确认什么）、"
    "或结束（action=done，给出 message）。只能调用 payload 中列出的工具。"
)


def run_loop(
    llm,
    tools: list[Tool],
    task: str,
    *,
    max_steps: int = 5,
) -> list[dict]:
    """反复「LLM 决策 → 执行工具 → 结果回传」，直到 done/confirm 或步数用尽。"""
    tool_map = {name: fn for name, _, fn in tools}
    steps: list[dict] = []
    for _ in range(max_steps):
        payload = {
            "task": task,
            "tools": [{"name": name, "description": desc} for name, desc, _ in tools],
            "history": steps,
        }
        try:
            decision = llm.generate(
                schema=AgentDecision,
                instructions=_DECIDE_INSTRUCTIONS,
                payload=payload,
            )
        except LLMUnavailable:
            break
        if not isinstance(decision, AgentDecision):
            steps.append({"error": "LLM 输出无效"})
            break
        if decision.action == "done":
            steps.append({"done": decision.message})
            return steps
        if decision.action == "confirm":
            steps.append({"needs_confirmation": decision.message})
            return steps
        if decision.action == "tool":
            fn = tool_map.get(decision.tool or "")
            if fn is None:
                steps.append({"error": f"未知工具: {decision.tool}"})
                continue
            try:
                result = fn(**decision.args)
            except Exception as exc:  # noqa: BLE001 - 工具失败回传给 LLM
                result = {"error": str(exc)}
            steps.append({"tool": decision.tool, "args": decision.args, "result": result})
            continue
        steps.append({"error": f"未知动作: {decision.action}"})
    return steps
