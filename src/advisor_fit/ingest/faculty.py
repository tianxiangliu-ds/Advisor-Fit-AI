"""高校官网导师信息采集：师资列表 → 个人主页 → 结构化导师档案。

用 LLM 从异构的中文高校页面中鲁棒地抽取字段；无 LLM 时用链接/邮箱正则兜底。
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel

from advisor_fit.ingest.homepage import html_to_text
from advisor_fit.llm.prompts import prompt_text
from advisor_fit.models.faculty import FacultyRecord

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag != "a":
            return
        attrs = dict(attrs)
        href = (attrs.get("href") or "").strip()
        text = ""
        self._current = [href, text]

    def handle_data(self, data) -> None:
        if getattr(self, "_current", None) is not None:
            self._current[1] += data

    def handle_endtag(self, tag) -> None:
        if tag == "a" and getattr(self, "_current", None) is not None:
            href, text = self._current
            text = re.sub(r"\s+", " ", text).strip()
            if href and text:
                self.links.append((text, href))
            self._current = None


def extract_links(html: str, base_url: str) -> list[dict]:
    parser = _LinkExtractor()
    parser.feed(html)
    return [
        {"text": text, "href": urljoin(base_url, href)}
        for text, href in parser.links
        if not href.startswith(("javascript:", "mailto:", "#"))
    ]


class FacultyLink(BaseModel):
    name: str
    href: str


class FacultyListOutput(BaseModel):
    faculty: list[FacultyLink] = []


class FacultyPageOutput(BaseModel):
    title: str = ""
    email: str = ""
    research_areas: list[str] = []
    research_directions: list[str] = []
    publications: list[str] = []


_LIST_INSTRUCTIONS = prompt_text("faculty_list")

_PAGE_INSTRUCTIONS = prompt_text("faculty_page")


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def build_client(timeout: float = 20.0) -> httpx.Client:
    """统一的浏览器式客户端：复用连接与 Cookie，减少被 412/403 拦掉的概率。"""
    return httpx.Client(timeout=timeout, follow_redirects=True, headers=_BROWSER_HEADERS)


def fetch_html(url: str, *, client: httpx.Client | None = None, timeout: float = 20.0) -> str:
    client = client or build_client(timeout)
    resp = client.get(url)
    resp.raise_for_status()
    return resp.text


def fetch_html_rendered(url: str, *, timeout: float = 30.0) -> str:
    """用 Playwright 渲染 JS 页面后取 HTML（需安装 playwright + chromium）。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001 - 未安装 Playwright
        raise RuntimeError(
            "需要安装 playwright：pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        html = page.content()
        browser.close()
    return html


def _faculty_id(university: str, college: str, name: str, homepage_url: str) -> str:
    raw = f"{university}\n{college}\n{name}\n{homepage_url}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _heuristic_faculty_links(links: list[dict]) -> list[FacultyLink]:
    """启发式识别教师链接：2-4 个汉字的锚文本，且 href 指向详情页。"""
    detail_hint = ("content.jsp", "/info/", "teacher", "faculty", "/js/", "/szdw/", "xyjl")
    return [
        FacultyLink(name=link["text"], href=link["href"])
        for link in links
        if re.fullmatch(r"[一-龥·]{2,4}", link["text"])
        and any(hint in link["href"] for hint in detail_hint)
    ]


def extract_faculty_list(
    html: str, base_url: str, llm, *, university: str, college: str
) -> list[FacultyLink]:
    links = extract_links(html, base_url)
    heuristic = _heuristic_faculty_links(links)
    payload = {
        "university": university,
        "college": college,
        "links": [{"text": link["text"], "href": link["href"]} for link in links[:300]],
    }
    try:
        output = llm.generate(
            schema=FacultyListOutput, instructions=_LIST_INSTRUCTIONS, payload=payload
        )
    except Exception:  # noqa: BLE001 - 无 LLM 时用启发式
        return heuristic
    if not isinstance(output, FacultyListOutput) or not output.faculty:
        return heuristic
    return output.faculty


def parse_faculty_page(
    text: str, llm, *, name: str, university: str, college: str, homepage_url: str
) -> FacultyRecord:
    try:
        output = llm.generate(
            schema=FacultyPageOutput, instructions=_PAGE_INSTRUCTIONS, payload={"text": text[:6000]}
        )
    except Exception:  # noqa: BLE001 - 无 LLM 时规则兜底
        output = FacultyPageOutput()
    if not isinstance(output, FacultyPageOutput):
        output = FacultyPageOutput()

    emails = _EMAIL_RE.findall(text)
    if emails and not output.email:
        output.email = emails[0]

    return FacultyRecord(
        id=_faculty_id(university, college, name, homepage_url),
        name=name,
        university=university,
        college=college,
        title=output.title,
        homepage_url=homepage_url,
        email=output.email,
        research_areas=output.research_areas,
        research_directions=output.research_directions,
        publications=output.publications,
        profile_text=text[:4000],
        source_url=homepage_url,
        retrieved_at=datetime.now(UTC).isoformat(),
    )


def crawl_college(
    llm,
    list_url: str,
    *,
    university: str,
    college: str,
    limit: int = 200,
    client: httpx.Client | None = None,
) -> list[FacultyRecord]:
    """爬取一个学院的师资列表：先抓列表页识别教师链接，再逐个抓个人主页抽取档案。"""
    list_html = fetch_html(list_url, client=client)
    faculty_links = extract_faculty_list(
        list_html, list_url, llm, university=university, college=college
    )
    records: list[FacultyRecord] = []
    for link in faculty_links[:limit]:
        try:
            page_html = fetch_html(link.href, client=client)
            text = html_to_text(page_html)
            records.append(
                parse_faculty_page(
                    text, llm, name=link.name, university=university,
                    college=college, homepage_url=link.href,
                )
            )
        except Exception:  # noqa: BLE001 - 单个教师页失败不阻断整批
            continue
    return records
