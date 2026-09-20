"""Harness 预算闸门：超限一律优雅降级，绝不抛异常中断主流程。

上限数值与 `CLAUDE.md` 的「单次研究的资源上限（预算闸门）」保持一致。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel


class BudgetLimits(BaseModel):
    max_steps: int = 5
    max_llm_calls: int = 15
    max_tokens: int = 150_000
    max_external_calls: int = 25
    max_wall_time_seconds: float = 120.0
    max_calls_per_source: int = 8


class BudgetTracker:
    """按维度记账，并用 exceeded() 给出降级原因码（None 表示都没超）。"""

    def __init__(
        self,
        limits: BudgetLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or BudgetLimits()
        self._clock = clock
        self._started_at = clock()
        self.steps = 0
        self.llm_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.external_calls = 0
        self.per_source: dict[str, int] = {}

    def record_step(self) -> None:
        self.steps += 1

    def record_llm_call(self, *, tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.llm_calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out

    def record_external_call(self, source: str) -> None:
        self.external_calls += 1
        self.per_source[source] = self.per_source.get(source, 0) + 1

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def elapsed_seconds(self) -> float:
        return self._clock() - self._started_at

    def exceeded(self) -> str | None:
        """返回第一个触顶的资源码；顺序固定，便于测试与日志复现。"""
        limits = self.limits
        if self.steps >= limits.max_steps:
            return "MAX_STEPS"
        if self.llm_calls >= limits.max_llm_calls:
            return "MAX_LLM_CALLS"
        if self.tokens >= limits.max_tokens:
            return "MAX_TOKENS"
        if self.external_calls >= limits.max_external_calls:
            return "MAX_EXTERNAL_CALLS"
        for source, count in self.per_source.items():
            if count >= limits.max_calls_per_source:
                return f"SOURCE_RATE_LIMIT:{source}"
        if self.elapsed_seconds() >= limits.max_wall_time_seconds:
            return "MAX_WALL_TIME"
        return None

    def snapshot(self) -> dict:
        return {
            "steps": self.steps,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "external_calls": self.external_calls,
            "per_source": dict(self.per_source),
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
        }
