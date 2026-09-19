"""消歧评测：量化作者消歧的 precision / recall / F1，防回归。

数据：evals/disambiguation_golden.jsonl，每行一个导师 + 若干已标注候选论文。
expected 取值：match（属于该导师）/ exclude（同名他人）/ review（机构不同但疑似调动，属该导师）。

用法：
    python scripts/run_disambiguation_evals.py          # 用真实 LLM
    python scripts/run_disambiguation_evals.py --rule   # 用机构规则兜底作基线对比

二分类口径：match 与 review 都视为「是本人」（正例），exclude 视为「非本人」（负例）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.agents.research import disambiguate_papers  # noqa: E402
from advisor_fit.llm.provider import NullLLM, build_llm  # noqa: E402


def _load_golden() -> list[dict]:
    path = ROOT / "evals" / "disambiguation_golden.jsonl"
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def _to_papers(candidates: list[dict]) -> list[dict]:
    return [
        {
            "title": c["title"],
            "authors": c.get("authors", []),
            "institution": c.get("institution", ""),
            "year": c.get("year"),
            "abstract": c.get("abstract", ""),
            "belongs": True,
            "needs_review": False,
            "disambig_reason": "",
        }
        for c in candidates
    ]


def _evaluate(llm) -> dict:
    tp = fp = fn = tn = 0
    per_case: list[dict] = []
    for case in _load_golden():
        papers = _to_papers(case["candidates"])
        papers = disambiguate_papers(
            llm, papers, professor_name=case["name"], institution=case.get("institution")
        )
        c_tp = c_fp = c_fn = 0
        for paper, cand in zip(papers, case["candidates"], strict=True):
            pred_pos = bool(paper.get("belongs", True))
            true_pos = cand["expected"] in ("match", "review")
            if pred_pos and true_pos:
                tp += 1
                c_tp += 1
            elif pred_pos and not true_pos:
                fp += 1
                c_fp += 1
            elif not pred_pos and true_pos:
                fn += 1
                c_fn += 1
            else:
                tn += 1
        precision = c_tp / (c_tp + c_fp) if (c_tp + c_fp) else 0.0
        recall = c_tp / (c_tp + c_fn) if (c_tp + c_fn) else 0.0
        per_case.append({"name": case["name"], "precision": precision, "recall": recall})

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "per_case": per_case,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rule", action="store_true", help="用机构规则兜底作基线")
    args = parser.parse_args()

    if args.rule:
        llm = NullLLM()
    else:
        llm = build_llm()
        if isinstance(llm, NullLLM):
            print(
                "未配置 LLM_API_KEY，无法运行 LLM 消歧评测（可用 --rule 跑规则基线）",
                file=sys.stderr,
            )
            return 2

    result = _evaluate(llm)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
