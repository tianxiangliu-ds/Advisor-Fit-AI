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
import hashlib
import html as html_module
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.config import settings  # noqa: E402
from advisor_fit.ingest.faculty import (  # noqa: E402
    build_client,
    extract_links,
    fetch_html,
    fetch_html_rendered,
)
from advisor_fit.ingest.fetch import OUTCOME_ROBOTS_DENIED, Fetcher  # noqa: E402
from advisor_fit.ingest.homepage import html_to_text  # noqa: E402
from advisor_fit.ingest.name_verify import (  # noqa: E402
    looks_like_person_name,
    parse_card_link,
    verify_with_llm,
)
from advisor_fit.ingest.supervisor_roster import split_note  # noqa: E402
from advisor_fit.llm.provider import NullLLM, build_llm  # noqa: E402
from advisor_fit.models.advisor import Advisor  # noqa: E402
from advisor_fit.storage.advisor_repo import AdvisorRepository  # noqa: E402

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
    # ---- 以下为扩到 top50 新增的 30 所 ----
    "南京大学": "https://www.nju.edu.cn/",
    "复旦大学": "https://www.fudan.edu.cn/",
    "中山大学": "https://www.sysu.edu.cn/",
    "哈尔滨工业大学": "https://www.hit.edu.cn/",
    "北京师范大学": "https://www.bnu.edu.cn/",
    "南开大学": "https://www.nankai.edu.cn/",
    "山东大学": "https://www.sdu.edu.cn/",
    "厦门大学": "https://www.xmu.edu.cn/",
    "吉林大学": "https://www.jlu.edu.cn/",
    "湖南大学": "https://www.hnu.edu.cn/",
    "东北大学": "https://www.neu.edu.cn/",
    "兰州大学": "https://www.lzu.edu.cn/",
    "西北工业大学": "https://www.nwpu.edu.cn/",
    "西北农林科技大学": "https://www.nwsuaf.edu.cn/",
    "中国农业大学": "https://www.cau.edu.cn/",
    "北京理工大学": "https://www.bit.edu.cn/",
    "北京交通大学": "https://www.bjtu.edu.cn/",
    "北京科技大学": "https://www.ustb.edu.cn/",
    "华北电力大学": "https://www.ncepu.edu.cn/",
    "南京航空航天大学": "https://www.nuaa.edu.cn/",
    "南京理工大学": "https://www.njust.edu.cn/",
    "华东师范大学": "https://www.ecnu.edu.cn/",
    "华东理工大学": "https://www.ecust.edu.cn/",
    "上海大学": "https://www.shu.edu.cn/",
    "苏州大学": "https://www.suda.edu.cn/",
    "郑州大学": "https://www.zzu.edu.cn/",
    "武汉理工大学": "https://www.whut.edu.cn/",
    "华中师范大学": "https://www.ccnu.edu.cn/",
    "华中农业大学": "https://www.hzau.edu.cn/",
    "暨南大学": "https://www.jnu.edu.cn/",
}

# 各校研究生院（用于筛出"确实招研究生的培养单位"）
GRAD_SITES: dict[str, str] = {
    "武汉大学": "https://gs.whu.edu.cn/zsgz/sszs/a2026n.htm",
    "华中科技大学": "https://gs.hust.edu.cn/",
    "中南大学": "https://gra.csu.edu.cn/",
    "清华大学": "https://yjsy.tsinghua.edu.cn/",
    "北京大学": "https://grs.pku.edu.cn/",
    "中国科学技术大学": "https://gradschool.ustc.edu.cn/",
    "四川大学": "https://gs.scu.edu.cn/",
    "东南大学": "https://yjsy.seu.edu.cn/",
    "北京航空航天大学": "https://graduate.buaa.edu.cn/",
    "西安电子科技大学": "https://gr.xidian.edu.cn/",
    "南京大学": "https://grawww.nju.edu.cn/",
    "复旦大学": "https://gsao.fudan.edu.cn/",
    "中山大学": "https://graduate.sysu.edu.cn/",
    "浙江大学": "https://grs.zju.edu.cn/",
    "上海交通大学": "https://www.gs.sjtu.edu.cn/",
    "天津大学": "https://gs.tju.edu.cn/",
    "武汉理工大学": "https://gd.whut.edu.cn/",
    "华中师范大学": "https://gs.ccnu.edu.cn/",
    "华中农业大学": "https://yjs.hzau.edu.cn/",
    "苏州大学": "https://yjs.suda.edu.cn/",
}

# 研究生招生页里"培养单位"链接的文字特征：如「104信息管理学院(2026年)>」
_GRAD_UNIT_RE = re.compile(r"^\d{2,3}\s*(.+?)(?:[（(]\s*20\d{2}\s*年\s*[)）])?[>\s]*$")

# 一所学校招研究生的培养单位通常在 15 个以上；少于这个数说明名单没解析对，
# 此时**必须放弃筛选**——拿半截名单去排除真实学院，会把整所学校爬成 0 人。
MIN_CREDIBLE_UNITS = 12

# 研究生院站内，招生单位名单可能挂在这些栏目下
GRAD_UNIT_PAGE_KEYS = (
    "招生简章", "招生专业目录", "专业目录", "培养单位", "招生单位",
    "硕士招生", "招生专业", "招生信息", "院系设置",
)


