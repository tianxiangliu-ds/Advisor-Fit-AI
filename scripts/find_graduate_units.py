"""为每所学校找到「招研究生的培养单位」权威名单，存成 data/graduate_units.json。

为什么单独做这件事：批量爬取时用来筛选的名单必须准确。多数学校的研究生院首页
并不列培养单位，真名单在「招生信息 → 硕士招生 → 招生简章 / 专业目录」里，
而且各校栏目名不统一。

识别三类页面（按可信度从高到低）：
- coded  ：带招生编号的链接，如「104信息管理学院(2026年)」——最精确；
- catalog：招生目录索引页——一堆同目录下、文字是学院名的链接，
           如重庆大学 /sszyml/2026/1.html、同济 /zsml/sszsml/index/2026；
- plain  ：正文里出现很多「XX学院」——最宽松，仅作线索，需人工复核。

做法：先探测研究生院子域名，再按「专业目录 > 招生简章 > 招生信息」的优先级
在站内广度优先搜索，取找到的最优名单写入结果文件。

结果文件**人工可编辑**：自动找不到的学校，直接在里面补 source_url 再重跑。
"""

from __future__ import annotations

import argparse
import heapq
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crawl_university as cu  # noqa: E402

from advisor_fit.ingest.faculty import (  # noqa: E402
    build_client,
    extract_links,
    fetch_html_rendered,
)
from advisor_fit.ingest.homepage import html_to_text  # noqa: E402

OUT_PATH = ROOT / "data" / "graduate_units.json"

# 研究生院/研究生招生网的常见子域名前缀
PROBE_PREFIXES = (
    "gs", "yjsy", "grs", "yz", "yzb", "yzbm", "graduate", "yjs",
    "gsao", "grad", "yjszs", "yzw", "zs", "zsb",
)

# 站内搜索时，链接文字里出现这些词的页面优先看（数字越小越优先）
LINK_PRIORITY = (
    ("招生专业目录", 0), ("专业目录", 0), ("培养单位", 0), ("招生单位", 0),
    ("硕士招生", 1), ("招生简章", 1), ("简章", 1), ("招生章程", 1),
    ("招生信息", 2), ("院系设置", 2), ("教学单位", 2), ("招生专业", 2),
    ("全日制", 3), ("学术学位", 3), ("专业学位", 3), ("目录", 3),
)

# 单位名：以学院/研究院/…结尾
_UNIT_NAME_RE = re.compile(
    r"[\u4e00-\u9fa5（）()·\-A-Za-z]{2,24}"
    r"(?:学院|研究院|研究所|学部|学系|中心|实验室|医院)"
)

# 正文里「编号 + 单位名」，如「010 建筑与城市规划学院」「101经济学院」。
# 很多学校的研究生招生系统是 JS 渲染的，名单只以文字形式出现，不是链接，
# 所以必须能从正文里认出来。
_TEXT_CODED_RE = re.compile(
    r"(?<!\d)(\d{2,3})\s*"
    r"([\u4e00-\u9fa5（）()·\-A-Za-z]{2,24}?"
    r"(?:学院|研究院|研究所|学部|学系|中心|实验室|医院|系|部|所))"
)

# 正文宽松抽取时，明显不是培养单位的词
_PLAIN_STOP = (
    "研究生院", "招生办", "就业", "图书馆", "档案馆", "校医院", "心理健康",
    "国际交流", "合作交流", "校友", "基金会", "编辑部", "出版社", "后勤",
    "信息中心", "网络中心", "教师发展中心", "实验教学中心", "分析测试中心",
    "研究中心", "服务中心", "管理中心", "会议中心", "活动中心",
)


# 名字后面挂着的界面文字，如「数学科学学院(请点击查看)」
_UI_SUFFIX_RE = re.compile(
    r"[（(]\s*(?:请)?(?:点击|单击)?(?:查看|进入|详情|更多|查询|链接|网站|主页|官网)"
    r"\s*[)）]\s*$"
)


