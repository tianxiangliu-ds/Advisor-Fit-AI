"""名册体检：看清这份社区数据到底覆盖了什么、能和项目字段对上多少。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\roster_report.py
    .\\.venv\\Scripts\\python.exe scripts\\roster_report.py --university 武汉大学
    .\\.venv\\Scripts\\python.exe scripts\\roster_report.py --export data\\roster\\index.md

说明：名册只保留「学校 / 学院 / 导师姓名」三列。985/211 这类标签只在统计里用，
不写进名册表，避免把"分类标签"混成"导师事实"。
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
from advisor_fit.storage.roster_repo import RosterRepository  # noqa: E402

# 项目需要的 6 个导师字段，以及名册能提供哪些
PROJECT_FIELDS = {
    "name": "导师姓名",
    "institution": "学校/单位",
    "department": "院系",
    "title": "职称",
    "email": "邮箱",
    "declared_interests": "研究方向",
}
ROSTER_CAN_FILL = {
    "name": "supervisor（导师姓名）",
    "institution": "university（学校）",
    "department": "department（学院）",
}


def _load_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--university", default="武汉大学", help="展开哪个学校的院系目录")
    parser.add_argument("--top", type=int, default=20, help="列出前多少所学校")
    parser.add_argument("--export", default="", help="把完整目录导出成 Markdown 文件")
    parser.add_argument("--raw", default="data/roster/comments_data.json", help="原始 JSON 路径")
    args = parser.parse_args()

    repo = RosterRepository(settings.data_dir / "supervisor_roster.db")
    total = repo.count()
    if total == 0:
        print("本地名册为空。请先运行：scripts\\import_roster.py <comments_data.json>")
        return 2

    universities = repo.universities()
    all_departments = {(u, d) for u in universities for d in repo.departments(u)}

    section("一、总览")
    print(f"有效名册记录（去重后）：{total} 条")
    print(f"覆盖学校：{len(universities)} 所")
    print(f"覆盖学院：{len(all_departments)} 个（学校 × 学院）")

    per_school = {u: len(repo.lookup(u)) for u in universities}
    counts = sorted(per_school.values(), reverse=True)
    if counts:
        middle = counts[len(counts) // 2]
        print(f"每校导师数：最多 {counts[0]} 位，中位数 {middle} 位，最少 {counts[-1]} 位")

    section(f"二、导师最多的前 {args.top} 所学校")
    print(f"{'学校':<22}{'导师数':>6}{'学院数':>8}")
    for university in universities[: args.top]:
        print(f"{university:<22}{per_school[university]:>6}{len(repo.departments(university)):>8}")

    raw = _load_raw(ROOT / args.raw)
    if raw:
        categories = Counter(str(item.get("school_cate", "")).strip() or "(未标)" for item in raw)
        section("三、学校层次分布（来自原始数据，去重前）")
        for name, count in categories.most_common():
            print(f"  {name:<12}{count:>7} 条")
        labelled = {
            str(item.get("university", "")).strip()
            for item in raw
            if str(item.get("school_cate", "")).strip() in {"985", "211"}
        }
        print(f"\n  带 985/211 标签的学校：{len(labelled)} 所")

    section(f"四、院系目录展开：{args.university}")
    departments = repo.departments(args.university)
    if not departments:
        print(f"名册里没有「{args.university}」，下面是相近的学校名：")
        for university in universities:
            if args.university[:2] in university:
                print(f"  {university}")
    else:
        head = f"{args.university} 共 {len(departments)} 个学院"
        head += f" / {per_school.get(args.university, 0)} 位导师"
        print(head)
        print()
        for department in departments:
            names = repo.lookup(args.university, department)
            print(f"  ▸ {department}（{len(names)} 位）")
            print(f"      {'、'.join(names)}")

    section("五、名册字段 vs 项目所需字段")
    print(f"{'项目字段':<10}{'中文名':<12}{'名册能否提供':<24}")
    for field, label in PROJECT_FIELDS.items():
        source = ROSTER_CAN_FILL.get(field, "✗ 不能（需官网 / 其他网站）")
        print(f"{field:<10}{label:<12}{source:<24}")
    fillable = len(ROSTER_CAN_FILL)
    print(f"\n  可直接填充：{fillable}/{len(PROJECT_FIELDS)} 个字段")
    missing = "、".join(
        PROJECT_FIELDS[f] for f in PROJECT_FIELDS if f not in ROSTER_CAN_FILL
    )
    print(f"  仍需官网或其他来源：{missing}")

    if args.export:
        out = ROOT / args.export
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# 导师名册目录", "", f"共 {len(universities)} 所学校 / {total} 位导师", ""]
        for university in universities:
            lines.append(f"## {university}（{per_school[university]} 位）")
            lines.append("")
            for department in repo.departments(university):
                names = repo.lookup(university, department)
                lines.append(f"- **{department}**（{len(names)}）：{'、'.join(names)}")
            lines.append("")
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n完整目录已导出：{out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
