"""Harness 工具治理层：把「工具」从裸函数升级为带能力声明与资源约束的注册项。

Phase 1 目标：
- 每个工具显式声明权限、限速、缓存 TTL、成本估算、超时与重试策略；
- 保留对旧的 (name, description, callable) 三元组的兼容，避免一次性改动过大。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, Field, field_validator

ToolFn = Callable[..., dict]
LegacyTool = tuple[str, str, ToolFn]

_ALLOWED_PERMISSIONS = {"read", "network", "write"}


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=5)
    backoff_seconds: float = Field(default=0.0, ge=0.0)


class ToolSpec(BaseModel):
    """工具的静态声明。运行期约束（计数、熔断）由 BudgetTracker 负责。"""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON Schema
    permission: str = "read"  # read | network | write
    rate_limit_per_run: int | None = None
    cache_ttl_seconds: int = 0
    cost_estimate: float = 0.0
    timeout_seconds: float = 20.0
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    @field_validator("permission")
    @classmethod
    def _known_permission(cls, value: str) -> str:
        if value not in _ALLOWED_PERMISSIONS:
            raise ValueError(f"未知权限: {value}")
        return value


class ToolRegistry:
    """工具的注册表。上层只通过它拿 spec 与 callable，不再直接持有裸函数列表。"""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[ToolSpec, ToolFn]] = {}

    def register(self, spec: ToolSpec, fn: ToolFn) -> None:
        if spec.name in self._entries:
            raise ValueError(f"tool already registered: {spec.name}")
        self._entries[spec.name] = (spec, fn)

    def get(self, name: str) -> tuple[ToolSpec, ToolFn] | None:
        return self._entries.get(name)

    def names(self) -> list[str]:
        return list(self._entries)

    def specs(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._entries.values()]

    def external_tool_names(self) -> set[str]:
        """会发起外部请求的工具名；预算闸门据此统计 external_calls。"""
        return {
            spec.name for spec, _ in self._entries.values() if spec.permission == "network"
        }

    @classmethod
    def from_pairs(cls, tools: Sequence[LegacyTool]) -> ToolRegistry:
        """从旧的 (name, description, callable) 三元组构建，元数据取默认值。"""
        registry = cls()
        for name, description, fn in tools:
            registry.register(ToolSpec(name=name, description=description), fn)
        return registry
