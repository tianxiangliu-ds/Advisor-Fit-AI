"""把社区开源导师数据导入本地名册（只取学校 / 学院 / 姓名）。

用法：
    # 先用内置样例验证链路
    .\\.venv\\Scripts\\python.exe scripts\\import_roster.py seeds\\roster_sample.json

    # 再导入真实数据（自行下载 RateMySupervisor 的 data/comments_data.json）
    .\\.venv\\Scripts\\python.exe scripts\\import_roster.py path\\to\\comments_data.json

说明：
- 只保留 university / department / supervisor 三列，rate 与 description 会被丢弃；
- 名册是「有哪些导师」的名单来源，不是事实来源：论文归属与研究方向仍以官网、学术库为准。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.config import settings  # noqa: E402
from advisor_fit.ingest.supervisor_roster import (  # noqa: E402
    parse_roster,
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

    entries = parse_roster(json.loads(path.read_text(encoding="utf-8")))
    if not entries:
        print(
            "没有解析出任何条目，请检查文件格式"
            "（至少需要 university 与 supervisor 两列）",
            file=sys.stderr,
        )
        return 1

    repo = RosterRepository(settings.data_dir / "supervisor_roster.db")
    repo.replace_all(entries)

    summary = roster_summary(entries)
    print(
        f"已导入 {summary['entries']} 条名册记录"
        f"（{summary['universities']} 所学校 / {summary['departments']} 个学院）"
    )
    for university, count in top_universities(entries, 5):
        print(f"  {university}：{count} 位")
    print(f"本地名册库共 {repo.count()} 条 → {repo.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
