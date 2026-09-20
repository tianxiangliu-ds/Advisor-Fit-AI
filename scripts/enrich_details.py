"""补采导师字段：只进个人详情页，把职称/邮箱/研究方向补齐。

为什么需要单独一个脚本：列表页只有姓名，职称/邮箱/研究方向都在每个人的详情页里。
重跑整站爬取很慢，而详情页补采只需要为每个人多访问一次。

用法：
    # 先小范围试跑，看看效果
    .\\.venv\\Scripts\\python.exe scripts\\enrich_details.py --university 武汉大学 --limit 20

    # 全量补采
    .\\.venv\\Scripts\\python.exe scripts\\enrich_details.py --university 武汉大学

结果：更新 data/official_crawl/<学校>.json，并写回 data/advisors.db。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from crawl_university import _fetch_with_fallback, extract_detail_fields  # noqa: E402

from advisor_fit.config import settings  # noqa: E402
from advisor_fit.ingest.faculty import build_client  # noqa: E402
from advisor_fit.models.advisor import Advisor  # noqa: E402
from advisor_fit.storage.advisor_repo import AdvisorRepository  # noqa: E402


def enrich_entry(entry: dict, *, client, delay: float) -> bool:
    """为一条记录补采详情字段；返回是否补到了新东西。"""
    url = entry.get("homepage_url") or ""
    missing = not (entry.get("title") and entry.get("email") and entry.get("directions"))
    if not url.startswith("http") or not missing:
        return False
    try:
        time.sleep(delay)
        html = _fetch_with_fallback(url, client=client)
    except Exception:  # noqa: BLE001 - 单个人失败不影响整体
        return False
    detail = extract_detail_fields(html, entry.get("name", ""))
    got = False
    for key, value in detail.items():
        if value and not entry.get(key):
            entry[key] = value
            got = True
    return got


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--university", default="武汉大学")
    parser.add_argument("--limit", type=int, default=0, help="每人限制处理多少条（0=全部）")
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--max-per-college", type=int, default=0, help="每个学院最多补多少人")
    args = parser.parse_args()

    path = ROOT / "data" / "official_crawl" / f"{args.university}.json"
    if not path.exists():
        print(f"找不到 {path}，请先跑 crawl_university.py")
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))

    todo = [(c, e) for c in data for e in c.get("entries", [])]
    if args.limit:
        todo = todo[: args.limit]

    client = build_client()
    filled = attempts = 0
    try:
        for index, (_college, entry) in enumerate(todo, 1):
            attempts += 1
            if enrich_entry(entry, client=client, delay=args.delay):
                filled += 1
            if index % 50 == 0:
                print(f"  已处理 {index}/{len(todo)}，补到字段 {filled} 人", flush=True)
    finally:
        client.close()

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写回数据库
    advisors = []
    for college in data:
        for entry in college.get("entries", []):
            advisors.append(
                Advisor(
                    university=args.university,
                    department=college["college"],
                    name=entry["name"],
                    title=entry.get("title", ""),
                    email=entry.get("email", ""),
                    research_directions=(
                        [entry["directions"]] if entry.get("directions") else []
                    ),
                    homepage_url=entry.get("homepage_url", ""),
                    sources=["official"],
                    source_url=college.get("list_url", ""),
                )
            )
    if advisors:
        repo = AdvisorRepository(Path(settings.data_dir) / "advisors.db")
        repo.upsert_many(advisors, source="official")

    with_title = sum(1 for _, e in todo if e.get("title"))
    with_email = sum(1 for _, e in todo if e.get("email"))
    with_dir = sum(1 for _, e in todo if e.get("directions"))
    print()
    print(f"处理 {attempts} 位，其中 {filled} 位补到了新字段")
    print(f"  现在有职称：{with_title}  有邮箱：{with_email}  有研究方向：{with_dir}")
    print(f"已写回 {path} 与 data/advisors.db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
