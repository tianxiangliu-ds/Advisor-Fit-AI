"""从导师个人主页/学校官方页面提取结构化信息（姓名、学校、院系、职称、邮箱、方向）。

LLM 优先做结构化抽取；无 LLM 时用正则规则兜底（仅提取邮箱、页面标题猜测姓名）。
网页抓取是可选插件能力，失败不影响手动录入流程。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx
from pydantic import BaseModel

from advisor_fit.ingest.fetch import Fetcher
from advisor_fit.llm.prompts import prompt_text

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class HomepageProfile(BaseModel):
    name: str = ""
    institution: str = ""
    department: str = ""
    title: str = ""  # 职称，如「教授」
    email: str = ""
    declared_interests: list[str] = []
    publications: list[str] = []  # 代表论文标题（用于作者名查不到时兜底检索）


_HOMEPAGE_INSTRUCTIONS = prompt_text("homepage_extract")


class _TextExtractor(HTMLParser):
    """把 HTML 转成可读文本，丢弃 script/style，块级标签之间换行。"""

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "td", "th"):
            self.parts.append("\n")

    def handle_endtag(self, tag) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract_title(html: str) -> str:
    match = _TITLE_RE.search(html)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def fetch_homepage(url: str, *, client: httpx.Client | None = None, timeout: float = 20.0) -> str:
    """抓取主页 HTML；失败抛 httpx.HTTPError，由调用方降级。"""
    client = client or httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    resp = client.get(url)
    resp.raise_for_status()
    return resp.text


def _guess_name_from_title(title: str) -> str:
    """从 <title> 猜测姓名：取常见分隔符前的一段，如「张三 - 武汉大学」。"""
    for sep in ("-", "|", "_", "—", "–"):
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if 2 <= len(head) <= 6:
                return head
    return ""


def _rule_extract(text: str, title: str) -> HomepageProfile:
    """无 LLM 时的规则兜底：仅提取邮箱与页面标题猜测姓名。"""
    profile = HomepageProfile()
    emails = _EMAIL_RE.findall(text)
    if emails:
        profile.email = emails[0]
    if title:
        profile.name = _guess_name_from_title(title)
    return profile


def parse_homepage(text: str, llm, *, title: str = "") -> HomepageProfile:
    """从主页文本提取结构化信息；LLM 优先，无 LLM 时规则兜底。"""
    payload = {"text": text[:8000]}
    try:
        output = llm.generate(
            schema=HomepageProfile,
            instructions=_HOMEPAGE_INSTRUCTIONS,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - LLM 不可用降级为规则
        output = HomepageProfile()
    if not isinstance(output, HomepageProfile):
        output = HomepageProfile()

    emails = _EMAIL_RE.findall(text)
    if emails and not output.email:
        output.email = emails[0]
    if not output.name and title:
        output.name = _guess_name_from_title(title)
    return output


def parse_homepage_html(html: str, llm) -> HomepageProfile:
    """从一段 HTML 直接解析（含标题推断），供需要同时拿原始 HTML 的调用方使用。"""
    return parse_homepage(html_to_text(html), llm, title=extract_title(html))


def extract_homepage_profile(
    url: str, llm, *, fetcher: Fetcher | None = None
) -> HomepageProfile:
    """抓取主页并提取结构化信息（一步到位，供应用层调用）。

    走统一的抓取零件：先看 robots.txt 是否允许、按域名限速、失败自动重试。
    抓不到时抛出带中文说明的错误，由界面提示用户改为手动填写。
    """
    result = (fetcher or Fetcher()).fetch(url)
    if not result.ok:
        raise RuntimeError(result.friendly_error())
    return parse_homepage_html(result.text, llm)
