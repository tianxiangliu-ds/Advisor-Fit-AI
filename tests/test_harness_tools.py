"""Harness 工具治理层：ToolSpec / ToolRegistry 的契约测试。"""

from __future__ import annotations

import pytest

from advisor_fit.harness.tools import RetryPolicy, ToolRegistry, ToolSpec


def _echo(**kwargs) -> dict:
    return {"echo": kwargs}


def _spec(name: str = "echo", **overrides) -> ToolSpec:
    base = {
        "name": name,
        "description": "回显参数",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        "permission": "read",
        "rate_limit_per_run": 3,
        "cache_ttl_seconds": 600,
        "cost_estimate": 0.0,
        "timeout_seconds": 5.0,
        "retry": RetryPolicy(max_attempts=2, backoff_seconds=0.1),
    }
    base.update(overrides)
    return ToolSpec(**base)


def test_registry_stores_spec_and_callable():
    registry = ToolRegistry()
    registry.register(_spec(), _echo)

    assert registry.names() == ["echo"]
    spec, fn = registry.get("echo")
    assert spec.permission == "read"
    assert spec.rate_limit_per_run == 3
    assert fn(q="hi") == {"echo": {"q": "hi"}}


def test_registry_rejects_duplicate_name():
    registry = ToolRegistry()
    registry.register(_spec(), _echo)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_spec(), _echo)


def test_registry_get_returns_none_for_unknown_tool():
    assert ToolRegistry().get("missing") is None


def test_registry_specs_are_serialisable_for_prompt_payload():
    registry = ToolRegistry()
    registry.register(_spec(), _echo)

    payload = [spec.model_dump() for spec in registry.specs()]

    assert payload[0]["name"] == "echo"
    assert payload[0]["retry"]["max_attempts"] == 2


def test_registry_from_pairs_keeps_legacy_tuple_tools_working():
    """旧的 (name, description, callable) 三元组必须仍能注册，只是拿不到元数据。"""
    registry = ToolRegistry.from_pairs([("legacy", "旧工具", _echo)])

    spec, fn = registry.get("legacy")
    assert spec.description == "旧工具"
    assert spec.permission == "read"
    assert spec.timeout_seconds > 0
    assert fn(q="x") == {"echo": {"q": "x"}}


def test_network_permission_is_marked_so_budget_can_count_external_calls():
    registry = ToolRegistry()
    registry.register(_spec(name="search", permission="network"), _echo)

    assert registry.external_tool_names() == {"search"}


def test_spec_rejects_unknown_permission():
    with pytest.raises(ValueError):
        _spec(permission="root")
