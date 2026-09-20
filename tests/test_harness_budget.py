"""Harness 预算闸门：超限必须优雅降级，不能抛异常中断。"""

from __future__ import annotations

from advisor_fit.harness.budget import BudgetLimits, BudgetTracker


def test_tracker_counts_each_resource_kind():
    tracker = BudgetTracker(BudgetLimits(max_steps=5, max_llm_calls=3, max_tokens=100))
    tracker.record_step()
    tracker.record_llm_call(tokens_in=40, tokens_out=10)
    tracker.record_external_call("wanfang")

    snapshot = tracker.snapshot()

    assert snapshot["steps"] == 1
    assert snapshot["llm_calls"] == 1
    assert snapshot["tokens"] == 50
    assert snapshot["external_calls"] == 1


def test_exceeded_returns_reason_code_when_a_limit_is_hit():
    tracker = BudgetTracker(BudgetLimits(max_external_calls=1))
    assert tracker.exceeded() is None

    tracker.record_external_call("wanfang")

    assert tracker.exceeded() == "MAX_EXTERNAL_CALLS"


def test_exceeded_prioritises_steps_then_calls_then_tokens():
    tracker = BudgetTracker(
        BudgetLimits(max_steps=1, max_llm_calls=1, max_tokens=10, max_external_calls=99)
    )
    tracker.record_step()
    assert tracker.exceeded() == "MAX_STEPS"

    tracker.record_llm_call(tokens_in=100, tokens_out=0)
    assert tracker.exceeded() == "MAX_STEPS"


def test_token_limit_counts_input_plus_output():
    tracker = BudgetTracker(BudgetLimits(max_tokens=100))
    tracker.record_llm_call(tokens_in=60, tokens_out=39)
    assert tracker.exceeded() is None

    tracker.record_llm_call(tokens_in=1, tokens_out=0)
    assert tracker.exceeded() == "MAX_TOKENS"


def test_wall_time_limit_uses_injected_clock():
    now = {"t": 0.0}
    tracker = BudgetTracker(
        BudgetLimits(max_wall_time_seconds=10.0), clock=lambda: now["t"]
    )
    assert tracker.exceeded() is None

    now["t"] = 11.0

    assert tracker.exceeded() == "MAX_WALL_TIME"


def test_same_source_rate_limit_is_reported_separately():
    tracker = BudgetTracker(BudgetLimits(max_external_calls=99, max_calls_per_source=2))
    tracker.record_external_call("wanfang")
    assert tracker.exceeded() is None

    tracker.record_external_call("wanfang")

    assert tracker.exceeded() == "SOURCE_RATE_LIMIT:wanfang"


def test_default_limits_match_the_documented_budget_gate():
    limits = BudgetLimits()

    assert limits.max_steps == 5
    assert limits.max_llm_calls == 15
    assert limits.max_tokens == 150_000
    assert limits.max_external_calls == 25
    assert limits.max_wall_time_seconds == 120.0
    assert limits.max_calls_per_source == 8
