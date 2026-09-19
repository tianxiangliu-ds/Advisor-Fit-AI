"""批量采集高校导师信息到导师库。

用法：
    python scripts/crawl_faculty.py                # 静态抓取（数据源 data/faculty_seed.json）
    python scripts/crawl_faculty.py --js           # 用 Playwright 渲染 JS 页面
    python scripts/crawl_faculty.py --limit 20     # 每个学院最多采 20 位

结果写入 data/faculty.db（FacultyRepository），跨会话累积。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.config import settings  # noqa: E402
from advisor_fit.ingest.faculty import (  # noqa: E402
    crawl_college,
    fetch_html_rendered,
)
from advisor_fit.llm.provider import build_llm  # noqa: E402
from advisor_fit.storage.faculty_repo import FacultyRepository  # noqa: E402


def _load_seed() -> dict:
    return json.loads((ROOT / "seeds" / "faculty_seed.json").read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--js", action="store_true", help="用 Playwright 渲染 JS 页面")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--delay", type=float, default=0.4, help="每次请求间隔秒数（礼貌限速）")
    args = parser.parse_args()

    llm = build_llm()
    repo = FacultyRepository(settings.data_dir / "faculty.db")
    seed = _load_seed()
    colleges = seed.get("colleges", [])

    if args.js:
        # 用 Playwright 抓列表页，再静态抓个人页（个人页多为静态）
        from advisor_fit.ingest.faculty import (
            extract_faculty_list,
            fetch_html,
            parse_faculty_page,
        )
        from advisor_fit.ingest.homepage import html_to_text

        for college in colleges:
            print(f"[js] 爬取 {college['university']}·{college['college']} ...")
            try:
                list_html = fetch_html_rendered(college["list_url"])
                faculty = extract_faculty_list(
                    list_html, college["list_url"], llm,
                    university=college["university"], college=college["college"],
                )
                saved = 0
                for link in faculty[: args.limit]:
                    try:
                        time.sleep(args.delay)
                        page_html = fetch_html(link.href)
                        record = parse_faculty_page(
                            html_to_text(page_html), llm, name=link.name,
                            university=college["university"], college=college["college"],
                            homepage_url=link.href,
                        )
                        repo.upsert(record)
                        saved += 1
                    except Exception as exc:  # noqa: BLE001
                        print(f"   跳过 {link.name}: {type(exc).__name__}")
                print(f"  采得 {saved}/{len(faculty[: args.limit])} 位", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  {college['college']} 失败: {type(exc).__name__}: {exc}")
    else:
        for college in colleges:
            print(f"爬取 {college['university']}·{college['college']} ...")
            try:
                records = crawl_college(
                    llm, college["list_url"],
                    university=college["university"], college=college["college"],
                    limit=args.limit,
                )
                repo.upsert_many(records)
                print(f"  采得 {len(records)} 位")
            except Exception as exc:  # noqa: BLE001
                print(f"  {college['college']} 失败: {type(exc).__name__}: {exc}")

    print(f"\n导师库共 {repo.count()} 条记录 → data/faculty.db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
