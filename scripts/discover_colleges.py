"""发现高校「学院 → 师资入口」URL，写入 seeds/faculty_seed.json 的 colleges 数组。

用法：
    python scripts/discover_colleges.py            # 静态抓取
    python scripts/discover_colleges.py --js       # 用 Playwright 渲染 JS 页面

策略：主站 → 找「院系/学院设置」页 → 提取各学院主页 → 各学院首页找「师资/教师」入口。
结果可能有误，需人工抽查后用于全量采集。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.ingest.faculty import extract_links, fetch_html, fetch_html_rendered  # noqa: E402

_COLLEGE_DIR_KEYS = ("院系设置", "学院设置", "学院部门", "院系概况", "院系", "学部院系", "院系机构")
_FACULTY_KEYS = ("师资队伍", "师资力量", "专任教师", "专职教师", "教师名录", "教师队伍", "师资概况")


def _find_college_dir(html: str, base: str) -> str | None:
    for link in extract_links(html, base):
        if any(k in link["text"] for k in _COLLEGE_DIR_KEYS):
            return link["href"]
    return None


def _find_faculty(html: str, base: str) -> str | None:
    for link in extract_links(html, base):
        if any(k in link["text"] for k in _FACULTY_KEYS):
            return link["href"]
    return None


def discover(university: str, home: str, use_js: bool) -> list[dict]:
    import httpx

    if use_js:
        html_fn = fetch_html_rendered
    else:
        client = httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        html_fn = lambda url: fetch_html(url, client=client)  # noqa: E731

    home_html = html_fn(home)
    dir_url = _find_college_dir(home_html, home)
    if not dir_url:
        return []

    dir_html = html_fn(dir_url)
    college_links = [
        link for link in extract_links(dir_html, dir_url)
        if ("学院" in link["text"] or "学部" in link["text"] or "研究院" in link["text"])
        and len(link["text"]) <= 12
    ]
    results: list[dict] = []
    seen: set[str] = set()
    for cl in college_links:
        name = cl["text"]
        if name in seen:
            continue
        seen.add(name)
        try:
            ch = html_fn(cl["href"])
        except Exception:  # noqa: BLE001
            continue
        fac = _find_faculty(ch, cl["href"])
        if fac:
            results.append({"university": university, "college": name, "list_url": fac})
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--js", action="store_true")
    args = parser.parse_args()

    seed_path = ROOT / "seeds" / "faculty_seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    universities = [(u["name"], f"https://www.{u['domain']}/") for u in seed["universities"]]

    all_colleges: list[dict] = []
    for university, home in universities:
        print(f"[{university}] 发现学院…")
        try:
            cols = discover(university, home, args.js)
            print(f"  找到 {len(cols)} 个学院师资入口")
            all_colleges.extend(cols)
        except Exception as exc:  # noqa: BLE001
            print(f"  失败: {type(exc).__name__}: {exc}")

    print(f"\n共发现 {len(all_colleges)} 个学院入口")
    out = seed_path.parent / "faculty_seed.discovered.json"
    out.write_text(
        json.dumps({"colleges": all_colleges}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已写入 {out}（人工核对后合并进 faculty_seed.json）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
