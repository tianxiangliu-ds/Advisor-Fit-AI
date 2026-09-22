"""扩充 Claim 金标集（一次性生成脚本，保留以便复核与再生成）。

为什么金标集要从 3 条扩到几十条：Claim 校验是**防幻觉的最后一道闸门**，
三条用例只覆盖了三条规则各一次，任何一条规则的边界（哪种状态算"肯定陈述"、
哪类声明才要求作者已确认）都没有被钉住。

**标签怎么来的**：不是照抄实现跑出来的结果，而是按 `validation/claims.py` 顶部
写明的三条规则手工推导的期望值。生成后会立刻用评测跑一遍——如果哪条对不上，
要么是我对规则的理解有误，要么是实现有 bug，两种情况都该暴露出来。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 一个"归属已确认"的常规证据
EV_CONFIRMED = {
    "id": "ev_W1",
    "source_type": "openalex",
    "title": "Retrieval-Augmented Generation: A Survey",
    "author_resolution_status": "CONFIRMED",
}
# 同一篇论文，但作者归属**没有**被确认
EV_UNCONFIRMED = {
    "id": "ev_W2",
    "source_type": "openalex",
    "title": "Graph Neural Networks for Recommendation",
    "author_resolution_status": "UNCONFIRMED",
}

# (说明, claim, evidences, 期望是否通过)
CASES: list[tuple[str, dict, list[dict], bool]] = [
    # -- A. 引用必须指向存在的证据 ------------------------------------------
    ("引用存在的证据", {
        "id": "c_a1", "text": "该团队近年关注检索增强生成",
        "claim_type": "observed_research_trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], True),
    ("引用不存在的证据", {
        "id": "c_a2", "text": "该团队近年关注检索增强生成",
        "claim_type": "observed_research_trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_not_exist"]}, [EV_CONFIRMED], False),
    ("两条证据里有一条不存在", {
        "id": "c_a3", "text": "该团队近年关注检索增强生成",
        "claim_type": "observed_research_trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_W1", "ev_not_exist"]}, [EV_CONFIRMED], False),
    ("一条证据都不引用（规则未禁止，应通过）", {
        "id": "c_a4", "text": "这里是一句不涉及事实的一般性表述",
        "claim_type": "generic", "status": "UNKNOWN",
        "evidence_ids": []}, [EV_CONFIRMED], True),
    ("引用空字符串 ID", {
        "id": "c_a5", "text": "该团队近年关注检索增强生成",
        "claim_type": "observed_research_trend", "status": "SUPPORTED",
        "evidence_ids": [""]}, [EV_CONFIRMED], False),

    # -- B. 招生 / 团队类只能为 UNKNOWN -------------------------------------
    ("招生状态为 UNKNOWN（唯一允许的写法）", {
        "id": "c_b1", "text": "导师招生状态未找到公开声明",
        "claim_type": "recruiting", "status": "UNKNOWN",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], True),
    ("招生状态写成 SUPPORTED（明令禁止的肯定陈述）", {
        "id": "c_b2", "text": "导师今年招收 3 名硕士",
        "claim_type": "recruiting", "status": "SUPPORTED",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], False),
    ("招生状态写成 PARTIAL 也算肯定陈述", {
        "id": "c_b3", "text": "导师可能有名额",
        "claim_type": "recruiting", "status": "PARTIAL",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], False),
    ("团队规模写成 SUPPORTED", {
        "id": "c_b4", "text": "团队现有 6 名博士生",
        "claim_type": "team_size", "status": "SUPPORTED",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], False),
    ("团队规模写成 UNKNOWN（允许）", {
        "id": "c_b5", "text": "团队规模未核实",
        "claim_type": "team_size", "status": "UNKNOWN",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], True),
    ("团队写成 CONFLICTED 也不行", {
        "id": "c_b6", "text": "两处资料对团队人数说法不一致",
        "claim_type": "team", "status": "CONFLICTED",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], False),
    ("非招生类的肯定陈述不受这条限制", {
        "id": "c_b7", "text": "研究契合度为强",
        "claim_type": "research_fit", "status": "SUPPORTED",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], True),

    # -- C. 趋势类声明要求作者归属已确认 ------------------------------------
    ("趋势 + SUPPORTED + 作者已确认", {
        "id": "c_c1", "text": "该团队近年转向检索增强生成",
        "claim_type": "observed_research_trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_W1"]}, [EV_CONFIRMED], True),
    ("趋势 + SUPPORTED + 作者未确认", {
        "id": "c_c2", "text": "该团队近年转向图神经网络",
        "claim_type": "observed_research_trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_W2"]}, [EV_CONFIRMED, EV_UNCONFIRMED], False),
    ("趋势（trend 别名）+ SUPPORTED + 作者未确认", {
        "id": "c_c3", "text": "该团队近年转向图神经网络",
        "claim_type": "trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_W2"]}, [EV_CONFIRMED, EV_UNCONFIRMED], False),
    ("趋势（research_trend 别名）+ SUPPORTED + 作者未确认", {
        "id": "c_c4", "text": "该团队近年转向图神经网络",
        "claim_type": "research_trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_W2"]}, [EV_CONFIRMED, EV_UNCONFIRMED], False),
    ("趋势 + UNKNOWN 状态 + 作者未确认（只查 SUPPORTED，应通过）", {
        "id": "c_c5", "text": "该团队的研究方向待核实",
        "claim_type": "observed_research_trend", "status": "UNKNOWN",
        "evidence_ids": ["ev_W2"]}, [EV_CONFIRMED, EV_UNCONFIRMED], True),
    ("趋势 + PARTIAL + 作者未确认（只查 SUPPORTED，应通过）", {
        "id": "c_c6", "text": "该团队可能转向图神经网络",
        "claim_type": "observed_research_trend", "status": "PARTIAL",
        "evidence_ids": ["ev_W2"]}, [EV_CONFIRMED, EV_UNCONFIRMED], True),
    ("非趋势类型 + SUPPORTED + 作者未确认（不受这条限制）", {
        "id": "c_c7", "text": "该论文提出了一个新的检索模型",
        "claim_type": "publication", "status": "SUPPORTED",
        "evidence_ids": ["ev_W2"]}, [EV_CONFIRMED, EV_UNCONFIRMED], True),
    ("趋势引用两条证据，其中一条未确认", {
        "id": "c_c8", "text": "该团队近年同时关注两个方向",
        "claim_type": "observed_research_trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_W1", "ev_W2"]}, [EV_CONFIRMED, EV_UNCONFIRMED], False),

    # -- D. 组合与边界 ------------------------------------------------------
    ("招生类违规与引用缺失同时存在", {
        "id": "c_d1", "text": "导师今年招收 3 名硕士",
        "claim_type": "recruiting", "status": "SUPPORTED",
        "evidence_ids": ["ev_not_exist"]}, [EV_CONFIRMED], False),
    ("趋势作者未确认与引用缺失同时存在", {
        "id": "c_d2", "text": "该团队近年转向图神经网络",
        "claim_type": "trend", "status": "SUPPORTED",
        "evidence_ids": ["ev_W2", "ev_not_exist"]},
     [EV_CONFIRMED, EV_UNCONFIRMED], False),
    ("全空的声明（无引用、类型未知、状态未知）", {
        "id": "c_d3", "text": "",
        "claim_type": "unknown", "status": "UNKNOWN",
        "evidence_ids": []}, [EV_CONFIRMED], True),
    ("招生的 UNKNOWN + 引用存在的证据（应通过）", {
        "id": "c_d4", "text": "招生状态未找到公开声明，建议邮件中询问",
        "claim_type": "recruiting", "status": "UNKNOWN",
        "evidence_ids": ["ev_W1", "ev_W2"]}, [EV_CONFIRMED, EV_UNCONFIRMED], True),
    ("证据字典为空、声明也不引用证据（应通过）", {
        "id": "c_d5", "text": "不涉及事实的一般性表述",
        "claim_type": "generic", "status": "UNKNOWN",
        "evidence_ids": []}, [], True),
]


def main() -> int:
    lines = []
    for note, claim, evidences, expected in CASES:
        lines.append(
            json.dumps(
                {"note": note, "claim": claim, "evidences": evidences,
                 "expected_valid": expected},
                ensure_ascii=False,
            )
        )
    out = ROOT / "evals" / "gold_claims.jsonl"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok = sum(1 for *_, expected in CASES if expected)
    print(f"已写入 {len(CASES)} 条（期望通过 {ok}、期望拦下 {len(CASES) - ok}）-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
