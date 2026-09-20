"""最小 Agent 循环（照 pi-agent-core 思想）+ 工具治理 / 预算闸门 / 运行轨迹。

LLM 负责决策（下一步调哪个工具 / 请求人工确认 / 结束），
循环负责执行工具并把结果喂回，直到结束、需要人工确认或预算用尽。

**工具契约是在这里被真正执行的**：单轮限流、超时、重试都按 `ToolSpec` 的声明生效，
不是"声明了好看"。这一点很重要——如果声明了重试却没重试，一次网络抖动就会丢掉
整轮检索，而读代码的人会以为已经处理过了。

工具执行失败不阻断循环，错误结果回传给 LLM 让它自己换招；
预算超限不抛异常，而是记录降级原因后正常返回。
"""

from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from pydantic import BaseModel

from advisor_fit.harness.budget import BudgetTracker
from advisor_fit.harness.context import ContextBudget, build_payload
from advisor_fit.harness.tools import LegacyTool, ToolRegistry, ToolSpec
from advisor_fit.harness.trace import RunTrace, digest_args
from advisor_fit.llm.prompts import prompt_text
from advisor_fit.llm.provider import LLMUnavailable

# 兼容旧的 (name, description, callable) 三元组写法
Tool = LegacyTool

# 工具超时靠线程池实现：工具都是 I/O 密集（HTTP、解析），一个卡住的调用会拖死整轮。
_MAX_TOOL_THREADS = 4

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


def _call_with_timeout(fn, args: dict[str, Any], timeout: float, executor):
    """执行工具；超过声明的时间没返回就判定超时。

    没有线程池（或未声明超时）时退化为直接调用，行为与以前一致。
    """
    if executor is None or not timeout or timeout <= 0:
        return fn(**args)
    future = executor.submit(lambda: fn(**args))
    try:
        return future.result(timeout=timeout)
    except FuturesTimeout as exc:
        future.cancel()
        raise TimeoutError(f"工具超过 {timeout:g} 秒未返回") from exc


def _execute_tool(
    spec: ToolSpec,
    fn,
    args: dict[str, Any],
    *,
    executor,
    budget: BudgetTracker | None,
    external: bool,
) -> tuple[Any, str, str, int, int]:
    """按契约执行一次工具调用：超时 + 重试 + 外部请求记账。

    返回（结果, 状态, 错误, 实际尝试次数, 耗时毫秒）。
    """
    attempts = 0
    last_error = ""
    started = time.monotonic()
    max_attempts = max(1, spec.retry.max_attempts)

    while attempts < max_attempts:
        attempts += 1
        if external and budget is not None:
            # 每次尝试都是一次真实外部请求，都要计入预算——重试也不能白花
            budget.record_external_call(spec.name)
        try:
            result = _call_with_timeout(fn, args, spec.timeout_seconds, executor)
            return result, "ok", "", attempts, _elapsed_ms(started)
        except Exception as exc:  # noqa: BLE001 - 失败要回传给 LLM，不中断循环
            last_error = str(exc) or type(exc).__name__
            if attempts < max_attempts and spec.retry.backoff_seconds > 0:
                time.sleep(spec.retry.backoff_seconds * attempts)  # 线性退避

    return {"error": last_error}, "error", last_error, attempts, _elapsed_ms(started)


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
    context_budget: ContextBudget | None = None,
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
    # 记下这次给了模型哪些工具，便于复盘"它当时有哪些选择、为什么没选另一个"
    trace.tools = _tool_payload(registry)

    steps = list(resume_steps or [])
    granted = set(granted_confirmations or [])
    context_budget = context_budget or ContextBudget()
    # 单轮调用次数，用于执行 spec.rate_limit_per_run
    tool_calls: Counter[str] = Counter()

    executor = ThreadPoolExecutor(
        max_workers=_MAX_TOOL_THREADS, thread_name_prefix="agent-tool"
    )
    try:
        for _ in range(max_steps):
            if budget is not None:
                reason = budget.exceeded()
                if reason is not None:
                    trace.degraded_reason = reason
                    trace.budget = budget.snapshot()
                    steps.append({"budget_exhausted": reason})
                    return steps
                budget.record_step()

            # 上下文压缩：steps 里带着整份论文列表，原样喂给模型会随步数无限膨胀
            payload = build_payload(
                task=task, tools=_tool_payload(registry), steps=steps, budget=context_budget
            )
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

            # 容错：模型有时把**工具名直接写进 action**，而不是 action="tool" + tool="名字"。
            # 实测 DeepSeek 会这样（轨迹里表现为"未知动作: check_paper_affiliations"，
            # 白白浪费一步）。只要这个名字确实是已注册的工具，就当成一次工具调用。
            action, tool_name = decision.action, decision.tool
            if action not in ("tool", "confirm", "done") and registry.get(action) is not None:
                action, tool_name = "tool", action

            if action == "done":
                trace.add(kind="done", name="done", summary=decision.message)
                steps.append({"done": decision.message})
                return steps
            if action == "confirm":
                if decision.message in granted:
                    trace.add(kind="confirmation", name="granted", summary=decision.message)
                    steps.append({"confirmed": decision.message})
                    continue
                trace.add(kind="confirmation", name="requested", summary=decision.message)
                steps.append({"needs_confirmation": decision.message})
                return steps
            if action == "tool":
                steps.append(_run_tool_step(
                    decision, tool_name, registry, trace, tool_calls, executor,
                    budget, external_tools,
                ))
                continue
            steps.append({"error": f"未知动作: {decision.action}"})
            trace.add(kind="error", status="error", error=f"未知动作: {decision.action}")
    finally:
        # 不等待：超时的调用可能还在后台跑，等它会把这轮拖回原样
        executor.shutdown(wait=False)

    if budget is not None:
        trace.budget = budget.snapshot()
    return steps


def _run_tool_step(
    decision: AgentDecision,
    tool_name: str | None,
    registry: ToolRegistry,
    trace: RunTrace,
    tool_calls: Counter[str],
    executor,
    budget: BudgetTracker | None,
    external_tools: set[str],
) -> dict:
    """执行一步工具调用，并把该记的都记上。返回追加到 steps 的那条记录。"""
    entry = registry.get(tool_name or "")
    if entry is None:
        trace.add(
            kind="error",
            name=tool_name or "",
            status="error",
            error=f"未知工具: {tool_name}",
        )
        return {"error": f"未知工具: {tool_name}"}

    spec, fn = entry
    args_digest = digest_args(decision.args)

    # 契约里的单轮限流必须真的拦住——否则模型可以在一个工具上反复烧额度
    if spec.rate_limit_per_run is not None and tool_calls[spec.name] >= spec.rate_limit_per_run:
        message = f"{spec.name} 本轮调用已达上限（{spec.rate_limit_per_run} 次）"
        trace.add(
            kind="tool_call", name=spec.name, args_digest=args_digest,
            status="skipped", error=message,
        )
        return {"tool": tool_name, "args": decision.args, "result": {"error": message}}

    tool_calls[spec.name] += 1
    result, status, error, attempts, duration = _execute_tool(
        spec, fn, decision.args,
        executor=executor, budget=budget, external=spec.name in external_tools,
    )
    summary = _summarise(result)
    if attempts > 1:
        summary = f"{summary}（重试 {attempts - 1} 次）"
    trace.add(
        kind="tool_call",
        name=spec.name,
        args_digest=args_digest,
        status=status,
        duration_ms=duration,
        summary=summary,
        error=error,
    )
    return {"tool": tool_name, "args": decision.args, "result": result}
