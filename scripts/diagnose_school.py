"""逐学院体检：把一所学校每个学院"卡在哪一步"打印出来，用于定位爬取失败原因。

用法：
    python scripts/diagnose_school.py --university 浙江大学
    python scripts/diagnose_school.py --university 浙江大学 --limit 8 --details

输出每位学院一行：学院名 | 解析到的学院网站 | 师资入口 | 抓到人数 | 备注
只要有一列是空的，就知道该修哪一环。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 控制台默认可能是 GBK，中文日志会乱码或直接报 UnicodeEncodeError；
# 统一改成 UTF-8 输出，日志文件才能直接读。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crawl_university as cu  # noqa: E402

from advisor_fit.ingest.faculty import build_client  # noqa: E402
from advisor_fit.ingest.homepage import html_to_text  # noqa: E402


def diagnose(college: str, url: str, home: str, *, client, delay: float) -> dict:
    """跑一个学院的完整流程，记录每一环的结果。"""
    info: dict = {"college": college, "entry": url, "site": "", "faculty": "",
                  "count": 0, "note": ""}

    # 第一环：学院页是不是学校主站的简介页？是的话要找到学院自己的网站
    try:
        site, html = cu.resolve_college_site(college, url, home, client=client)
        info["site"] = site
    except Exception as exc:  # noqa: BLE001
        info["note"] = f"取学院页失败：{type(exc).__name__}"
        return info

    # 第二环：找师资队伍入口
    try:
        faculty = cu.find_faculty_page(
            url, client=client, college=college, home=home
        )
        info["faculty"] = faculty
    except Exception as exc:  # noqa: BLE001
        info["note"] = f"找师资入口失败：{type(exc).__name__}"
        return info
    if not faculty:
        info["note"] = "未找到师资队伍入口"
        return info

    # 第三环：抓名单（含 JS 兜底）
    try:
        time.sleep(delay)
        page = cu._fetch_with_fallback(faculty, client=client)
        entries = cu.extract_entries(page, faculty)
        rendered_used = False
        if len(entries) < 3:
            try:
                rendered = cu._fetch(faculty, client=client, js=True)
                rendered_entries = cu.extract_entries(rendered, faculty)
                if len(rendered_entries) > len(entries):
                    entries = rendered_entries
                    rendered_used = True
            except Exception:  # noqa: BLE001 - 渲染失败就用静态结果
                pass
        info["count"] = len(entries)
        info["rendered"] = rendered_used
        info["sample"] = [item["name"] for item in entries[:6]]
        if not entries:
            text = html_to_text(page) or ""
            info["note"] = f"页面无姓名链接（正文 {len(text)} 字）"
    except Exception as exc:  # noqa: BLE001
        info["note"] = f"抓师资页失败：{type(exc).__name__}"
    return info


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--university", required=True)
    parser.add_argument("--limit", type=int, default=10, help="只体检前 N 个学院")
    parser.add_argument("--delay", type=float, default=0.3)
    args = parser.parse_args()

    home = cu.UNIVERSITIES.get(args.university, "")
    if not home:
        print(f"内置清单里没有 {args.university}")
        return 1

    client = build_client()
    cu._FETCHER = cu.Fetcher(
        client=client, user_agent=client.headers["User-Agent"], min_interval_seconds=0.8
    )

    colleges = cu.discover_colleges(home, client=client)
    print(f"【{args.university}】发现 {len(colleges)} 个学院，体检前 "
          f"{min(args.limit, len(colleges))} 个\n", flush=True)

    rows = []
    for index, (name, url) in enumerate(colleges[: args.limit], 1):
        info = diagnose(name, url, home, client=client, delay=args.delay)
        rows.append(info)
        site = info["site"].replace("https://", "").replace("http://", "")[:38]
        faculty = info["faculty"].replace("https://", "").replace("http://", "")[:44]
        flag = "渲染" if info.get("rendered") else "    "
        print(f"[{index:>2}] {name[:20]:<22} {info['count']:>4} 人 {flag}", flush=True)
        print(f"     学院网站 {site or '（未解析到，仍在主站）'}", flush=True)
        print(f"     师资入口 {faculty or '（未找到）'}", flush=True)
        if info["note"]:
            print(f"     ⚠ {info['note']}", flush=True)
        if info.get("sample"):
            print(f"     样本 {info['sample']}", flush=True)

    client.close()
    ok = sum(1 for r in rows if r["count"] > 0)
    zero = [r["college"] for r in rows if r["count"] == 0]
    print(f"\n体检结果：{ok}/{len(rows)} 个学院抓到人")
    if zero:
        print("0 人学院：" + "、".join(zero))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