def _normalize_unit_name(text: str) -> str:
    """把「104信息管理学院(2026年)>」规整成「信息管理学院」，便于与学院官网名对齐。"""
    name = re.sub(r"\s+", "", text or "")
    name = re.sub(r"^[0-9]{2,3}", "", name)
    name = re.sub(r"[（(]\s*20\d{2}\s*年\s*[)）]", "", name)
    name = name.rstrip(">＞ ").strip()
    return name.replace("（", "(").replace("）", ")")


def _unit_links(html: str, base_url: str) -> list[str]:
    """从一页 HTML 里抽出形如「104信息管理学院(2026年)」的培养单位名。"""
    units: list[str] = []
    seen: set[str] = set()
    for link in extract_links(html, base_url):
        raw = re.sub(r"\s+", "", link["text"])
        if not any(key in raw for key in ("学院", "研究院", "实验室", "学系", "中心", "医院")):
            continue
        if not _GRAD_UNIT_RE.match(raw):
            continue
        name = _normalize_unit_name(raw)
        if len(name) < 3 or name in seen:
            continue
        seen.add(name)
        units.append(name)
    return units


def discover_graduate_units(
    grad_url: str, *, client=None, timeout: float = 20.0,
    min_units: int = MIN_CREDIBLE_UNITS, max_pages: int = 8,
) -> list[str]:
    """从研究生院网站取出"招研究生的培养单位"名单。

    大多数学校的研究生院首页并不直接列培养单位，真正的名单在
    「招生信息 → 硕士招生 → 招生简章 / 专业目录」这类页面里。这里按广度优先
    往下找最多 `max_pages` 页，取找到的最长名单。

    **拿不到可信名单时返回空列表**，由调用方据此不做筛选。宁可不筛选（多爬几个
    无关单位），也绝不能拿半截名单把真实学院排除掉。
    """
    html = _fetch_with_fallback(grad_url, client=client, timeout=timeout)
    best = _unit_links(html, grad_url)
    if len(best) >= min_units:
        return best

    visited = {grad_url}
    frontier: list[tuple[str, str]] = [(grad_url, html)]
    pages = 0
    while frontier and pages < max_pages:
        base, page_html = frontier.pop(0)
        for link in extract_links(page_html, base):
            text = re.sub(r"\s+", "", link["text"])
            if not any(key in text for key in GRAD_UNIT_PAGE_KEYS):
                continue
            url = link["href"]
            if url in visited:
                continue
            visited.add(url)
            pages += 1
            if pages > max_pages:
                break
            try:
                sub_html = _fetch_with_fallback(url, client=client, timeout=timeout)
            except Exception:  # noqa: BLE001 - 单页取不到就试下一页
                continue
            found = _unit_links(sub_html, url)
            if len(found) > len(best):
                best = found
            if len(best) >= min_units:
                print(f"  研究生院名单取自：{url}", flush=True)
                return best
            frontier.append((url, sub_html))

    if len(best) < min_units:
        print(
            f"  研究生院名单只解析出 {len(best)} 个培养单位（少于 {min_units}），"
            f"判定为不可信，本次不做筛选",
            flush=True,
        )
        return []
    return best


