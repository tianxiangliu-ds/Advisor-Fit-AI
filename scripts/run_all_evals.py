"""一键评测：跑出各维度指标、和基线对比、给出回归结论。

用法：

```powershell
# 跑一遍离线指标（不需要 Key、不联网）
.\\.venv\\Scripts\\python.exe scripts\\run_all_evals.py

# 把当前结果存成基线（之后每次跑都会和它比）
.\\.venv\\Scripts\\python.exe scripts\\run_all_evals.py --save-baseline

# 和基线对比，出现回归时退出码为 1（可直接挂进 CI）
.\\.venv\\Scripts\\python.exe scripts\\run_all_evals.py --compare

# 额外跑需要 LLM_API_KEY 的真实模型消歧评测
.\\.venv\\Scripts\\python.exe scripts\\run_all_evals.py --with-llm
```

为什么要有这个脚本：指标散在几个脚本里、输出格式各不相同，就没有人能一眼看出
"这一版比上一版是好了还是坏了"。这里统一成一张表 + 一份基线文件。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from advisor_fit.evaluation import (  # noqa: E402
    compare_to_baseline,
    load_baseline,
    run_all,
    save_baseline,
)
from advisor_fit.llm.provider import NullLLM, build_llm  # noqa: E402

BASELINE_PATH = ROOT / "evals" / "baseline.json"


def _print_report(report) -> None:
    print(f"\n{'指标':<32}{'数值':>10}  {'门线':>8}  说明")
    print("-" * 96)
    for metric in report.metrics:
        gate = "—" if metric.gate is None else (
            f"{metric.gate * 100:.0f}%" if metric.unit == "%" else f"{metric.gate:g}"
        )
        flag = "✓" if metric.passed else "✗"
        print(f"{flag} {metric.label:<30}{metric.display():>10}  {gate:>8}  {metric.detail}")

    for item in report.skipped:
        print(f"\n跳过：{item['key']} —— {item['reason']}")


def _print_comparison(changes) -> list[dict]:
    regressions = [c for c in changes if c["regressed"]]
    moved = [c for c in changes if c["delta"] is not None and abs(c["delta"]) > 1e-9]

    if not changes:
        print("\n没有可比对的基线。先跑一次 --save-baseline。")
        return regressions

    print(f"\n{'与基线对比':<32}{'之前':>10}{'现在':>10}{'变化':>10}")
    print("-" * 96)
    for change in changes:
        if change.get("new"):
            print(f"  {change['label']:<30}{'（新增）':>10}{change['after']:>10.3f}{'—':>10}")
            continue
        sign = "↑" if change["delta"] > 0 else ("↓" if change["delta"] < 0 else "=")
        mark = "  ⚠ 回归" if change["regressed"] else ""
        print(
            f"  {change['label']:<30}{change['before']:>10.3f}"
            f"{change['after']:>10.3f}   {sign} {change['delta']:+.3f}{mark}"
        )

    if not moved:
        print("\n所有指标与基线完全一致。")
    elif regressions:
        print(f"\n发现 {len(regressions)} 项回归。")
    else:
        print(f"\n{len(moved)} 项有变化，没有回归。")
    return regressions


def main() -> int:
    parser = argparse.ArgumentParser(description="一键评测各维度指标")
    parser.add_argument("--save-baseline", action="store_true", help="把结果存成基线")
    parser.add_argument("--compare", action="store_true", help="与基线对比，回归时退出码 1")
    parser.add_argument("--with-llm", action="store_true", help="额外跑真实模型消歧评测")
    args = parser.parse_args()

    llm = build_llm() if args.with_llm else NullLLM()
    report = run_all(ROOT, llm=llm, include_llm=args.with_llm)

    _print_report(report)

    if args.save_baseline:
        save_baseline(report, BASELINE_PATH)
        print(f"\n基线已保存到 {BASELINE_PATH.relative_to(ROOT)}")

    regressions: list[dict] = []
    if args.compare:
        regressions = _print_comparison(
            compare_to_baseline(report, load_baseline(BASELINE_PATH))
        )

    if not report.gates_passed:
        print("\n有指标没到及格线。")
        return 1
    if regressions:
        return 1
    print("\n全部指标通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
