"""arXiv Provider：物理 / 数学 / 计算机 / 统计等预印本（免费、无需申请 Key）。

注意事项：
- arXiv 官方要求请求间隔 **至少 3 秒**，这个限速写在源登记表（`sources.py`）里，
  由检索路由器统一sleep，Provider 自己不睡（方便测试）。
- 返回的是 Atom XML，不是 JSON，这里用标准库解析。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from advisor_fit.providers.academic import Work
from advisor_fit.providers.disciplines import discipline_from_arxiv_category

_BASE_URL = "https://export.arxiv.org/api/query"
_USER_AGENT = "AdvisorFitAI/0.2 (academic search; contact via project README)"

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"

SOURCE_LABEL = "arXiv"


class ArxivUnavailable(Exception):
    """arXiv 不可用；调用方应降级而不是伪造空成功。"""


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


class ArxivProvider:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self.client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )

    def _query(self, search_query: str, limit: int) -> list[Work]:
        resp = self.client.get(
            _BASE_URL,
            params={
                "search_query": search_query,
                "start": "0",
                "max_results": str(max(1, min(limit, 100))),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
        )
        resp.raise_for_status()
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:  # 上游返回非 XML（限流页/错误页）时降级为空
            raise ArxivUnavailable(str(exc)) from exc
        return [
            work
            for work in (self._to_work(entry) for entry in root.findall(f"{_ATOM}entry"))
            if work
        ]

    def _to_work(self, entry: ET.Element) -> Work | None:
        title = _clean(entry.findtext(f"{_ATOM}title"))
        if not title:
            return None
        published = _clean(entry.findtext(f"{_ATOM}published"))
        year = int(published[:4]) if published[:4].isdigit() else None
        url = _clean(entry.findtext(f"{_ATOM}id")) or None
        doi = _clean(entry.findtext(f"{_ARXIV}doi")) or None
        authors = [
            _clean(node.findtext(f"{_ATOM}name"))
            for node in entry.findall(f"{_ATOM}author")
        ]
        category = None
        primary = entry.find(f"{_ARXIV}primary_category")
        if primary is not None:
            category = primary.get("term")
        discipline = discipline_from_arxiv_category(category)
        return Work(
            id=url or title,
            title=title,
            year=year,
            doi=doi,
            venue=_clean(entry.findtext(f"{_ARXIV}journal_ref")) or "arXiv 预印本",
            abstract=_clean(entry.findtext(f"{_ATOM}summary")),
            source_url=f"https://doi.org/{doi}" if doi else url,
            source_platform=SOURCE_LABEL,
            authors=[name for name in authors if name],
            topics=[category] if category else [],
            disciplines=[discipline] if discipline else [],
        )

    def search_publications(
        self,
        name: str,
        *,
        institution: str | None = None,
        source: str = "en",
        limit: int = 20,
    ) -> list[Work]:
        """按作者名检索 arXiv 预印本（arXiv 不支持按机构过滤）。"""
        return self._query(f'au:"{name}"', limit)

    def search_by_title(self, title: str, *, source: str = "en", limit: int = 5) -> list[Work]:
        return self._query(f'ti:"{title}"', limit)


__all__ = ["ArxivProvider", "ArxivUnavailable", "SOURCE_LABEL"]
