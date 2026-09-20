"""上下文组装：决定"每一轮到底把什么喂给模型"。

为什么需要单独一层：循环原本把 `history` 原样塞进 payload，而每一步的工具结果里
带着**整份论文列表**。实测三轮下来 payload 从 234 字符涨到 **29,014 字符**——
既突破了 `CLAUDE.md` 规定的「单页文本入 LLM ≤ 12k 字符」，又随步数无限膨胀，
而其中绝大部分是**重复的论文列表**。

但模型其实不需要看到全文。它要的是"我调过什么、拿到了几条、机构分布如何"，
据此决定下一步是扩搜、换库、还是请人确认。所以这里把历史**压缩成摘要**：

- 工具结果只保留条数、机构分布与前几个标题，丢掉摘要等长字段；
- 最近的步骤保留得详细些（模型正要据此决策），更早的只留一行；
- 最后再兜一道硬上限：超了就从最旧的开始降级，直到装得下。

**被压缩的只是喂给模型的那份副本**；`run_loop` 返回给调用方的 `steps` 仍是完整的，
界面与下游拿到的数据不受影响。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 论文/结果列表里最占地方的字段——摘要动辄几百字，而模型决策时用不到
_BULKY_FIELDS = ("abstract", "profile_text", "keywords", "topics", "authors", "sources")

# 历史里"结果"这个键的别名：不同工具返回的字段名不一样
_RESULT_LIST_KEYS = ("papers", "items", "results", "works", "claims", "verdicts")


@dataclass(frozen=True)
class ContextBudget:
    """一次入模型的上下文预算。默认值与 CLAUDE.md 的上限一致。"""

    max_chars: int = 12_000
    # 最近几步保留"较详细"的摘要（含标题样本），更早的只留一行计数
    detailed_steps: int = 2
    # 每条摘要里最多列几个标题
    max_titles: int = 5
    # 每条摘要里最多列几个机构
    max_institutions: int = 5


def _institutions(items: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for item in items:
        value = str(item.get("institution") or "").strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def _titles(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("title") or "")[:60] for item in items if item.get("title")]


def compact_result(result: Any, *, budget: ContextBudget, detailed: bool) -> Any:
    """把一次工具结果压成决策够用的摘要。

    非列表结果（错误、计数、状态）原样保留——它们本来就不大，而且模型必须看到
    「这个工具失败了」才能换招。
    """
    if not isinstance(result, dict):
        return result

    compacted: dict[str, Any] = {}
    for key, value in result.items():
        if key in _BULKY_FIELDS:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            # 论文/条目列表：只留条数、机构分布与标题样本
            digest: dict[str, Any] = {"count": len(value)}
            institutions = _institutions(value)
            if institutions:
                digest["institutions"] = institutions[: budget.max_institutions]
            if detailed:
                titles = _titles(value)
                if titles:
                    digest["titles"] = titles[: budget.max_titles]
            compacted[key] = digest
        else:
            compacted[key] = value
    return compacted


def compact_step(step: dict[str, Any], *, budget: ContextBudget, detailed: bool) -> dict[str, Any]:
    """压缩一条历史步骤。"""
    if "result" not in step:
        # done / confirmed / needs_confirmation / error 这类控制步骤，原文保留
        return step
    compacted = {k: v for k, v in step.items() if k != "result"}
    compacted["result"] = compact_result(step["result"], budget=budget, detailed=detailed)
    return compacted


def build_history(
    steps: list[dict[str, Any]], *, budget: ContextBudget | None = None
) -> list[dict[str, Any]]:
    """把完整步骤列表压成可入模型的历史。

    最近的 `detailed_steps` 条保留标题样本，更早的只留"调了什么、拿到几条"。
    """
    budget = budget or ContextBudget()
    total = len(steps)
    history: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        detailed = (total - index) <= budget.detailed_steps
        history.append(compact_step(step, budget=budget, detailed=detailed))
    return history


def _size(payload: Any) -> int:
    import json

    return len(json.dumps(payload, ensure_ascii=False))


def build_payload(
    *,
    task: str,
    tools: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    budget: ContextBudget | None = None,
) -> dict[str, Any]:
    """组装这一轮喂给模型的 payload，并保证不超过预算。

    超预算时**从最旧的步骤开始降级**（先去掉标题样本，再去掉整条），
    工具清单与任务描述永远保留——没有它们模型就无法决策。
    """
    budget = budget or ContextBudget()
    history = build_history(steps, budget=budget)
    payload = {"task": task, "tools": tools, "history": history}

    if _size(payload) <= budget.max_chars:
        return payload

    # 第一轮降级：把所有步骤都退回"只有计数"的形态
    history = [
        compact_step(step, budget=budget, detailed=False) for step in steps
    ]
    payload = {"task": task, "tools": tools, "history": history}
    if _size(payload) <= budget.max_chars:
        return payload

    # 第二轮降级：从最旧的开始整条丢弃，直到装得下（最近的必须留下）
    trimmed = list(history)
    while len(trimmed) > 1 and _size(
        {"task": task, "tools": tools, "history": trimmed}
    ) > budget.max_chars:
        trimmed.pop(0)
    if _size({"task": task, "tools": tools, "history": trimmed}) > budget.max_chars:
        # 连一步都装不下：至少保证任务与工具在，历史留最后一条的空壳
        trimmed = [{"note": "更早的步骤因上下文预算被省略"}]
    return {"task": task, "tools": tools, "history": trimmed}
