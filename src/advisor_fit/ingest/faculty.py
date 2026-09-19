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


_LIST_INSTRUCTIONS = (
    "从高校学院「师资/教师/导师」列表页的链接中，找出每位教师的姓名与其个人主页链接。"
    "忽略导航栏、新闻、招生、学生、行政等非教师链接。"
    "name 填教师中文姓名（2-4 个汉字），href 填完整链接。"
)

_PAGE_INSTRUCTIONS = (
    "从高校教师个人主页文本中抽取结构化信息。"
    "title=职称（教授/副教授/讲师等）；email=邮箱；"
    "research_areas=研究领域（大类，列表）；research_directions=研究方向（具体，列表）；"
    "publications=代表论文/著作标题（列表）。"
    "只抽取文本中明确写出的内容，找不到留空，不得编造。"
)


def fetch_html(url: str, *, client: httpx.Client | None = None, timeout: float = 20.0) -> str:
    client = client or httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
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


def extract_faculty_list(
    html: str, base_url: str, llm, *, university: str, college: str
) -> list[FacultyLink]:
    links = extract_links(html, base_url)
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
        return [
            FacultyLink(name=link["text"], href=link["href"])
            for link in links
            if re.fullmatch(r"[一-龥]{2,4}", link["text"])
        ]
    if not isinstance(output, FacultyListOutput):
        return []
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
