"""Harness 运行轨迹：每一步都必须可追溯（工具、参数摘要、耗时、token、结果摘要）。"""

from __future__ import annotations

from advisor_fit.harness.trace import RunTrace, TraceStep, digest_args


def test_trace_records_steps_in_order_with_index():
    trace = RunTrace(task="检索导师论文")
    trace.add(kind="llm_decision", name="decide", duration_ms=12)
    trace.add(kind="tool_call", name="search_by_author", duration_ms=340, status="ok")

    assert [step.index for step in trace.steps] == [0, 1]
    assert trace.steps[1].name == "search_by_author"


def test_trace_totals_tokens_and_duration():
    trace = RunTrace(task="t")
    trace.add(kind="llm_decision", tokens_in=100, tokens_out=20, duration_ms=5)
    trace.add(kind="llm_decision", tokens_in=50, tokens_out=10, duration_ms=7)

    assert trace.total_tokens() == 180
    assert trace.total_duration_ms() == 12


def test_digest_args_is_stable_and_hides_long_values():
    first = digest_args({"name": "王伟", "institution": "武汉大学"})
    second = digest_args({"institution": "武汉大学", "name": "王伟"})

    assert first == second
    assert len(first) <= 80

    long_args = digest_args({"abstract": "x" * 5000})
    assert len(long_args) <= 80


def test_trace_serialises_for_repository_storage():
    trace = RunTrace(task="t")
    trace.add(kind="tool_call", name="search_by_title", status="error", error="超时")
    trace.degraded_reason = "MAX_EXTERNAL_CALLS"
    trace.budget = {"external_calls": 25}

    payload = trace.to_dict()

    assert payload["degraded_reason"] == "MAX_EXTERNAL_CALLS"
    assert payload["steps"][0]["error"] == "超时"
    assert payload["budget"]["external_calls"] == 25
    assert RunTrace(**{"task": "t", "steps": []}).steps == []


def test_step_never_stores_raw_full_arguments():
    """轨迹只存参数摘要，避免把整篇摘要或 CV 文本写进数据库。"""
    step = TraceStep(index=0, kind="tool_call", name="t", args_digest="name=王伟")

    assert "abstract" not in step.model_dump()


def test_run_loop_records_the_tool_surface_offered_to_the_model():
    """只记"实际调用了什么"不够——还要记"当时有哪些工具可选"。

    否则复盘时无法回答"它为什么没用另一个工具"。
    """
    from advisor_fit.harness.loop import run_loop
    from advisor_fit.harness.tools import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="search_by_author",
            description="按作者检索",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}},
            permission="network",
        ),
        lambda **_: {"count": 0},
    )
    registry.register(
        ToolSpec(name="search_by_title", description="按标题检索", permission="network"),
        lambda **_: {"count": 0},
    )

    trace = RunTrace(task="t")
    run_loop(_AlwaysDoneLLM(), task="t", registry=registry, trace=trace, max_steps=1)

    names = [spec["name"] for spec in trace.tools]
    assert names == ["search_by_author", "search_by_title"]
    # 参数 schema 也要带上，否则无法解释"模型为什么传错了参数"
    assert trace.tools[0]["parameters"]["properties"]["name"]["type"] == "string"
    assert trace.tools[0]["permission"] == "network"


class _AlwaysDoneLLM:
    """只回一个 done 的最小 LLM，用于观察 run_loop 在无工具调用时记录了什么。"""

    def generate(self, *, schema, instructions, payload):
        return schema(action="done", message="无需检索")
