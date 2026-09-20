"""工具契约必须被**真正执行**，不能只写在声明里。

背景：`ToolSpec` 上声明了单轮限流、超时、重试，但循环里一度只是 `fn(**args)`——
声明了却没生效。这种"看起来处理过了"的假象比不写更危险：读代码的人会以为一次
网络抖动已经被重试兜住了，实际上整轮检索就此丢掉。

这个文件专门盯"声明 == 行为"。
"""

from __future__ import annotations

import time

from advisor_fit.harness.budget import BudgetTracker
from advisor_fit.harness.loop import AgentDecision, run_loop
from advisor_fit.harness.tools import RetryPolicy, ToolRegistry, ToolSpec
from advisor_fit.harness.trace import RunTrace


class FakeLLM:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def generate(self, *, schema, instructions, payload):
        return self.decisions.pop(0)


def _one_call(tool: str = "flaky", **args) -> FakeLLM:
    return FakeLLM(
        [
            AgentDecision(action="tool", tool=tool, args=args),
            AgentDecision(action="done", message="结束"),
        ]
    )


def _registry(spec: ToolSpec, fn) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(spec, fn)
    return registry


def _tool_steps(trace: RunTrace) -> list:
    return [step for step in trace.steps if step.kind == "tool_call"]


# -- 重试 ----------------------------------------------------------------------


def test_retry_recovers_from_a_transient_failure():
    """第一次失败、第二次成功——这正是网络抖动该被兜住的场景。"""
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("连接超时")
        return {"papers": ["p1"]}

    registry = _registry(
        ToolSpec(name="flaky", description="会偶发失败的工具",
                 retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0)),
        flaky,
    )
    trace = RunTrace(task="t")
    steps = run_loop(_one_call(), registry=registry, trace=trace, max_steps=2)

    assert len(attempts) == 2, "第一次失败后应当重试"
    assert any(step.get("tool") == "flaky" and "p1" in step["result"].get("papers", [])
               for step in steps)
    assert _tool_steps(trace)[0].status == "ok"


def test_retry_stops_at_max_attempts_and_reports_the_error():
    attempts = []

    def always_fails():
        attempts.append(1)
        raise RuntimeError("HTTP 503")

    registry = _registry(
        ToolSpec(name="bad", description="总是失败",
                 retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0)),
        always_fails,
    )
    trace = RunTrace(task="t")
    steps = run_loop(_one_call(tool="bad"), registry=registry, trace=trace, max_steps=2)

    assert len(attempts) == 3
    assert any("HTTP 503" in str(step.get("result", {})) for step in steps)
    assert _tool_steps(trace)[0].status == "error"


def test_retry_count_is_visible_in_the_trace():
    """重试必须在轨迹里看得见，否则"重试过"这件事无从复盘。"""
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("boom")
        return {"count": 1}

    registry = _registry(
        ToolSpec(name="flaky", description="偶发失败",
                 retry=RetryPolicy(max_attempts=2, backoff_seconds=0.0)),
        flaky,
    )
    trace = RunTrace(task="t")
    run_loop(_one_call(), registry=registry, trace=trace, max_steps=2)

    assert "重试 1 次" in _tool_steps(trace)[0].summary


def test_default_retry_policy_does_not_retry():
    """没声明重试的工具不该被偷偷重试（比如有副作用的写操作）。"""
    calls = []

    def once():
        calls.append(1)
        raise RuntimeError("nope")

    registry = _registry(ToolSpec(name="once", description="不重试"), once)
    run_loop(_one_call(tool="once"), registry=registry, max_steps=2)

    assert len(calls) == 1


def test_retries_are_counted_against_the_external_budget():
    """重试也是真实外部请求，不能白花额度。"""
    def always_fails():
        raise RuntimeError("boom")

    registry = _registry(
        ToolSpec(name="net", description="联网工具", permission="network",
                 retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0)),
        always_fails,
    )
    budget = BudgetTracker()
    run_loop(_one_call(tool="net"), registry=registry, budget=budget, max_steps=2)

    assert budget.external_calls == 3


# -- 单轮限流 ------------------------------------------------------------------


def test_rate_limit_blocks_further_calls_to_the_same_tool():
    calls = []

    def search(name):
        calls.append(name)
        return {"count": 1}

    registry = _registry(
        ToolSpec(name="search", description="检索", permission="network",
                 rate_limit_per_run=2),
        search,
    )
    llm = FakeLLM([
        AgentDecision(action="tool", tool="search", args={"name": "a"}),
        AgentDecision(action="tool", tool="search", args={"name": "b"}),
        AgentDecision(action="tool", tool="search", args={"name": "c"}),
        AgentDecision(action="done", message="结束"),
    ])
    trace = RunTrace(task="t")
    steps = run_loop(llm, registry=registry, trace=trace, max_steps=4)

    assert calls == ["a", "b"], "第 3 次不该真的调用工具"
    assert any("已达上限" in str(step.get("result", {})) for step in steps)

    statuses = [step.status for step in _tool_steps(trace)]
    assert statuses == ["ok", "ok", "skipped"]


def test_rate_limit_is_per_tool_not_global():
    a_calls, b_calls = [], []

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="a", description="甲", rate_limit_per_run=1),
        lambda: a_calls.append(1) or {"count": 1},
    )
    registry.register(
        ToolSpec(name="b", description="乙", rate_limit_per_run=1),
        lambda: b_calls.append(1) or {"count": 1},
    )
    llm = FakeLLM([
        AgentDecision(action="tool", tool="a", args={}),
        AgentDecision(action="tool", tool="b", args={}),
        AgentDecision(action="done", message="结束"),
    ])
    run_loop(llm, registry=registry, max_steps=3)

    assert a_calls == [1]
    assert b_calls == [1]


def test_tool_without_rate_limit_can_be_called_repeatedly():
    calls = []

    registry = _registry(
        ToolSpec(name="free", description="不限流"),
        lambda: calls.append(1) or {"count": 1},
    )
    llm = FakeLLM([
        AgentDecision(action="tool", tool="free", args={}),
        AgentDecision(action="tool", tool="free", args={}),
        AgentDecision(action="tool", tool="free", args={}),
        AgentDecision(action="done", message="结束"),
    ])
    run_loop(llm, registry=registry, max_steps=4)

    assert len(calls) == 3


# -- 超时 ----------------------------------------------------------------------


def test_tool_that_hangs_is_cut_off_and_the_loop_continues():
    """一个卡住的工具不能拖死整轮——这是上线后最难受的一类故障。"""

    def hangs():
        time.sleep(3)
        return {"count": 1}

    registry = _registry(
        ToolSpec(name="slow", description="会卡住的工具", timeout_seconds=0.2),
        hangs,
    )
    trace = RunTrace(task="t")
    started = time.monotonic()
    steps = run_loop(_one_call(tool="slow"), registry=registry, trace=trace, max_steps=2)
    elapsed = time.monotonic() - started

    assert elapsed < 2.5, f"应当被超时切断，实际用了 {elapsed:.1f}s"
    assert any("未返回" in str(step.get("result", {})) for step in steps)
    assert steps[-1] == {"done": "结束"}
    assert _tool_steps(trace)[0].status == "error"


def test_fast_tool_is_unaffected_by_the_timeout_wrapper():
    registry = _registry(
        ToolSpec(name="quick", description="很快", timeout_seconds=5.0),
        lambda: {"count": 7},
    )
    steps = run_loop(_one_call(tool="quick"), registry=registry, max_steps=2)

    assert any(step.get("result", {}).get("count") == 7 for step in steps)
