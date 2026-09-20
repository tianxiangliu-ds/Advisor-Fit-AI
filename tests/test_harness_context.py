"""上下文预算测试。

背景：循环原本把 `history` 原样塞进 payload，而每一步的工具结果里带着**整份论文
列表**。实测三轮下来 payload 从 234 字符涨到 29,014 字符——既突破了
`CLAUDE.md` 规定的「单页文本入 LLM ≤ 12k 字符」，又随步数无限膨胀，
而其中绝大部分是重复的论文列表。

关键约束：**被压缩的只是喂给模型的那份副本**。`run_loop` 返回给调用方的 steps
必须仍是完整数据，否则界面与下游会拿到残缺结果。
"""

from __future__ import annotations

import json

from advisor_fit.harness.context import (
    ContextBudget,
    build_history,
    build_payload,
    compact_result,
)
from advisor_fit.harness.loop import AgentDecision, run_loop
from advisor_fit.harness.tools import ToolRegistry, ToolSpec

TOOLS = [{"name": "search_by_author", "description": "按作者查", "parameters": {},
          "permission": "network"}]


def _paper(index: int) -> dict:
    return {
        "title": f"面向知识图谱的实体抽取方法研究（第{index}篇）",
        "authors": ["陆伟", "张三"],
        "institution": "武汉大学信息管理学院",
        "year": 2020 + index % 5,
        "abstract": "本文研究了面向知识图谱的实体抽取方法。" * 20,
        "source_url": f"https://example.com/{index}",
        "belongs": True,
        "needs_review": False,
    }


def _step(index: int, papers: int, *, tool: str = "search_by_author") -> dict:
    return {
        "tool": tool,
        "args": {"name": "陆伟"},
        "result": {"count": papers, "papers": [_paper(i) for i in range(papers)]},
    }


# -- 压缩本身 ------------------------------------------------------------------


def test_result_lists_become_counts_and_institutions():
    compacted = compact_result(
        {"count": 3, "papers": [_paper(i) for i in range(3)]},
        budget=ContextBudget(), detailed=True,
    )

    assert compacted["papers"]["count"] == 3
    assert compacted["papers"]["institutions"] == ["武汉大学信息管理学院"]
    assert compacted["papers"]["titles"]


def test_bulky_fields_are_dropped():
    """摘要动辄几百字，而模型决策时用不到。"""
    compacted = compact_result(
        {"papers": [_paper(0)]}, budget=ContextBudget(), detailed=True
    )

    serialised = json.dumps(compacted, ensure_ascii=False)
    assert "本文研究了面向知识图谱的实体抽取方法" not in serialised
    assert "abstract" not in serialised


def test_older_steps_lose_title_samples_but_keep_counts():
    """最近几步保留标题（模型正要据此决策），更早的只留计数。"""
    steps = [_step(i, 5) for i in range(5)]

    history = build_history(steps, budget=ContextBudget(detailed_steps=2))

    assert "titles" in history[-1]["result"]["papers"]
    assert "titles" in history[-2]["result"]["papers"]
    assert "titles" not in history[0]["result"]["papers"]
    assert history[0]["result"]["papers"]["count"] == 5


def test_control_steps_are_kept_verbatim():
    """done / 错误这类控制步骤本来就不大，而且模型必须看到它才能决策。"""
    history = build_history([{"done": "够了"}, {"error": "未知工具"}])

    assert history[0] == {"done": "够了"}
    assert history[1] == {"error": "未知工具"}


def test_error_results_are_not_compacted_away():
    """工具失败了必须让模型看见，否则它会一直重试同一个坏调用。"""
    step = {"tool": "x", "args": {}, "result": {"error": "HTTP 503"}}

    history = build_history([step])

    assert history[0]["result"]["error"] == "HTTP 503"


# -- 预算 ----------------------------------------------------------------------


def test_payload_stays_within_budget_with_many_large_steps():
    """这是这套机制存在的理由：步数多、结果大，也要装得下。"""
    steps = [_step(i, 40) for i in range(6)]

    payload = build_payload(task="检索导师", tools=TOOLS, steps=steps)
    size = len(json.dumps(payload, ensure_ascii=False))

    assert size <= ContextBudget().max_chars, f"实际 {size:,} 字符"


def test_task_and_tools_survive_even_when_history_must_be_dropped():
    """历史可以砍，任务描述与工具清单不能砍——没有它们模型无法决策。"""
    steps = [_step(i, 400) for i in range(20)]

    payload = build_payload(
        task="检索导师", tools=TOOLS, steps=steps,
        budget=ContextBudget(max_chars=1_500),
    )

    assert payload["task"] == "检索导师"
    assert payload["tools"] == TOOLS
    assert len(json.dumps(payload, ensure_ascii=False)) <= 1_500


def test_recent_history_is_preferred_when_trimming():
    """装不下时砍最旧的，不能砍最新的——模型正要据此决策。"""
    steps = [_step(i, 30) for i in range(10)]
    for index, step in enumerate(steps):
        step["marker"] = index  # 标在步骤上，压缩时不会被丢掉

    payload = build_payload(
        # 预算故意压到装不下全部：压缩后的单条约 150 字符，10 条约 1500
        task="t", tools=TOOLS, steps=steps, budget=ContextBudget(max_chars=700)
    )

    markers = [item.get("marker") for item in payload["history"] if "marker" in item]
    assert markers, "至少要留下一步"
    assert markers[-1] == 9, "最近一步必须在"
    assert markers[0] > 0, "被砍掉的应当是最旧的"
    assert len(json.dumps(payload, ensure_ascii=False)) <= 700


# -- 循环里的实际效果 ----------------------------------------------------------


def test_run_loop_compacts_what_the_model_sees_but_not_what_it_returns():
    """压缩只作用于喂给模型的那份副本；返回给调用方的仍是完整数据。"""
    seen_sizes: list[int] = []
    papers = [_paper(i) for i in range(37)]

    class Recorder:
        def generate(self, *, schema, instructions, payload):
            seen_sizes.append(len(json.dumps(payload, ensure_ascii=False)))
            if len(seen_sizes) < 3:
                return schema(action="tool", tool="search_by_author", args={"name": "陆伟"})
            return schema(action="done", message="够了")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="search_by_author", description="按作者查", permission="network"),
        lambda name: {"count": len(papers), "papers": papers},
    )

    steps = run_loop(Recorder(), registry=registry, task="检索导师", max_steps=4)

    assert all(size <= ContextBudget().max_chars for size in seen_sizes), seen_sizes
    # 三个工具结果各带 37 篇，返回给调用方的仍是原样
    returned = [s for s in steps if s.get("tool")]
    assert returned and len(returned[0]["result"]["papers"]) == 37


def test_run_loop_accepts_a_custom_budget():
    seen: list[int] = []

    class Recorder:
        def generate(self, *, schema, instructions, payload):
            seen.append(len(json.dumps(payload, ensure_ascii=False)))
            return schema(action="done", message="够了")

    registry = ToolRegistry()
    registry.register(ToolSpec(name="search_by_author", description="x"), lambda name: {})
    run_loop(
        Recorder(), registry=registry, task="t", max_steps=1,
        context_budget=ContextBudget(max_chars=500),
    )

    assert seen and seen[0] <= 500


def test_agent_decision_defaults_stay_intact():
    """压缩不该影响决策模型本身。"""
    decision = AgentDecision(action="tool", tool="search_by_author", args={"name": "陆伟"})

    assert decision.tool == "search_by_author"
    assert decision.args == {"name": "陆伟"}
