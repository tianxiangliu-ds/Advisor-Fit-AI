"""最小 Agent 循环（照 pi-agent-core 思想）+ 工具治理 / 预算闸门 / 运行轨迹。

LLM 负责决策（下一步调哪个工具 / 请求人工确认 / 结束），
循环负责执行工具并把结果喂回，直到结束、需要人工确认或预算用尽。
工具执行失败不阻断循环，错误结果回传给 LLM；
预算超限不抛异常，而是记录降级原因后正常返回。
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from advisor_fit.harness.budget import BudgetTracker
from advisor_fit.harness.tools import LegacyTool, ToolRegistry
from advisor_fit.harness.trace import RunTrace, digest_args
from advisor_fit.llm.prompts import prompt_text
from advisor_fit.llm.provider import LLMUnavailable

# 兼容旧的 (name, description, callable) 三元组写法
Tool = LegacyTool

_DECIDE_INSTRUCTIONS = prompt_text("agent_decide")


class AgentDecision(BaseModel):
    action: str  # tool | confirm | done
    tool: str | None = None
    args: dict[str, Any] = {}
    message: str = ""


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _summarise(result: Any) -> str:
    """只留一行可读摘要，避免把整篇论文列表写进轨迹。"""
    if not isinstance(result, dict):
        return type(result).__name__
    if "error" in result:
        return f"error={result['error']}"[:60]
    for key in ("count", "papers", "claims", "verdicts"):
        if key in result:
            value = result[key]
            return f"{key}={len(value)}" if isinstance(value, list) else f"{key}={value}"
    return ",".join(sorted(result))[:60]


def _tool_payload(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            "permission": spec.permission,
        }
        for spec in registry.specs()
    ]


def run_loop(
    llm,
    tools: list[Tool] | None = None,
    task: str = "",
    *,
    max_steps: int = 5,
    resume_steps: list[dict] | None = None,
    granted_confirmations: list[str] | None = None,
    registry: ToolRegistry | None = None,
    budget: BudgetTracker | None = None,
    trace: RunTrace | None = None,
) -> list[dict]:
    """反复「LLM 决策 → 执行工具 → 结果回传」，直到 done/confirm 或步数用尽。

    支持断点续跑：传入 resume_steps（上轮已产生的步骤）与 granted_confirmations
    （用户已确认的消息），遇到相同的 confirm 时视为已授权，继续循环而非再次暂停。
    """
    if registry is None:
        registry = ToolRegistry.from_pairs(tools or [])
    if trace is None:
        trace = RunTrace(task=task)
    external_tools = registry.external_tool_names()

    steps = list(resume_steps or [])
    granted = set(granted_confirmations or [])

    for _ in range(max_steps):
        if budget is not None:
            reason = budget.exceeded()
            if reason is not None:
                trace.degraded_reason = reason
                trace.budget = budget.snapshot()
                steps.append({"budget_exhausted": reason})
                return steps
            budget.record_step()

        payload = {"task": task, "tools": _tool_payload(registry), "history": steps}
        started = time.monotonic()
        try:
            decision = llm.generate(
                schema=AgentDecision,
                instructions=_DECIDE_INSTRUCTIONS,
                payload=payload,
            )
        except LLMUnavailable as exc:
            trace.add(
                kind="llm_decision",
                name="decide",
                status="skipped",
                duration_ms=_elapsed_ms(started),
                error=str(exc),
            )
            break
        trace.add(
            kind="llm_decision",
            name="decide",
            duration_ms=_elapsed_ms(started),
            summary=getattr(decision, "action", "invalid"),
        )
        if not isinstance(decision, AgentDecision):
            steps.append({"error": "LLM 输出无效"})
            break
        if decision.action == "done":
            trace.add(kind="done", name="done", summary=decision.message)
            steps.append({"done": decision.message})
            return steps
        if decision.action == "confirm":
            if decision.message in granted:
                trace.add(kind="confirmation", name="granted", summary=decision.message)
                steps.append({"confirmed": decision.message})
                continue
            trace.add(kind="confirmation", name="requested", summary=decision.message)
            steps.append({"needs_confirmation": decision.message})
            return steps
        if decision.action == "tool":
            entry = registry.get(decision.tool or "")
            if entry is None:
                steps.append({"error": f"未知工具: {decision.tool}"})
                trace.add(
                    kind="error",
                    name=decision.tool or "",
                    status="error",
                    error=f"未知工具: {decision.tool}",
                )
                continue
            spec, fn = entry
            if budget is not None and spec.name in external_tools:
                budget.record_external_call(spec.name)
            args_digest = digest_args(decision.args)
            started = time.monotonic()
            try:
                result = fn(**decision.args)
                status, error = "ok", ""
            except Exception as exc:  # noqa: BLE001 - 工具失败回传给 LLM
                result = {"error": str(exc)}
                status, error = "error", str(exc)
            trace.add(
                kind="tool_call",
                name=spec.name,
                args_digest=args_digest,
                status=status,
                duration_ms=_elapsed_ms(started),
                summary=_summarise(result),
                error=error,
            )
            steps.append({"tool": decision.tool, "args": decision.args, "result": result})
            continue
        steps.append({"error": f"未知动作: {decision.action}"})
        trace.add(
            kind="error", status="error", error=f"未知动作: {decision.action}"
        )

    if budget is not None:
        trace.budget = budget.snapshot()
    return steps
