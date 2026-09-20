"""按学校爬取官网：院系目录 → 各院系师资页 → 导师名单。

用法：
    # 单所学校（先跑这个验证效果）
    .\\.venv\\Scripts\\python.exe scripts\\crawl_university.py --university 武汉大学

    # 全部内置的 top20（耗时较长，建议放后台跑）
    .\\.venv\\Scripts\\python.exe scripts\\crawl_university.py --all

    # 只发现院系、不抓导师（快速看结构）
    .\\.venv\\Scripts\\python.exe scripts\\crawl_university.py --discover-only

结果：
- 写入 data/supervisor_roster.db 的 roster 表（标记 in_official=1），与社区名册并存不覆盖；
- 同时把明细写到 data/official_crawl/<学校>.json，方便人工核对。

注意：高校站点结构差异很大，部分师资页是 JS 动态渲染，静态抓取拿不到正文。
本脚本尽力而为，并会把每个学院的采集结果如实报告出来（含失败原因）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.config import settings  # noqa: E402
from advisor_fit.ingest.cv import looks_like_chinese_name  # noqa: E402
from advisor_fit.ingest.faculty import extract_links, fetch_html  # noqa: E402
from advisor_fit.ingest.homepage import html_to_text  # noqa: E402
from advisor_fit.ingest.supervisor_roster import RosterEntry  # noqa: E402
from advisor_fit.storage.roster_repo import RosterRepository  # noqa: E402

# 内置的 top20（按社区名册导师数排序）
UNIVERSITIES: dict[str, str] = {
    "浙江大学": "https://www.zju.edu.cn/",
    "中国科学院大学": "https://www.ucas.ac.cn/",
    "东南大学": "https://www.seu.edu.cn/",
    "天津大学": "https://www.tju.edu.cn/",
    "华中科技大学": "https://www.hust.edu.cn/",
    "西安交通大学": "https://www.xjtu.edu.cn/",
    "北京航空航天大学": "https://www.buaa.edu.cn/",
    "上海交通大学": "https://www.sjtu.edu.cn/",
    "西安电子科技大学": "https://www.xidian.edu.cn/",
    "清华大学": "https://www.tsinghua.edu.cn/",
    "武汉大学": "https://www.whu.edu.cn/",
    "中国科学技术大学": "https://www.ustc.edu.cn/",
    "北京邮电大学": "https://www.bupt.edu.cn/",
    "大连理工大学": "https://www.dlut.edu.cn/",
    "北京大学": "https://www.pku.edu.cn/",
    "四川大学": "https://www.scu.edu.cn/",
    "中南大学": "https://www.csu.edu.cn/",
    "同济大学": "https://www.tongji.edu.cn/",
    "电子科技大学": "https://www.uestc.edu.cn/",
    "重庆大学": "https://www.cqu.edu.cn/",
}

# 「院系设置」入口页的常见叫法
COLLEGE_DIR_KEYS = (
    "院系设置", "学院设置", "院系概况", "学部院系", "院系机构", "院系介绍",
    "学院部门", "组织机构", "院系导航", "教学单位", "院系",
)
# 学院首页里「师资」入口的常见叫法
FACULTY_KEYS = (
    "师资队伍", "师资力量", "师资概况", "师资介绍", "教师队伍", "教师名录",
    "专任教师", "专职教师", "导师队伍", "导师介绍", "全体教师", "教师介绍", "师资",
)
# 明显不是人名的词，避免把导航项当成导师。
# 注意：像「党群工作」「国际交流」「武大主页」这种，首字（党/国/武）本身就是姓氏，
# 光靠"首字是姓氏"滤不掉，必须显式列出来。
NAME_STOPWORDS = {
    "学院", "大学", "学校", "教授", "教师", "师资", "首页", "更多", "详情", "查看",
    "新闻", "通知", "公告", "招生", "就业", "党建", "工会", "校友", "人才", "科研",
    "教学", "学生", "研究生", "本科生", "实验", "中心", "办公室", "委员会", "研究所",
    "实验室", "系所", "概况", "简介", "联系", "地图", "导航", "登录", "搜索", "下载",
    "上一篇", "下一篇", "返回", "列表", "全部", "展开", "收起", "关于", "服务",
    # 实测抓到的站点导航项
    "学院简介", "学院概况", "师资队伍", "师资力量", "人才培养", "现任领导", "党群工作",
    "规章制度", "科学研究", "信息公开", "国际交流", "合作交流", "机构设置", "荣休教师",
    "工会工作", "学生工作", "历史沿革", "武大主页", "旧版", "字母检索", "系部检索",
    "下载专区", "联系我们", "详细", "尾页", "下页", "学术信息", "学术动态", "学术期刊",
    "学术机构", "教学工作", "教学机构", "教辅机构", "数字科研", "科研平台", "研究成果",
    "社会服务", "成果转化", "机构导览", "毕业合影", "院友之家", "院友动态", "院友名录",
    "院友心语", "图书分馆", "培训新闻", "培训通知", "专题课程", "国际合作", "合作项目",
    "办事流程", "各地分会", "学科百年", "实验中心", "行政分工",
}
# 同一个词出现在同一所学校的这么多学院里，就按站点导航丢弃（正常挂名不会有这么多）
NAV_CROSS_COLLEGE_THRESHOLD = 6


@dataclass
class CollegeResult:
    college: str
    list_url: str = ""
    names: list[str] = field(default_factory=list)
    note: str = ""


def _norm(url: str, base: str) -> str:
    return urljoin(base, url)


def discover_colleges(home: str, *, timeout: float = 20.0) -> list[tuple[str, str]]:
    """从学校首页找到「院系设置」页，再取出各学院（名称, 链接）。"""
    home_html = fetch_html(home, timeout=timeout)
    dir_url = ""
    for link in extract_links(home_html, home):
        if any(key in link["text"] for key in COLLEGE_DIR_KEYS):
            dir_url = link["href"]
            break
    if not dir_url:
        return []

    dir_html = fetch_html(dir_url, timeout=timeout)
    colleges: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in extract_links(dir_html, dir_url):
        text = re.sub(r"\s+", "", link["text"])
        if not (2 <= len(text) <= 14):
            continue
        if not any(key in text for key in ("学院", "学部", "学系", "研究院", "研究中心", "实验室")):
            continue
        if text in seen:
            continue
        seen.add(text)
        colleges.append((text, link["href"]))
    return colleges


def find_faculty_page(college_url: str, *, timeout: float = 20.0) -> str:
    """在学院主页里找「师资队伍」入口。"""
    try:
        html = fetch_html(college_url, timeout=timeout)
    except Exception:  # noqa: BLE001 - 单个学院失败不影响整体
        return ""
    links = extract_links(html, college_url)
    for key in FACULTY_KEYS:
        for link in links:
            if key in link["text"]:
                return link["href"]
    for link in links:
        path = urlparse(link["href"]).path.lower()
        if any(token in path for token in ("szdw", "shizi", "teacher", "faculty", "jsml")):
            return link["href"]
    return ""


def extract_names(html: str, base_url: str) -> list[str]:
    """从师资页里提取导师姓名。

    判据：锚文本是 2–4 个汉字、**首字是常见中文姓氏**、且不在导航词表里。
    只靠"2-4 个汉字"会把「学院简介」「师资队伍」「下页」当成名字，
    加上姓氏判断后误判能压到很低。
    """
    names: list[str] = []
    seen: set[str] = set()
    for link in extract_links(html, base_url):
        text = re.sub(r"\s+", "", link["text"])
        if text in NAME_STOPWORDS:
            continue
        if not looks_like_chinese_name(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        names.append(text)
    return names


def crawl_college(college: str, url: str, *, delay: float, timeout: float = 20.0) -> CollegeResult:
    result = CollegeResult(college=college)
    try:
        list_url = find_faculty_page(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result.note = f"找师资页失败：{type(exc).__name__}"
        return result
    if not list_url:
        result.note = "未找到师资队伍入口"
        return result

    result.list_url = list_url
    try:
        time.sleep(delay)
        html = fetch_html(list_url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result.note = f"抓师资页失败：{type(exc).__name__}"
        return result

    names = extract_names(html, list_url)
    if len(names) < 3:
        # 再试一层：有些学校师资入口先到「系/所」列表，再进具体名单
        best: list[str] = []
        for link in extract_links(html, list_url)[:25]:
            if not any(key in link["text"] for key in ("系", "所", "中心", "教研室")):
                continue
            try:
                time.sleep(delay)
                sub_html = fetch_html(link["href"], timeout=timeout)
            except Exception:  # noqa: BLE001
                continue
            sub_names = extract_names(sub_html, link["href"])
            if len(sub_names) > len(best):
                best = sub_names
            if len(best) >= 10:
                break
        if len(best) > len(names):
            names = best
            result.note = "经二级页取得"

    result.names = names
    if not names:
        text_len = len(html_to_text(html))
        result.note = f"页面无姓名链接（正文 {text_len} 字，可能是 JS 动态渲染）"
    return result


def crawl_university(
    university: str,
    home: str,
    *,
    delay: float = 1.0,
    discover_only: bool = False,
    max_colleges: int = 0,
) -> list[CollegeResult]:
    print(f"\n{'=' * 70}\n【{university}】{home}\n{'=' * 70}", flush=True)
    colleges = discover_colleges(home)
    print(f"发现学院：{len(colleges)} 个", flush=True)
    if max_colleges:
        colleges = colleges[:max_colleges]
    if discover_only:
        for name, url in colleges:
            print(f"  · {name}  {url}", flush=True)
        return [CollegeResult(college=name, list_url=url) for name, url in colleges]

    results: list[CollegeResult] = []
    for index, (name, url) in enumerate(colleges, 1):
        time.sleep(delay)
        outcome = crawl_college(name, url, delay=delay)
        results.append(outcome)
        if outcome.names:
            print(f"  [{index}/{len(colleges)}] {name}：{len(outcome.names)} 位", flush=True)
        else:
            print(f"  [{index}/{len(colleges)}] {name}：0 位（{outcome.note}）", flush=True)
    return results


def drop_site_navigation(results: list[CollegeResult]) -> tuple[list[CollegeResult], list[str]]:
    """丢弃"跨学院大面积出现"的词——那是站点导航，不是导师姓名。

    正常挂名最多出现在两三个学院；出现在 6 个以上几乎必然是导航项。
    """
    counter: Counter[str] = Counter()
    for item in results:
        for name in set(item.names):
            counter[name] += 1
    nav = {name for name, count in counter.items() if count >= NAV_CROSS_COLLEGE_THRESHOLD}
    if not nav:
        return results, []

    for item in results:
        item.names = [name for name in item.names if name not in nav]
        if not item.names and not item.note:
            item.note = "过滤后为空（原文只有站点导航项）"
    return results, sorted(nav)


def save(university: str, results: list[CollegeResult]) -> int:
    results, dropped = drop_site_navigation(results)
    if dropped:
        sample = "、".join(dropped[:5])
        print(f"  （已丢弃 {len(dropped)} 个站点导航词，例如：{sample}）", flush=True)

    entries: list[RosterEntry] = []
    payload = []
    for item in results:
        for name in item.names:
            entries.append(
                RosterEntry(
                    university=university,
                    department=item.college,
                    supervisor=name,
                )
            )
        payload.append(
            {
                "college": item.college,
                "list_url": item.list_url,
                "count": len(item.names),
                "names": item.names,
                "note": item.note,
            }
        )

    out_dir = ROOT / "data" / "official_crawl"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{university}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if entries:
        repo = RosterRepository(settings.data_dir / "supervisor_roster.db")
        repo.upsert_official(entries, source_url="官网院系师资页")
    return len(entries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--university", default="武汉大学")
    parser.add_argument("--home", default="")
    parser.add_argument("--all", action="store_true", help="跑内置的全部 top20")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0, help="每次请求间隔秒数")
    parser.add_argument("--max-colleges", type=int, default=0)
    args = parser.parse_args()

    targets = (
        list(UNIVERSITIES.items())
        if args.all
        else [(args.university, args.home or UNIVERSITIES.get(args.university, ""))]
    )

    total = 0
    for university, home in targets:
        if not home:
            print(f"没有 {university} 的官网地址，请用 --home 指定")
            continue
        try:
            results = crawl_university(
                university,
                home,
                delay=args.delay,
                discover_only=args.discover_only,
                max_colleges=args.max_colleges,
            )
        except Exception as exc:  # noqa: BLE001 - 单所学校失败不影响其它
            print(f"  {university} 整体失败：{type(exc).__name__}: {exc}", flush=True)
            continue
        if args.discover_only:
            continue
        saved = save(university, results)
        total += saved
        hit = sum(1 for item in results if item.names)
        print(f"  → {university}：{hit}/{len(results)} 个学院采到名单", flush=True)
        print(f"     共 {saved} 位导师", flush=True)

    if not args.discover_only:
        print(f"\n合计写入 {total} 位官网导师")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
