"""把社区开源导师数据导入本地名册库。

用法：
    # 先用内置样例验证链路
    .\\.venv\\Scripts\\python.exe scripts\\import_roster.py seeds\\roster_sample.json

    # 再导入真实数据（自行下载 RateMySupervisor 的 data/comments_data.json）
    .\\.venv\\Scripts\\python.exe scripts\\import_roster.py data\\roster\\comments_data.json

存储口径：
- `roster` 表：导师，按 (学校, 学院, 姓名) 去重——不重不漏；括号备注进 `note` 列；
- `reviews` 表：学生评价，一位导师可以有多条，全部保留；
- 只清理社区来源的数据，不影响官网采集的结果。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.config import settings  # noqa: E402
from advisor_fit.ingest.supervisor_roster import (  # noqa: E402
    parse_roster_payload,
    roster_summary,
    top_universities,
)
from advisor_fit.storage.roster_repo import RosterRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="社区数据 JSON 文件路径")
    args = parser.parse_args()

    path = Path(args.source)
    if not path.exists():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2

    payload = parse_roster_payload(json.loads(path.read_text(encoding="utf-8")))
    if not payload.entries:
        print(
            "没有解析出任何条目，请检查文件格式（至少需要 university 与 supervisor 两列）",
            file=sys.stderr,
        )
        return 1

    repo = RosterRepository(settings.data_dir / "supervisor_roster.db")
    entries, reviews = repo.import_community(payload)

    summary = roster_summary(payload.entries)
    print(
        f"已导入 {entries} 位导师"
        f"（{summary['universities']} 所学校 / {summary['departments']} 个学院）"
        f"、{reviews} 条学生评价"
    )
    for university, count in top_universities(payload.entries, 5):
        print(f"  {university}：{count} 位")

    with_note = Counter(1 for item in payload.entries if item.note)
    rated = [item.rate for item in payload.reviews if item.rate is not None]
    print(f"  带括号备注的导师：{with_note[1]} 位")
    if rated:
        print(f"  有评分的评价：{len(rated)} 条，平均分 {sum(rated) / len(rated):.2f}")

    stats = repo.stats()
    print(
        f"本地名册库：{stats['advisors']} 位导师 / {stats['reviews']} 条评价"
        f" → {repo.db_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