def clean_unit_name(name: str) -> str:
    """去掉单位名后面挂的界面文字。"""
    cleaned = re.sub(r"\s+", "", name or "")
    for _ in range(3):  # 可能叠了两层，如「X学院(请点击查看)(硕士)」
        stripped = _UI_SUFFIX_RE.sub("", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
    return cleaned.rstrip(">＞·、,，。 ")


def coded_units_from_text(html: str) -> list[str]:
    """从页面正文里认「编号 + 单位名」，应对 JS 渲染、名单不是链接的招生目录。"""
    text = html_to_text(html) or ""
    units: list[str] = []
    seen: set[str] = set()
    for _code, name in _TEXT_CODED_RE.findall(text):
        name = clean_unit_name(name)
        if len(name) < 3 or len(name) > 25 or name in seen:
            continue
        seen.add(name)
        units.append(name)
    return units


def root_domain(url: str) -> str:
    """取可注册域名，用于判断两个地址是不是同一所学校。

    www.tongji.edu.cn / yzbm.tongji.edu.cn -> tongji.edu.cn
    yz.chsi.com.cn -> chsi.com.cn（研招网，不是同济自己的站）
    """
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-1] == "cn" and parts[-2] in ("edu", "com", "gov", "org", "net"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def template_urls(home: str, years: tuple[int, ...] = (2026, 2025)) -> list[str]:
    """按已知的通用招生目录模板，直接猜可能的地址。

    实测两套模板覆盖了不少学校：
    - /sszyml/<年>/index.html      （重庆大学、四川大学）
    - /zsml/sszsml/index/<年>       （同济大学，挂在 yzbm 子域）
    """
    root = root_domain(home)
    guesses: list[str] = []
    for year in years:
        for sub in ("yz", "yzb", "yzbm", "gs", "yjsy", "grs"):
            guesses.append(f"https://{sub}.{root}/sszyml/{year}/index.html")
            guesses.append(f"https://{sub}.{root}/zsml/sszsml/index/{year}")
        guesses.append(f"https://yz.{root}/sszyml/index")
    return guesses


def probe_sites(home: str, client, *, timeout: float = 12.0) -> list[str]:
    """探测该校还活着的研究生院子域名。"""
    host = re.sub(r"^https?://", "", home).split("/")[0]
    root = host.split(".", 1)[1] if host.count(".") >= 2 else host
    alive: list[str] = []
    for prefix in PROBE_PREFIXES:
        url = f"https://{prefix}.{root}/"
        try:
            resp = client.get(url, timeout=timeout, follow_redirects=True)
        except Exception:  # noqa: BLE001 - DNS 不存在就是没有这个站点
            continue
        if resp.status_code < 400:
            final = str(resp.url)
            if final not in alive:
                alive.append(final)
    return alive[:5]


def catalog_units(html: str, url: str) -> list[str]:
    """识别「招生目录索引页」：一堆同目录下、文字是学院名的链接。

    这是各校研究生招生系统用得最多的一种模板，例如重庆大学
    /sszyml/2026/1.html、2.html… 和同济 /zsml/sszsml/index/2026。
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for link in extract_links(html, url):
        text = clean_unit_name(link["text"])
        if not _UNIT_NAME_RE.fullmatch(text):
            continue
        href = link["href"].split("?")[0]
        prefix = href.rsplit("/", 1)[0] if "/" in href else href
        groups[prefix].append(text)

    if not groups:
        return []
    best = max(groups.values(), key=len)
    # 同一个目录下至少 12 个学院名，才算"目录索引页"而不是普通导航
    return best if len(best) >= 12 else []


def plain_units(html: str) -> list[str]:
    """从正文里宽松抽取单位名（最弱线索，需人工复核）。"""
    text = html_to_text(html) or ""
    units: list[str] = []
    seen: set[str] = set()
    for raw in _UNIT_NAME_RE.findall(text):
        name = clean_unit_name(raw)
        if any(stop in name for stop in _PLAIN_STOP):
            continue
        if name in seen:
            continue
        seen.add(name)
        units.append(name)
    return units


def score_page(html: str, url: str) -> tuple[list[str], list[str], list[str]]:
    """给一页打分：返回（编号型, 目录索引型, 宽松型）。"""
    coded = cu._unit_links(html, url)
    for name in coded_units_from_text(html):
        if name not in coded:
            coded.append(name)
    return coded, catalog_units(html, url), plain_units(html)


def link_priority(text: str) -> int:
    for key, priority in LINK_PRIORITY:
        if key in text:
            return priority
    return 99


# 识别方法可信度：编号型 > 目录索引型 > 宽松型
METHOD_RANK = {"coded": 3, "catalog": 2, "plain": 1}
# 各方法的可信门槛：编号型必须够 15 个，否则多半是首页导航拼出来的
METHOD_MIN = {"coded": 15, "catalog": 12, "plain": 12}


def _is_better(cand_method: str, cand_n: int, best_method: str, best_n: int) -> bool:
    """判断新找到的名单是否比现有的更好。

    规则（踩过两次坑才定下来）：
    1. 先看这个候选**够不够可信**：编号型要 ≥15 个（真正的招生目录都在 20 个以上，
       而首页导航里东拼西凑只能凑出十几个），目录索引型和宽松型要 ≥12 个。
    2. 一方可信、一方不可信 -> 可信的赢。
    3. 都可信 -> **比识别方法**（编号型 > 目录索引型 > 宽松型）。
       不能比数量：宽松型在任意新闻页上都能抓出几十个"XX学院"，数量多但全是噪声。
    4. 都不可信 -> 取更长的那份，聊胜于无。
    """
    cand_ok = cand_n >= METHOD_MIN.get(cand_method, 12)
    best_ok = best_n >= METHOD_MIN.get(best_method, 12)
    if cand_ok != best_ok:
        return cand_ok
    if cand_ok:
        return METHOD_RANK[cand_method] > METHOD_RANK.get(best_method, 0)
    return cand_n > best_n


def search_units(
    seeds: list[str], client, *, max_pages: int = 40, timeout: float = 25.0,
    max_depth: int = 3, allowed_domains: set[str] | None = None,
) -> tuple[str, list[str], str, list[str]]:
    """按优先级广度优先，找培养单位名单最全的一页。

    返回（页面 URL, 名单, 来源类型, 最像招生目录的候选地址）。
    来源类型 coded/catalog/plain。

    `allowed_domains` 用于把搜索限制在本校域名内——否则会顺着友情链接跑到
    研招网之类的外站，在那里抽出一堆无关的"学院"名。
    """
    best: tuple[int, str, list[str], str] = (0, "", [], "")
    # 链接文字里直接写着"招生专业目录/培养单位"的页面——JS 渲染阶段优先重试这些
    catalog_candidates: list[str] = []

    def allowed(url: str) -> bool:
        return allowed_domains is None or root_domain(url) in allowed_domains

    visited: set[str] = set()
    counter = 0
    queue: list[tuple[int, int, int, str]] = []
    for url in seeds:
        heapq.heappush(queue, (0, 0, counter, url))
        counter += 1

    fetched = 0
    while queue and fetched < max_pages:
        _prio, depth, _seq, url = heapq.heappop(queue)
        if url in visited:
            continue
        visited.add(url)
        fetched += 1
        try:
            resp = client.get(url, timeout=timeout, follow_redirects=True)
        except Exception:  # noqa: BLE001 - 取不到就跳过
            continue
        if resp.status_code >= 400:
            continue
        if "html" not in resp.headers.get("content-type", ""):
            continue
        html = resp.text
        final = str(resp.url)

        coded, catalog, plain = score_page(html, final)
        for method, units in (("coded", coded), ("catalog", catalog), ("plain", plain)):
            if not units:
                continue
            if _is_better(method, len(units), best[3], len(best[2])):
                best = (METHOD_RANK[method], final, units, method)
        # 已经拿到可信且够长的名单，收工
        if best[3] in ("coded", "catalog") and len(best[2]) >= 15:
            break

        if depth >= max_depth:
            continue
        for link in extract_links(html, final):
            target = link["href"]
            if not target.startswith("http") or target in visited:
                continue
            if not allowed(target):
                continue
            text = re.sub(r"\s+", "", link["text"])
            priority = link_priority(text)
            if priority > 3:
                continue
            if priority == 0 and target not in catalog_candidates:
                catalog_candidates.append(target)
            heapq.heappush(queue, (priority, depth + 1, counter, target))
            counter += 1

    return best[1], best[2], best[3], catalog_candidates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="只跑这些学校，逗号分隔")
    parser.add_argument("--max-pages", type=int, default=40, help="每校最多看几页")
    parser.add_argument("--skip-probe", action="store_true", help="跳过子域名探测")
    parser.add_argument("--redo", action="store_true", help="已有名单也重找")
    parser.add_argument("--js", action="store_true",
                        help="静态页找不到时，用浏览器渲染 JS 招生目录再试")
    parser.add_argument("--max-render", type=int, default=8,
                        help="每校最多渲染几个页面")
    args = parser.parse_args()

    existing: dict = {}
    if OUT_PATH.exists():
        existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))

    schools = list(cu.UNIVERSITIES.items())
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        schools = [(n, h) for n, h in schools if n in wanted]

    client = build_client()
    cu._FETCHER = cu.Fetcher(
        client=client, user_agent=client.headers["User-Agent"], min_interval_seconds=0.8
    )

    for index, (school, home) in enumerate(schools, 1):
        print(f"\n[{index}/{len(schools)}] {school}", flush=True)
        prev = existing.get(school) or {}

        if not args.redo and len(prev.get("units") or []) >= cu.MIN_CREDIBLE_UNITS:
            print(f"  已有 {len(prev['units'])} 个单位，保留", flush=True)
            continue

        seeds: list[str] = []
        for url in [cu.GRAD_SITES.get(school, "")] + list(prev.get("tried_sites") or []):
            if url and url not in seeds:
                seeds.append(url)

        if not args.skip_probe:
            found = probe_sites(home, client)
            print(f"  研究生院站点：{found or '（无）'}", flush=True)
            for url in found:
                if url not in seeds:
                    seeds.append(url)

        if not seeds:
            print("  没有可用起点", flush=True)
            existing[school] = {
                "source_url": "", "units": [], "method": "none",
                "tried_sites": [], "checked_at": time.strftime("%Y-%m-%d %H:%M"),
            }
            OUT_PATH.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            continue

        # 只在本校域名内搜索，避免跑到研招网等外站去抽无关的"学院"名
        allowed = {root_domain(u) for u in [home, *seeds] if root_domain(u)}

        url, units, method, catalog_candidates = search_units(
            seeds, client, max_pages=args.max_pages, allowed_domains=allowed
        )

        # 还不可信就直接试通用目录模板地址（重庆大学/同济那两套）
        if len(units) < cu.MIN_CREDIBLE_UNITS:
            for guess in template_urls(home):
                if root_domain(guess) not in allowed:
                    continue
                try:
                    resp = client.get(guess, timeout=20.0, follow_redirects=True)
                except Exception:  # noqa: BLE001 - 猜错地址很正常
                    continue
                if resp.status_code >= 400:
                    continue
                final = str(resp.url)
                coded, catalog, plain = score_page(resp.text, final)
                for cand_method, cand in (("coded", coded), ("catalog", catalog)):
                    if len(cand) >= cu.MIN_CREDIBLE_UNITS and _is_better(
                        cand_method, len(cand), method, len(units)
                    ):
                        url, units, method = final, cand, cand_method
                        print(f"  模板命中：{final}", flush=True)
                        break
                if method in ("coded", "catalog"):
                    break

        # 第二阶段：很多学校的招生目录是 JS 渲染的，静态页里什么都没有。
        # 用浏览器渲染最像目录的若干地址再试一次。
        if len(units) < cu.MIN_CREDIBLE_UNITS and args.js:
            render_targets: list[str] = []
            for cand in [*catalog_candidates, *template_urls(home), url]:
                if cand and cand not in render_targets and root_domain(cand) in allowed:
                    render_targets.append(cand)
            # 优先试硕士目录：地址里带 bs/博士 的是博士目录，单位虽相近但不是我们要的
            render_targets.sort(key=lambda u: (
                bool(re.search(r"bs|boshi|博士", u)),
                bool(re.search(r"ss|shuoshi|硕士|sszyml|sszsml", u)) is False,
            ))
            for cand in render_targets[: args.max_render]:
                try:
                    rendered = fetch_html_rendered(cand, timeout=45)
                except Exception:  # noqa: BLE001 - 渲染失败就试下一个
                    continue
                coded, catalog, plain = score_page(rendered, cand)
                for cand_method, got in (("coded", coded), ("catalog", catalog)):
                    if len(got) >= cu.MIN_CREDIBLE_UNITS and _is_better(
                        cand_method, len(got), method, len(units)
                    ):
                        url, units, method = cand, got, cand_method
                        print(f"  浏览器渲染命中：{cand}", flush=True)
                        break
                if method in ("coded", "catalog"):
                    break

        credible = len(units) >= cu.MIN_CREDIBLE_UNITS
        print(
            f"  -> {len(units)} 个单位（{method}，{'可信' if credible else '不够可信'}）"
            f"  来源 {url or '（没找到）'}",
            flush=True,
        )
        if units and not credible:
            print(f"     样本：{'、'.join(units[:8])}", flush=True)
        elif method == "plain" and credible:
            print(f"     [需人工复核] 样本：{'、'.join(units[:8])}", flush=True)
        existing[school] = {
            "source_url": url,
            "units": units,
            "method": method,
            "tried_sites": seeds,
            "checked_at": time.strftime("%Y-%m-%d %H:%M"),
        }
        OUT_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    client.close()

    credible_schools = [
        k for k, v in existing.items() if len(v.get("units") or []) >= cu.MIN_CREDIBLE_UNITS
    ]
    print(f"\n合计：{len(credible_schools)}/{len(existing)} 所学校拿到可信名单 -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
