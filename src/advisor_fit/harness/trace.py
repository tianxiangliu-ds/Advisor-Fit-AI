"""Harness 运行轨迹：每一步都可追溯，且不把原始长文本写进数据库。

轨迹用于评测与复盘：知道 Agent 调了哪个工具、参数是什么（摘要）、耗时多久、
花了多少 token、结果成功还是失败。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

_MAX_VALUE_CHARS = 24
_MAX_DIGEST_CHARS = 80


def digest_args(args: dict[str, Any]) -> str:
    """把工具参数压成稳定、可读、且不会泄露长文本的摘要。"""
    if not args:
        return ""
    parts: list[str] = []
    for key in sorted(args):
        value = args[key]
        text = "" if value is None else " ".join(str(value).split())
        if len(text) > _MAX_VALUE_CHARS:
            text = f"{text[:_MAX_VALUE_CHARS]}…"
        parts.append(f"{key}={text}")
    digest = "&".join(parts)
    if len(digest) > _MAX_DIGEST_CHARS:
        digest = f"{digest[:_MAX_DIGEST_CHARS]}…"
    return digest


class TraceStep(BaseModel):
    index: int
    kind: str  # llm_decision | tool_call | confirmation | error | done
    name: str = ""
    args_digest: str = ""
    status: str = "ok"  # ok | error | skipped
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    summary: str = ""
    error: str = ""


class RunTrace(BaseModel):
    task: str = ""
    # 这次运行是哪种模式：agent（大模型决策）| rule（未配模型时的确定性规则路径）。
    # 界面据此决定措辞——规则路径没有"模型可选工具"这回事，不能假装有。
    mode: str = "agent"
    # 这次运行经过的工作流阶段（见 harness/workflow.py）。
    # 单独的字段而不是塞进 steps：阶段不是"一顿操作"，混进去会污染步骤计数与耗时统计。
    stages: list[str] = Field(default_factory=list)
    # 这次运行**提供给模型**的工具清单（名称 / 说明 / 参数 schema / 权限）。
    # 只记"实际调用了什么"是不够的——看不出 Agent 当时有哪些选择，也就无法复盘
    # "它为什么没选另一个工具"。
    tools: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[TraceStep] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    degraded_reason: str = ""

    def add(
        self,
        *,
        kind: str,
        name: str = "",
        args_digest: str = "",
        status: str = "ok",
        duration_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        summary: str = "",
        error: str = "",
    ) -> TraceStep:
        step = TraceStep(
            index=len(self.steps),
            kind=kind,
            name=name,
            args_digest=args_digest,
            status=status,
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            summary=summary,
            error=error,
        )
        self.steps.append(step)
        return step

    def total_tokens(self) -> int:
        return sum(step.tokens_in + step.tokens_out for step in self.steps)

    def mark_stage(self, stage: str) -> None:
        """记录进入某个工作流阶段。连续重复标记只记一次。"""
        value = str(stage)
        if not self.stages or self.stages[-1] != value:
            self.stages.append(value)

    def total_duration_ms(self) -> int:
        return sum(step.duration_ms for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