def _unit_matches(college: str, units: list[str]) -> bool:
    """学院官网名 与 研究生院培养单位名 是否指同一个单位。"""
    target = _normalize_unit_name(college)
    for unit in units:
        if target == unit:
            return True
        if len(target) >= 4 and len(unit) >= 4 and (target in unit or unit in target):
            return True
    return False

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
# 「学校概况」这类二级入口，用于再找一层院系目录
OVERVIEW_KEYS = (
    "学校概况", "学校简介", "学校介绍", "本校概况", "大学概况", "学校基本信息",
    "组织机构", "机构设置", "院系设置",
)
# 明显不是人名的词，避免把导航项当成导师。
# 注意：像「党群工作」「国际交流」「武大主页」这种，首字（党/国/武）本身就是姓氏，
# 光靠"首字是姓氏"滤不掉，必须显式列出来。
NAME_STOPWORDS = {
    "学院", "大学", "学校", "教授", "教师", "师资", "首页", "更多", "详情", "查看",
    "新闻", "通知", "公告", "招生", "就业", "党建", "工会", "校友", "人才", "科研",
    "教学", "学生", "研究生", "本科生", "实验", "中心", "办公室", "委员会", "研究所",
    "实验室", "系所", "概况", "简介", "联系", "地图", "导航", "登录", "搜索", "下载",
    "上一篇", "下一篇", "上页", "返回", "列表", "全部", "展开", "收起", "关于", "服务",
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


# 「师资」页上常见的职称写法，长的排前面避免「教授」吃掉「副教授」
TITLE_WORDS = (
    "助理研究员", "特聘副研究员", "特聘研究员", "助理教授", "特聘教授",
    "副研究员", "副教授", "研究员", "讲师", "教授", "博士后",
    "高级工程师", "工程师", "实验师", "讲师（博导）",
)
_TAG_RE = re.compile(r"<[^>]+>")
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class CollegeResult:
    college: str
    list_url: str = ""
    names: list[str] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    note: str = ""


_JS_ENABLED = False
_LLM = None
_FETCHER: Fetcher | None = None


GENERIC_UNIT_NAMES = {
    "学院", "学院（系）", "学院(系)", "院系", "教学单位", "教学科研单位", "教学机构",
    "院系设置", "组织机构", "直属单位", "科研机构", "研究机构",
}


def _expand_generic_entries(
    colleges: list[tuple[str, str]], *, client=None, timeout: float = 20.0
) -> list[tuple[str, str]]:
    """有些学校「学院（系）」本身是个列表页，需要再展开一层才是真正的学院。"""
    expanded: list[tuple[str, str]] = []
    seen = {name for name, _ in colleges}
    for name, url in colleges:
        if name not in GENERIC_UNIT_NAMES and "/list." not in url:
            expanded.append((name, url))
            continue
        try:
            # 这类"列表页"通常靠 JS 加载，直接上渲染
            html = _fetch(url, client=client, timeout=timeout, js=True)
        except Exception:  # noqa: BLE001
            continue
        for sub_name, sub_url in _college_links(html, url):
            if sub_name in seen or sub_name in GENERIC_UNIT_NAMES:
                continue
            seen.add(sub_name)
            expanded.append((sub_name, sub_url))
    return expanded or colleges


def _fetch(url: str, *, client=None, timeout: float = 20.0, js: bool = False) -> str:
    """取网页。js=True 时用浏览器渲染（应对 JS 动态页），否则走普通请求。"""
    if _FETCHER is not None:
        result = _FETCHER.fetch(url)
        if not result.ok:
            if result.outcome == OUTCOME_ROBOTS_DENIED or result.status_code in (401, 403, 429):
                raise PermissionError(result.friendly_error())
            raise RuntimeError(result.friendly_error())
        if not (js or _JS_ENABLED):
            return result.text
    if js or _JS_ENABLED:
        return fetch_html_rendered(url, timeout=max(timeout, 30.0))
    return fetch_html(url, client=client, timeout=timeout)


def _fetch_with_fallback(url: str, *, client=None, timeout: float = 20.0) -> str:
    """先普通抓取；如果拿到的是"空壳"（正文和链接都很少），换浏览器渲染重试一次。"""
    html = ""
    try:
        html = _fetch(url, client=client, timeout=timeout)
        if len(html_to_text(html)) >= 200 and len(extract_links(html, url)) >= 5:
            return html
    except PermissionError:
        raise
    except Exception:  # noqa: BLE001 - 普通抓取失败就走渲染
        html = ""
    try:
        return _fetch(url, client=client, timeout=timeout, js=True)
    except PermissionError:
        raise
    except Exception:  # noqa: BLE001
        return html


def _norm(url: str, base: str) -> str:
    return urljoin(base, url)


def _dedupe(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def candidate_dir_urls(html: str, base: str) -> list[str]:
    """收集所有可能是「院系设置」入口的链接，强的排前面。"""
    strong: list[str] = []
    weak: list[str] = []
    for link in extract_links(html, base):
        text = re.sub(r"\s+", "", link["text"])
        if any(key in text for key in COLLEGE_DIR_KEYS):
            strong.append(link["href"])
            continue
        path = urlparse(link["href"]).path.lower()
        if any(token in path for token in ("yxsz", "jgsz", "yuanxi", "college", "school")):
            weak.append(link["href"])
    return _dedupe(strong + weak)


def _college_links(html: str, base: str) -> list[tuple[str, str]]:
    colleges: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in extract_links(html, base):
        # Cross-section page anchors (e.g. 交流合作#孔子学院) are navigation,
        # not entries in the college directory.
        if urlparse(link["href"]).fragment:
            continue
        text = re.sub(r"\s+", "", link["text"])
        if not (2 <= len(text) <= 16):
            continue
        if not any(key in text for key in ("学院", "学部", "学系", "研究院", "研究中心", "实验室")):
            continue
        if text in seen:
            continue
        seen.add(text)
        colleges.append((text, link["href"]))
    return colleges


def discover_colleges(
    home: str, *, client=None, timeout: float = 20.0
) -> list[tuple[str, str]]:
    """从学校首页找到各学院（名称, 链接）。

    高校站点命名差异极大，所以这里做多轮尝试：
    ① 首页本身如果就列了学院，直接用；
    ② 依次尝试首页里所有像「院系设置」的入口，取效果最好的一个；
    ③ 还不行就从「学校概况」这类页面再找一层。
    """
    home_html = _fetch_with_fallback(home, client=client, timeout=timeout)
    best = _college_links(home_html, home)

    for url in candidate_dir_urls(home_html, home):
        try:
            html = _fetch_with_fallback(url, client=client, timeout=timeout)
        except Exception:  # noqa: BLE001 - 单个入口失败就试下一个
            continue
        links = _college_links(html, url)
        if len(links) > len(best):
            best = links
    if len(best) >= 3:
        return _expand_generic_entries(best, client=client, timeout=timeout)

    # 二级：先进入「学校概况 / 组织机构」这类页面，再从里面找院系入口
    for link in extract_links(home_html, home):
        text = re.sub(r"\s+", "", link["text"])
        if not any(key in text for key in OVERVIEW_KEYS):
            continue
        try:
            overview_html = _fetch_with_fallback(link["href"], client=client, timeout=timeout)
        except Exception:  # noqa: BLE001
            continue
        for url in candidate_dir_urls(overview_html, link["href"])[:4]:
            try:
                html = _fetch_with_fallback(url, client=client, timeout=timeout)
            except Exception:  # noqa: BLE001
                continue
            links = _college_links(html, url)
            if len(links) > len(best):
                best = links
        if len(best) >= 3:
            break

    if len(best) < 3:
        # 最后一招：强制用浏览器渲染首页与候选入口各试一次
        for url in [home, *candidate_dir_urls(home_html, home)[:3]]:
            try:
                html = _fetch(url, client=client, timeout=timeout, js=True)
            except Exception:  # noqa: BLE001
                continue
            links = _college_links(html, url)
            if len(links) > len(best):
                best = links
            if len(best) >= 3:
                break

    return _expand_generic_entries(best, client=client, timeout=timeout)


def find_faculty_page(college_url: str, *, client=None, timeout: float = 20.0) -> str:
    """在学院主页里找「师资队伍」入口。"""
    try:
        html = _fetch_with_fallback(college_url, client=client, timeout=timeout)
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
    """只取姓名（用于判断这一页值不值得细解析）。"""
    return [item["name"] for item in extract_entries(html, base_url)]


def _cell_text(fragment: str) -> str:
    return re.sub(r"\s+", "", _TAG_RE.sub("", fragment))


def extract_table_rows(html: str) -> list[list[str]]:
    """把 <table> 的每一行拆成单元格文本——很多师资页用表格列 姓名/职称/方向/邮箱。"""
    rows: list[list[str]] = []
    for row_html in _TR_RE.findall(html):
        cells = [_cell_text(cell) for cell in _TD_RE.findall(row_html)]
        cells = [cell for cell in cells if cell]
        if len(cells) >= 2:
            rows.append(cells)
    return rows


def _match_title(text: str) -> str:
    for word in TITLE_WORDS:
        if text == word:
            return word
    for word in TITLE_WORDS:
        if word in text and len(text) <= 12:
            return word
    return ""


def extract_entries(html: str, base_url: str) -> list[dict]:
    """提取导师条目：姓名 +（能拿到的）职称 / 邮箱 / 研究方向 / 个人主页。

    两条路并用：
    ① 链接：锚文本是中文姓名的，拿到姓名与个人主页链接；
    ② 表格：姓名/职称/邮箱/方向并排成列时，把同一行的其它单元格补到这个人身上。
    """
    entries: dict[str, dict] = {}

    def _entry(name: str) -> dict:
        return entries.setdefault(
            name,
            {"name": name, "title": "", "email": "", "directions": "", "homepage_url": ""},
        )

    for link in extract_links(html, base_url):
        text = re.sub(r"\s+", "", link["text"])
        if text in NAME_STOPWORDS:
            continue
        if looks_like_person_name(text):
            item = _entry(text)
            if not item["homepage_url"]:
                item["homepage_url"] = link["href"]
            continue
        # 卡片式链接：整张人物卡片是一个 <a>，链接文字是「姓名+单位+职称+邮箱」的长串
        card = parse_card_link(text)
        if card and card.get("name") and (card.get("title") or card.get("email")):
            item = _entry(card["name"])
            if card.get("title") and not item["title"]:
                item["title"] = card["title"]
            if card.get("email") and not item["email"]:
                item["email"] = card["email"]
            if not item["homepage_url"]:
                item["homepage_url"] = link["href"]

    # On some official sites the whole teacher card is linked, while the
    # opening anchor's title attribute holds the clean name (sometimes spaced).
    for anchor in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html, re.I | re.S):
        attrs = anchor.group("attrs")
        title_attr = re.search(r"\btitle\s*=\s*['\"]([^'\"]+)['\"]", attrs, re.I)
        href_attr = re.search(r"\bhref\s*=\s*['\"]([^'\"]+)['\"]", attrs, re.I)
        if not title_attr or not href_attr:
            continue
        name = re.sub(r"\s+", "", html_module.unescape(title_attr.group(1)))
        if name in NAME_STOPWORDS or not looks_like_person_name(name):
            continue
        body = re.sub(r"\s+", "", html_module.unescape(_TAG_RE.sub("", anchor.group("body"))))
        if name not in body:
            continue
        item = _entry(name)
        item["homepage_url"] = item["homepage_url"] or _norm(href_attr.group(1), base_url)
        role = re.search(rf"{re.escape(name)}.{{0,8}}?({'|'.join(TITLE_WORDS)})", body)
        if role and not item["title"]:
            item["title"] = role.group(1)
        email = _EMAIL_RE.search(body)
        if email and not item["email"]:
            item["email"] = email.group(0)

    for row in extract_table_rows(html):
        name = ""
        for cell in row:
            if cell not in NAME_STOPWORDS and looks_like_person_name(cell):
                name = cell
                break
        if not name:
            continue
        item = _entry(name)
        for cell in row:
            if cell == name:
                continue
            if not item["email"]:
                found = _EMAIL_RE.search(cell)
                if found:
                    item["email"] = found.group(0)
                    continue
            if not item["title"]:
                title = _match_title(cell)
                if title:
                    item["title"] = title
                    continue
            if not item["directions"] and 6 <= len(cell) <= 120 and not _EMAIL_RE.search(cell):
                item["directions"] = cell

    return list(entries.values())


def collect_paginated_entries(
    html: str, list_url: str, *, client=None, delay: float = 1.0, timeout: float = 20.0,
    page_contexts: dict[str, str] | None = None,
) -> list[dict]:
    """Follow faculty-list pagination without following unrelated site navigation."""
    entries = {entry["name"]: entry for entry in extract_entries(html, list_url)}
    if page_contexts is not None:
        page_contexts[list_url] = html_to_text(html)
    visited = {list_url}
    pending = [(html, list_url)]
    page_prefix = urlparse(list_url).path.removesuffix(".htm") + "/"
    host = urlparse(list_url).netloc
    while pending:
        page_html, page_url = pending.pop(0)
        for link in extract_links(page_html, page_url):
            target = link["href"]
            parsed = urlparse(target)
            label = link["text"].strip()
            if (
                target in visited
                or parsed.netloc != host
                or not parsed.path.startswith(page_prefix)
                or not (label in {"下页", "尾页", "下一页"} or label.isdigit())
            ):
                continue
            visited.add(target)
            try:
                time.sleep(delay)
                following = _fetch_with_fallback(target, client=client, timeout=timeout)
            except Exception:  # noqa: BLE001 - retain earlier pages; report missing page later
                continue
            pending.append((following, target))
            if page_contexts is not None:
                page_contexts[target] = html_to_text(following)
            for entry in extract_entries(following, target):
                previous = entries.setdefault(entry["name"], entry)
                for key in ("title", "email", "directions", "homepage_url"):
                    if not previous.get(key) and entry.get(key):
                        previous[key] = entry[key]
    return list(entries.values())


def collect_faculty_siblings(
    html: str, list_url: str, *, client=None, delay: float = 1.0,
    timeout: float = 20.0, page_contexts: dict[str, str] | None = None,
) -> list[dict]:
    """Union adjacent faculty categories, such as professor/associate/lecturer."""
    entries = {
        entry["name"]: entry for entry in collect_paginated_entries(
            html, list_url, client=client, delay=delay, timeout=timeout,
            page_contexts=page_contexts,
        )
    }
    directory = urlparse(list_url).path.rsplit("/", 1)[0] + "/"
    categories = {"教授", "副教授", "讲师", "专任教师", "在岗教师", "全职教师", "研究员"}
    seen = {list_url}
    for link in extract_links(html, list_url):
        target = link["href"]
        parsed = urlparse(target)
        label = re.sub(r"\s+", "", link["text"])
        if (
            target in seen or label not in categories
            or parsed.netloc != urlparse(list_url).netloc
            or not parsed.path.startswith(directory)
            or "/" in parsed.path[len(directory):]
        ):
            continue
        seen.add(target)
        try:
            time.sleep(delay)
            following = _fetch_with_fallback(target, client=client, timeout=timeout)
        except Exception:  # noqa: BLE001 - one category cannot erase earlier pages
            continue
        for entry in collect_paginated_entries(
            following, target, client=client, delay=delay, timeout=timeout,
            page_contexts=page_contexts,
        ):
            previous = entries.setdefault(entry["name"], entry)
            for key in ("title", "email", "directions", "homepage_url"):
                if not previous.get(key) and entry.get(key):
                    previous[key] = entry[key]
    return list(entries.values())


_DIRECTION_MARKERS = ("研究方向", "研究领域", "主要研究", "研究兴趣", "科研方向")


def extract_detail_fields(html: str, name: str = "") -> dict:
    """从个人详情页正文里挖出职称 / 邮箱 / 研究方向。

    职称最容易踩的坑：页面导航里就有「博士后」「教授」这类词。
    所以优先看"和姓名出现在同一行"的职称，其次看显式的「职称：xxx」，
    最后才退化为普通行匹配，并且跳过"整行就是一个职称词"的导航项。
    """
    text = html_to_text(html)
    fields = {"title": "", "email": "", "directions": ""}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line == "正文" and name and any(name in part for part in lines[index + 1:index + 4]):
            lines = lines[index + 1:]
            break

    found = _EMAIL_RE.search("\n".join(lines))
    if found:
        fields["email"] = found.group(0)

    plain_title = ""

    for index, line in enumerate(lines):
        if line == name and index + 1 < len(lines) and lines[index + 1] in TITLE_WORDS:
            fields["title"] = lines[index + 1]
        if not fields["title"] and name and name in line:
            title = _match_title(line)
            if title:
                fields["title"] = title
        if not fields["title"] and line not in TITLE_WORDS:
            explicit = re.search(r"(?:职称|职务|岗位)[：:]\s*([^\s，。；、]{2,14})", line)
            if explicit:
                fields["title"] = explicit.group(1)
            elif _match_title(line) and len(line) <= 30:
                plain_title = plain_title or _match_title(line)
        if not fields["directions"]:
            for marker in _DIRECTION_MARKERS:
                # 只认「以关键词开头」的行，避免把论文标题里的"研究"误当研究方向
                if line.startswith(marker):
                    value = line[len(marker) :].lstrip("：: 　").strip()
                    if not value and line == marker and index + 1 < len(lines):
                        value = lines[index + 1]
                    if value in {"个人简介", "研究方向", "学术成果", "研究领域"}:
                        value = ""
                    if 2 <= len(value) <= 120:
                        fields["directions"] = value
                    break

    if not fields["title"]:
        fields["title"] = plain_title
    # Some colleges put both the role and research interests in a short
    # biography paragraph rather than labelled fields. Only inspect the
    # paragraph that begins with this person's name, not navigation or papers.
    if name and (not fields["title"] or not fields["directions"]):
        title_pattern = "|".join(re.escape(word) for word in TITLE_WORDS)
        for line in lines:
            if not line.startswith((name + "，", name + ",", name + "：")):
                continue
            if not fields["title"]:
                role = re.search(
                    rf"(?:现为|现任|担任).{{0,30}}?({title_pattern})(?=[，。；、\s]|$)",
                    line[:300],
                )
                if role:
                    fields["title"] = role.group(1)
            if not fields["directions"]:
                interest = re.search(
                    r"(?:学术兴趣包括|研究兴趣包括|研究方向为)[：:]?(.{2,120}?)(?:，现|。|；|$)",
                    line[:500],
                )
                if interest:
                    fields["directions"] = interest.group(1).strip("，、 ")
            break
    return fields


# 师资分类页常见的链接文本：出现这些词的链接，很可能指向下一层名单
SUBPAGE_KEYS = (
    "系", "所", "中心", "教研室", "教师", "人员", "队伍", "系列", "教授", "副教授",
    "讲师", "博导", "硕导", "师资", "名录", "国内", "国外", "全部", "全体", "专任",
    "在职", "教师名录", "导师", "团队",
)


def _sub_page_candidates(html: str, url: str) -> list[dict]:
    """挑出"可能是下一层名单页"的链接。

    注意：**不限制在前 N 个链接里找**。像杜伦联合学院的「国内师资 / 国外师资」
    排在整页第 30 个链接之后，之前截断在前 30 个，导致直接漏掉。
    """
    picked: list[dict] = []
    seen: set[str] = set()
    normalized_url = url.rstrip("/")
    for link in extract_links(html, url):
        href = (link["href"] or "").strip()
        if not href or href.rstrip("/") == normalized_url or href in seen:
            continue
        if urlparse(href).netloc != urlparse(url).netloc:
            continue
        seen.add(href)
        text = re.sub(r"\s+", "", link["text"])
        if not text or not any(key in text for key in SUBPAGE_KEYS):
            continue
        picked.append({"text": text, "href": href})
    return picked


def _collect_entries(
    url: str,
    *,
    client=None,
    delay: float,
    timeout: float = 20.0,
    depth: int = 1,
    visited: set[str] | None = None,
    max_depth: int = 3,
    enough: int = 25,
) -> list[dict]:
    """递归收集姓名：逐层比较，取人最多的那一支。

    为什么要"够多也继续看"：很多学院的师资页本身有十几个链接，但它们是
    「历史名家 / 学术研究 / 海外学习」这类栏目，看着像人名、其实是界面词。
    如果只看数量就停在这里，真正装名单的「专任教师」子页（可能有上百人）就会被漏掉。
    所以只有在数量足够多（>= enough）或没有下级候选时才停。
    """
    visited = visited if visited is not None else set()
    if depth > max_depth or url in visited:
        return []
    visited.add(url)
    try:
        time.sleep(delay)
        html = _fetch_with_fallback(url, client=client, timeout=timeout)
    except Exception:  # noqa: BLE001 - 单页失败不影响其它分支
        return []

    entries = extract_entries(html, url)
    if depth >= max_depth or len(entries) >= enough:
        return entries

    candidates = _sub_page_candidates(html, url)
    if not candidates:
        return entries

    combined = {entry["name"]: entry for entry in entries}
    for link in candidates:
        sub = _collect_entries(
            link["href"],
            client=client,
            delay=delay,
            timeout=timeout,
            depth=depth + 1,
            visited=visited,
            max_depth=max_depth,
            enough=enough,
        )
        for entry in sub:
            previous = combined.setdefault(entry["name"], entry)
            for key in ("title", "email", "directions", "homepage_url"):
                if not previous.get(key) and entry.get(key):
                    previous[key] = entry[key]
    return list(combined.values())


def enrich_with_details(
    entries: list[dict],
    *,
    client=None,
    delay: float,
    limit: int,
    timeout: float = 20.0,
) -> int:
    """按个人主页链接补采职称/邮箱/研究方向；只为前 limit 位补，避免耗时失控。"""
    filled = 0
    for entry in entries[:limit]:
        url = entry.get("homepage_url") or ""
        if not url or not url.startswith("http"):
            continue
        if entry.get("title") and entry.get("email") and entry.get("directions"):
            continue
        try:
            time.sleep(delay)
            html = _fetch_with_fallback(url, client=client, timeout=timeout)
        except Exception:  # noqa: BLE001 - 单个人失败不影响整体
            continue
        detail = extract_detail_fields(html, entry.get("name", ""))
        text = html_to_text(html)
        marker = "\n正文\n"
        if marker in text:
            text = text.split(marker, 1)[1]
        entry["profile_text"] = text.strip()
        entry["content_hash"] = hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
        entry["source_url"] = url
        for key, value in detail.items():
            if value and not entry.get(key):
                entry[key] = value
                filled += 1
    return filled


def _finalize_entries(entries: list[dict], html: str, result: CollegeResult) -> list[dict]:
    """对候选做规则之外的复核；不能按常见姓氏删除罕见姓名。"""
    if not entries or _LLM is None:
        return entries
    context = html_to_text(html)
    eligible = [item["name"] for item in entries if item["name"] in context]
    keep: list[str] = []
    for start in range(0, len(eligible), 25):
        batch = eligible[start:start + 25]
        # The verifier only receives its first 4,000 characters. Give every
        # candidate a nearby source excerpt, including late pagination pages.
        excerpts = []
        for name in batch:
            position = context.find(name)
            excerpts.append(context[max(0, position - 40):position + 100])
        keep.extend(verify_with_llm(_LLM, batch, "\n".join(excerpts)))
    before = len(entries)
    allowed = set(keep)
    kept = [item for item in entries if item["name"] not in eligible or item["name"] in allowed]
    if len(kept) != before and "AI 复核" not in result.note:
        note = f"AI 复核剔除 {before - len(kept)} 个非人名"
        result.note = f"{result.note}；{note}".strip("；")
    return kept


def crawl_college(
    college: str,
    url: str,
    *,
    client=None,
    delay: float,
    timeout: float = 20.0,
    details_per_college: int = 0,
) -> CollegeResult:
    result = CollegeResult(college=college)
    try:
        list_url = find_faculty_page(url, client=client, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result.note = f"找师资页失败：{type(exc).__name__}"
        return result
    if not list_url:
        result.note = "未找到师资队伍入口"
        return result

    result.list_url = list_url
    try:
        time.sleep(delay)
        html = _fetch_with_fallback(list_url, client=client, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result.note = f"抓师资页失败：{type(exc).__name__}"
        return result

    contexts: dict[str, str] = {}
    paginated = collect_faculty_siblings(
        html, list_url, client=client, delay=delay, timeout=timeout,
        page_contexts=contexts,
    )
    entries = _finalize_entries(paginated, "\n".join(contexts.values()), result)
    if len(entries) < 3:
        # 静态抓到的姓名太少：这一页很可能是 JS 渲染出来的，强制重抓一次
        try:
            time.sleep(delay)
            rendered = _fetch(list_url, client=client, timeout=timeout, js=True)
            rendered_entries = _finalize_entries(
                extract_entries(rendered, list_url), rendered, result
            )
            if len(rendered_entries) > len(entries):
                entries = rendered_entries
                html = rendered
                result.note = f"{result.note}；经浏览器渲染取得".strip("；")
        except Exception:  # noqa: BLE001 - 渲染失败就用静态结果
            pass

    # 还是太少就递归下钻：师资页常见「专任教师/国内师资/国外师资/各系所」等分类入口，
    # 真正的名单在下一层甚至下两层。**必须在过滤之后再判断**——很多分类页本身
    # 有几十个界面词链接，过滤前看着"够多"，过滤后其实一个真人都没有。
    if len(entries) < 3:
        drilled = _collect_entries(
            list_url,
            client=client,
            delay=delay,
            timeout=timeout,
            depth=1,
            visited=set(),  # _collect_entries 自己会把入口加入 visited
            max_depth=3,
        )
        verified = _finalize_entries(drilled, html, result) if drilled else []
        if len(verified) > len(entries):
            entries = verified
            result.note = f"{result.note}；经多层分类页下钻取得".strip("；")

    result.entries = entries
    result.names = [item["name"] for item in entries]

    if entries and details_per_college > 0:
        got = enrich_with_details(
            entries, client=client, delay=delay, limit=details_per_college, timeout=timeout
        )
        if got:
            result.note = (result.note + f"；补采 {got} 个字段").strip("；")
    if not entries:
        text_len = len(html_to_text(html))
        result.note = f"页面无姓名链接（正文 {text_len} 字，可能是 JS 动态渲染）"
    return result


def crawl_university(
    university: str,
    home: str,
    *,
    client=None,
    delay: float = 1.0,
    discover_only: bool = False,
    max_colleges: int = 0,
    details_per_college: int = 0,
    only_college: str = "",
    checkpoint_dir: Path | None = None,
    graduate_units: list[str] | None = None,
) -> list[CollegeResult]:
    print(f"\n{'=' * 70}\n【{university}】{home}\n{'=' * 70}", flush=True)
    colleges = discover_colleges(home, client=client)
    print(f"发现学院：{len(colleges)} 个", flush=True)
    if graduate_units:
        kept = [item for item in colleges if _unit_matches(item[0], graduate_units)]
        dropped = [item[0] for item in colleges if not _unit_matches(item[0], graduate_units)]
        # 二道保险：正常学校的研究生培养单位能覆盖大半学院。如果排除比例过半，
        # 说明名单和学院官网名对不上（多半是解析错页），此时不筛选才是对的。
        if len(colleges) >= 6 and len(kept) * 2 < len(colleges):
            print(
                f"  警告：培养单位名单只匹配上 {len(kept)}/{len(colleges)} 个学院，"
                f"判定名单不可信，本次不筛选",
                flush=True,
            )
        else:
            print(
                f"按研究生院培养单位筛选：保留 {len(kept)} 个，排除 {len(dropped)} 个",
                flush=True,
            )
            if dropped:
                print(f"  已排除：{'、'.join(dropped[:8])}", flush=True)
            colleges = kept
    if only_college:
        colleges = [(name, url) for name, url in colleges if name == only_college]
    if max_colleges:
        colleges = colleges[:max_colleges]
    if discover_only:
        for name, url in colleges:
            print(f"  · {name}  {url}", flush=True)
        return [CollegeResult(college=name, list_url=url) for name, url in colleges]

    results: list[CollegeResult] = []
    for index, (name, url) in enumerate(colleges, 1):
        checkpoint = checkpoint_dir / f"{index:02d}.json" if checkpoint_dir else None
        outcome = None
        if checkpoint and checkpoint.exists():
            try:
                saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                if saved["college_url"] == url and saved["result"]["college"] == name:
                    candidate = CollegeResult(**saved["result"])
                    if candidate.entries:
                        outcome = candidate
                        print(f"  [{index}/{len(colleges)}] {name}：复用本轮断点", flush=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if outcome is None:
            time.sleep(delay)
            outcome = crawl_college(
                name, url, client=client, delay=delay, details_per_college=details_per_college
            )
            if checkpoint:
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                staged = checkpoint.with_suffix(".tmp")
                staged.write_text(
                    json.dumps({"college_url": url, "result": asdict(outcome)}, ensure_ascii=False),
                    encoding="utf-8",
                )
                staged.replace(checkpoint)
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
        item.entries = [entry for entry in item.entries if entry["name"] not in nav]
        if not item.names and not item.note:
            item.note = "过滤后为空（原文只有站点导航项）"
    return results, sorted(nav)


def save(
    university: str, results: list[CollegeResult], *, output_path: Path | None = None
) -> int:
    results, dropped = drop_site_navigation(results)
    if dropped:
        sample = "、".join(dropped[:5])
        print(f"  （已丢弃 {len(dropped)} 个站点导航词，例如：{sample}）", flush=True)

    advisors: list[Advisor] = []
    payload = []
    for item in results:
        entries = item.entries or [
            {"name": name, "title": "", "email": "", "directions": "", "homepage_url": ""}
            for name in item.names
        ]
        for entry in entries:
            name, note = split_note(entry["name"])
            if not name:
                continue
            advisors.append(
                Advisor(
                    university=university,
                    department=item.college,
                    name=name,
                    note=note,
                    title=entry.get("title", ""),
                    email=entry.get("email", ""),
                    research_directions=(
                        [entry["directions"]] if entry.get("directions") else []
                    ),
                    homepage_url=entry.get("homepage_url", ""),
                    profile_text=entry.get("profile_text", ""),
                    sources=["official"],
                    source_url=entry.get("source_url") or item.list_url,
                    content_hash=entry.get("content_hash", ""),
                )
            )
        payload.append(
            {
                "college": item.college,
                "list_url": item.list_url,
                "count": len(entries),
                "entries": entries,
                "note": item.note,
            }
        )

    out_dir = ROOT / "data" / "official_crawl"
    out_dir.mkdir(parents=True, exist_ok=True)
    (output_path or out_dir / f"{university}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if advisors:
        repo = AdvisorRepository(settings.data_dir / "advisors.db")
        repo.upsert_many(advisors, source="official")
    return len(advisors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--university", default="武汉大学")
    parser.add_argument("--home", default="")
    parser.add_argument("--all", action="store_true", help="跑内置的全部 top20")
    parser.add_argument("--only", default="", help="只跑这些学校，用逗号分隔")
    parser.add_argument("--college", default="", help="只采指定学院，明细单独保存")
    parser.add_argument("--output", default="", help="本次采集明细 JSON 路径，避免覆盖旧结果")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0, help="每次请求间隔秒数")
    parser.add_argument("--max-colleges", type=int, default=0)
    parser.add_argument(
        "--js", action="store_true",
        help="强制用浏览器渲染取页（应对 JS 动态站点，较慢）",
    )
    parser.add_argument(
        "--details-per-college", type=int, default=0,
        help="每个学院额外进入前 N 位导师的个人主页，补采职称/邮箱/研究方向（0=不补）",
    )
    parser.add_argument(
        "--graduate", action="store_true",
        help="先用研究生院的培养单位名单筛选，只爬确实招研究生的学院",
    )
    parser.add_argument(
        "--details", action="store_true",
        help="爬完自动为所有人补采详情页字段（相当于 details-per-college 不限量）",
    )
    args = parser.parse_args()

    global _JS_ENABLED, _LLM, _FETCHER
    _JS_ENABLED = args.js
    llm = build_llm()
    _LLM = None if isinstance(llm, NullLLM) else llm
    if _LLM is None:
        print("提示：未配置 LLM_API_KEY，本次只用规则过滤（姓名里含界面词的一律剔除）")
    else:
        print("已启用大模型复核：候选姓名会再经 AI 逐条确认")

    if args.only:
        wanted = [name.strip() for name in args.only.split(",") if name.strip()]
        targets = [(name, UNIVERSITIES.get(name, "")) for name in wanted]
    elif args.all:
        targets = list(UNIVERSITIES.items())
    else:
        targets = [(args.university, args.home or UNIVERSITIES.get(args.university, ""))]

    total = 0
    client = build_client()
    _FETCHER = Fetcher(
        client=client, user_agent=client.headers["User-Agent"],
        min_interval_seconds=max(args.delay, 0.8),
    )
    try:
        for university, home in targets:
            if not home:
                print(f"没有 {university} 的官网地址，请用 --home 指定")
                continue
            try:
                units: list[str] | None = None
                if args.graduate:
                    grad_url = GRAD_SITES.get(university, "")
                    if grad_url:
                        try:
                            units = discover_graduate_units(grad_url, client=client)
                            print(f"研究生院培养单位：{len(units)} 个", flush=True)
                        except Exception as exc:  # noqa: BLE001 - 取不到就退回全量
                            print(f"  研究生院名单获取失败（{type(exc).__name__}），本次不筛选")
                    else:
                        print("  没有内置的研究生院地址，本次不筛选")
                detail_limit = 10**6 if args.details else args.details_per_college
                results = crawl_university(
                    university,
                    home,
                    client=client,
                    delay=args.delay,
                    discover_only=args.discover_only,
                    max_colleges=args.max_colleges,
                    details_per_college=detail_limit,
                    only_college=args.college,
                    graduate_units=units,
                    checkpoint_dir=(
                        Path(args.output + ".checkpoints") if args.output else None
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - 单所学校失败不影响其它
                print(f"  {university} 整体失败：{type(exc).__name__}: {exc}", flush=True)
                continue
            if args.discover_only:
                continue
            output_path = Path(args.output) if args.output else (
                ROOT / "data" / "official_crawl" / f"{university}-{args.college}.json"
                if args.college else None
            )
            saved = save(university, results, output_path=output_path)
            total += saved
            hit = sum(1 for item in results if item.names)
            print(f"  → {university}：{hit}/{len(results)} 个学院采到名单", flush=True)
            print(f"     共 {saved} 位导师", flush=True)
    finally:
        client.close()

    if not args.discover_only:
        print(f"\n合计写入 {total} 位官网导师")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
