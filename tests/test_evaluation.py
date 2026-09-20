"""统一评测的测试。

这里盯三件事：
1. 指标能被跑出来，且**门线的位置是对的**（放在"该负责的指标"上）；
2. 基线能存能读，回归能被认出来——包括"该低的变高"也算回归；
3. 需要 Key 的评测在没 Key 时是**跳过并说明原因**，不是让整轮失败。
"""

from __future__ import annotations

from pathlib import Path

from advisor_fit.evaluation import (
    EvalReport,
    Metric,
    compare_to_baseline,
    eval_claims,
    eval_disambiguation,
    eval_matching,
    eval_prompts,
    load_baseline,
    run_all,
    save_baseline,
)
from advisor_fit.llm.provider import NullLLM

ROOT = Path(__file__).resolve().parents[1]


# -- 指标本身 ------------------------------------------------------------------


def test_run_all_produces_every_dimension():
    report = run_all(ROOT)
    keys = set(report.values())

    # 四个维度都要有代表指标
    assert any(k.startswith("disambig.") for k in keys), "缺实体消歧指标"
    assert any(k.startswith("claims.") for k in keys), "缺 Evidence 覆盖指标"
    assert any(k.startswith("match.") for k in keys), "缺匹配一致性指标"
    assert any(k.startswith("prompts.") for k in keys), "缺提示词可靠性指标"


def test_disambiguation_recall_is_gated_but_precision_is_not():
    """规则基线只保召回，不该拿 precision 卡它。

    规则路径不排除任何论文，只把机构对不上的标成"需人工复核"，所以 precision
    等于金标正例比例，是**下限**而不是成绩。门线压在这里会永远失败。
    """
    metrics = {m.key: m for m in eval_disambiguation(ROOT, llm=NullLLM())}

    assert metrics["disambig.recall"].gate is not None
    assert metrics["disambig.precision"].gate is None
    assert metrics["disambig.f1"].gate is None


def test_disambiguation_metrics_stay_in_range():
    for metric in eval_disambiguation(ROOT, llm=NullLLM()):
        if metric.unit == "%":
            assert 0.0 <= metric.value <= 1.0, f"{metric.key} 超出 0~1"


def test_claims_metrics_agree_with_golden_set():
    metrics = {m.key: m for m in eval_claims(ROOT)}

    assert metrics["claims.accuracy"].value == 1.0
    assert metrics["claims.accuracy"].gate == 1.0
    # 金标里确实有"不该通过"的用例，否则这个指标就是空转
    assert metrics["claims.unsupported_detected"].value >= 1


def test_matching_consistency_is_perfect_on_fixed_input():
    metrics = {m.key: m for m in eval_matching()}

    assert metrics["match.determinism"].value == 1.0
    assert metrics["match.monotonic"].value == 1.0


def test_matching_monotonic_uses_gaps_not_strengths():
    """单调性必须看缺口数，不能看"强项条数"。

    strengths 里混着「技能覆盖」「证据充分度」这类与研究方向无关的通用条目，
    两份输入都会拿到同样条数，拿它比较是恒真的假指标。
    """
    metric = {m.key: m for m in eval_matching()}["match.monotonic"]

    assert "缺口" in metric.detail


def test_prompts_are_registered_and_usable():
    metrics = {m.key: m for m in eval_prompts()}

    assert metrics["prompts.usable"].value == 1.0
    assert metrics["prompts.registered"].value >= metrics["prompts.registered"].gate


# -- 跳过而不是失败 ------------------------------------------------------------


def test_llm_metrics_are_skipped_with_a_reason_when_no_key():
    report = run_all(ROOT, llm=NullLLM(), include_llm=True)

    skipped = {item["key"]: item["reason"] for item in report.skipped}
    assert "llm.disambig" in skipped
    assert "LLM_API_KEY" in skipped["llm.disambig"]
    assert not any(m.key.startswith("llm.") for m in report.metrics)
    # 关键：整轮评测不能因为缺 Key 就失败
    assert report.gates_passed is True


# -- 基线 ----------------------------------------------------------------------


def test_baseline_round_trip(tmp_path):
    path = tmp_path / "baseline.json"
    report = EvalReport(generated_at="t", metrics=[Metric("a", "甲", 0.5, "%")])

    save_baseline(report, path)
    loaded = load_baseline(path)

    assert loaded == {"a": 0.5}


def test_missing_or_broken_baseline_is_treated_as_empty(tmp_path):
    assert load_baseline(tmp_path / "nope.json") == {}

    broken = tmp_path / "broken.json"
    broken.write_text("{ 不是合法 json", encoding="utf-8")
    assert load_baseline(broken) == {}


def test_compare_flags_a_regression_in_a_higher_is_better_metric():
    report = EvalReport(metrics=[Metric("disambig.f1", "消歧 F1", 0.60, "%")])

    changes = compare_to_baseline(report, {"disambig.f1": 0.80})

    assert changes[0]["regressed"] is True
    assert changes[0]["delta"] < 0


def test_compare_flags_a_regression_in_a_lower_is_better_metric():
    """该低的指标变高也是回归——只看绝对值方向会漏掉这一类。"""
    report = EvalReport(
        metrics=[Metric("cost.usd", "单次成本", 0.5, "", higher_is_better=False)]
    )

    changes = compare_to_baseline(report, {"cost.usd": 0.3})

    assert changes[0]["regressed"] is True


def test_compare_treats_improvement_and_equality_as_not_regressed():
    improved = EvalReport(metrics=[Metric("disambig.f1", "消歧 F1", 0.90)])
    same = EvalReport(metrics=[Metric("disambig.f1", "消歧 F1", 0.80)])

    assert compare_to_baseline(improved, {"disambig.f1": 0.80})[0]["regressed"] is False
    assert compare_to_baseline(same, {"disambig.f1": 0.80})[0]["regressed"] is False


def test_compare_marks_metrics_that_are_new_since_the_baseline():
    report = EvalReport(metrics=[Metric("match.monotonic", "匹配单调性", 1.0)])

    changes = compare_to_baseline(report, {})

    assert changes[0]["new"] is True
    assert changes[0]["regressed"] is False


def test_report_gates_pass_on_the_current_repository():
    """当前仓库必须整体达标，否则说明有指标退步了。"""
    report = run_all(ROOT)

    failed = [m.label for m in report.metrics if not m.passed]
    assert not failed, f"以下指标没到及格线：{failed}"
    assert report.gates_passed is True
