"""把两个导师库合并成最终的统一导师库 data/advisors.db。

合并策略：
1. 以 `supervisor_roster.db` 的 roster 表为底（26,929 位：社区名册 + 官网采集的姓名/学校/学院）；
2. 用 `faculty.db`（早期官网采集，字段最全但只覆盖 10 所学校）按 (学校, 姓名) 补全
   职称 / 邮箱 / 研究方向 / 研究领域 / 代表论文 / 个人主页 / 简介；
   —— 只有当同校同名在 faculty.db 里**唯一**时才补，避免张冠李戴；
3. faculty.db 里导师库中没有的人，作为新条目补进来；
4. 学生评价一并迁入统一库的 reviews 表。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\merge_advisors.py
    .\\.venv\\Scripts\\python.exe scripts\\merge_advisors.py --target data\\advisors.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.models.advisor import Advisor  # noqa: E402
from advisor_fit.storage.advisor_repo import AdvisorRepository  # noqa: E402

ROSTER_DB = ROOT / "data" / "supervisor_roster.db"
FACULTY_DB = ROOT / "data" / "faculty.db"
TARGET_DB = ROOT / "data" / "advisors.db"


def _json_list(value: str) -> list[str]:
    try:
        data = json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
    if isinstance(data, list):
        return [str(item).strip() for item in data if str(item).strip()]
    if isinstance(data, str) and data.strip():
        return [data.strip()]
    return []


def load_roster(path: Path) -> list[Advisor]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    advisors: list[Advisor] = []
    for row in conn.execute("SELECT * FROM roster"):
        sources = []
        if row["in_community"]:
            sources.append("community")
        if row["in_official"]:
            sources.append("official")
        advisors.append(
            Advisor(
                university=row["university"],
                department=row["department"],
                name=row["supervisor"],
                note=row["note"],
                school_cate=row["school_cate"],
                sources=sources,
                source_url=row["source_url"],
                retrieved_at=row["retrieved_at"],
            )
        )
    conn.close()
    return advisors


def load_reviews(path: Path) -> list[dict]:
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute("SELECT * FROM reviews")]
    conn.close()
    return rows


def load_faculty(path: Path) -> tuple[dict[tuple[str, str], list[Advisor]], list[Advisor]]:
    """返回：(学校,姓名) -> 候选列表（用于补全），以及全部记录（用于新增）。"""
    if not path.exists():
        return {}, []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    by_name: dict[tuple[str, str], list[Advisor]] = defaultdict(list)
    all_rows: list[Advisor] = []
    for row in conn.execute("SELECT * FROM faculty"):
        advisor = Advisor(
            university=row["university"],
            department=row["college"] or row["department"],
            name=row["name"],
            title=row["title"],
            email=row["email"],
            research_directions=_json_list(row["research_directions"]),
            research_areas=_json_list(row["research_areas"]),
            publications=_json_list(row["publications"]),
            homepage_url=row["homepage_url"],
            profile_text=row["profile_text"],
            sources=["faculty"],
            source_url=row["source_url"],
            retrieved_at=row["retrieved_at"],
        )
        all_rows.append(advisor)
        by_name[(advisor.university, advisor.name)].append(advisor)
    conn.close()
    return by_name, all_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roster", default=str(ROSTER_DB))
    parser.add_argument("--faculty", default=str(FACULTY_DB))
    parser.add_argument("--target", default=str(TARGET_DB))
    args = parser.parse_args()

    target = Path(args.target)
    if target.exists():
        target.unlink()

    roster = load_roster(Path(args.roster))
    by_name, faculty_rows = load_faculty(Path(args.faculty))
    repo = AdvisorRepository(target)

    # 1) 以导师库为底写入
    repo.upsert_many(roster, source="community")
    existing_keys = {item.key() for item in roster}

    # 2) 用 faculty.db 补全（同校同名唯一才补）
    enriched = 0
    ambiguous = 0
    for item in roster:
        candidates = by_name.get((item.university, item.name), [])
        if not candidates:
            continue
        if len(candidates) > 1:
            ambiguous += 1
            continue
        before = repo.get(item.university, item.department, item.name)
        after = repo.upsert(candidates[0], source="faculty")
        if before is None or after.field_sources != before.field_sources:
            enriched += 1

    # 3) faculty.db 里导师库没有的人，补进来
    added = 0
    for item in faculty_rows:
        if item.key() in existing_keys:
            continue
        repo.upsert(item, source="faculty")
        existing_keys.add(item.key())
        added += 1

    # 4) 迁移学生评价
    reviews = load_reviews(Path(args.roster))
    if reviews:
        repo.import_reviews(reviews, replace=True)

    # 5) 报告
    rates = repo.field_fill_rates()
    total = repo.count()
    print("=" * 76)
    print("统一导师库合并完成")
    print("=" * 76)
    print(f"输出文件：{target}  （{target.stat().st_size / 1024 / 1024:.1f} MB）")
    print(f"导师总数：{total} 位")
    print(f"学生评价：{repo.review_count()} 条")
    print(f"覆盖学校：{len(repo.universities())} 所")
    print()
    print(f"合并明细：以导师库 {len(roster)} 位为底；"
          f"faculty.db 补全 {enriched} 位（同校同名胜疑跳过 {ambiguous} 位）；"
          f"新增 {added} 位")
    print()
    print("各字段填充率：")
    labels = {
        "name": "姓名", "university": "学校", "department": "学院",
        "title": "职称", "email": "邮箱", "research_directions": "研究方向",
        "research_areas": "研究领域", "publications": "代表论文",
        "homepage_url": "个人主页", "profile_text": "简介正文",
        "orcid": "ORCID", "openalex_id": "OpenAlex 编号",
        "note": "姓名备注", "school_cate": "学校层次",
    }
    for field, label in labels.items():
        rate = rates.get(field, 0.0)
        print(f"  {label:<12}{rate * 100:5.1f}%  （{int(rate * total)} 位）")
    print()
    print("来源分布：", repo.source_breakdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
