"""扩充消歧金标集：补上真实数据覆盖不到的**决策路径**（一次性生成脚本）。

为什么用构造用例而不是继续抓真实论文：
真实的"同名消歧"金标要求知道**每篇论文真正的作者归属**——那需要领域知识，
编不出来；而 OpenAlex 的机构过滤在当前查询形式下不生效（实测带过滤 0 篇、
不带过滤 3 篇），拿首作者机构当标签也不可靠。

所以这里补的是**场景用例**：场景由规则语义定义，标签由场景推导，不涉及任何
真人的事实。它们不替代真实数据，而是补齐真实数据碰不到的路径。

规则路径的两段逻辑（`agents/research.py`）：

1. `_rule_disambiguate`：机构冲突 → 标 `needs_review`（可能是曾任职单位），**不排除**；
2. `_fingerprint_prefilter`：池中存在"稳定合作者"（出现 ≥2 次的非本人作者）时，
   机构冲突且与该合作者集合无交集的，才判为同名并排除。

于是决策矩阵是：

| 机构冲突 | 池中有稳定合作者 | 与本篇有交集 | 结果 |
|---|---|---|---|
| 否 | — | — | 本人 |
| 是 | 否 | — | 本人但需复核 |
| 是 | 是 | 有 | 本人但需复核 |
| 是 | 是 | 无 | 疑似同名（排除） |

文件里每条带 `note` 字段说明它考的是哪条路径，与真实数据用例区分开。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "disambiguation_golden.jsonl"


def paper(title: str, authors: list[str], institution: str, expected: str,
          year: int = 2024) -> dict:
    return {"title": title, "authors": authors, "institution": institution,
            "year": year, "expected": expected}


SCENARIOS: list[dict] = [
    {
        "name": "张伟", "institution": "北京大学",
        "note": "构造用例：同名同校，三篇都应是本人",
        "candidates": [
            paper("同校论文甲", ["张伟", "李四"], "北京大学", "match"),
            paper("同校论文乙", ["张伟", "王五"], "北京大学信息科学技术学院", "match"),
            paper("同校论文丙", ["张伟"], "Peking University", "match"),
        ],
    },
    {
        "name": "张伟", "institution": "北京大学",
        "note": "构造用例：同名不同校且池中无稳定合作者 → 保守标记需复核，不直接排除",
        "candidates": [
            paper("外校论文甲", ["张伟", "赵一"], "清华大学", "review"),
            paper("外校论文乙", ["张伟", "钱二"], "复旦大学", "review"),
            paper("外校论文丙", ["张伟", "孙三"], "南开大学", "review"),
        ],
    },
    {
        "name": "张伟", "institution": "北京大学",
        "note": "构造用例：有稳定合作者，且本篇与该合作者有交集 → 仍算本人但需复核",
        "candidates": [
            paper("团队论文甲", ["张伟", "李四"], "清华大学", "review"),
            paper("团队论文乙", ["张伟", "李四"], "清华大学", "review"),
            paper("团队论文丙", ["张伟", "李四"], "上海交通大学", "review"),
        ],
    },
    {
        "name": "张伟", "institution": "北京大学",
        "note": "构造用例：有稳定合作者，但本篇与之无交集 → 判为同名并排除",
        "candidates": [
            paper("团队论文甲", ["张伟", "李四"], "清华大学", "review"),
            paper("团队论文乙", ["张伟", "李四"], "清华大学", "review"),
            paper("无关论文丙", ["张伟", "周七"], "复旦大学", "exclude"),
        ],
    },
    {
        "name": "刘洋", "institution": "浙江大学",
        "note": "构造用例：候选机构缺失时无法判断，保守算作本人（不标记复核）",
        "candidates": [
            paper("机构缺失论文甲", ["刘洋", "吴一"], "", "match"),
            paper("机构缺失论文乙", ["刘洋"], "", "match"),
            paper("同校论文丙", ["刘洋", "郑二"], "浙江大学", "match"),
        ],
    },
    {
        "name": "陈静", "institution": "南京大学",
        "note": "构造用例：学校简称（如「南大」）目前不被识别，会被标为需复核——保守方向",
        "candidates": [
            paper("简称论文甲", ["陈静", "冯一"], "南大", "review"),
            paper("全称论文乙", ["陈静", "陈二"], "南京大学", "match"),
            paper("英文论文丙", ["陈静"], "Nanjing University", "match"),
        ],
    },
]


def main() -> int:
    existing = [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # 幂等：先把上一次生成的构造用例去掉，再重新追加
    real = [case for case in existing if not str(case.get("note", "")).startswith("构造用例")]

    merged = [*real, *SCENARIOS]
    GOLDEN.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in merged) + "\n",
        encoding="utf-8",
    )
    n_cand = sum(len(case["candidates"]) for case in merged)
    print(f"真实用例 {len(real)} 个 + 构造用例 {len(SCENARIOS)} 个 = {len(merged)} 个")
    print(f"候选论文合计 {n_cand} 篇 -> {GOLDEN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
