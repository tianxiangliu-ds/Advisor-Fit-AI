"""Crossref Provider：全学科 DOI 元数据（免费、无需 Key）。

定位是**补全库**：主要用来按标题/作者补齐 DOI、年份、期刊与作者名单，
多数记录没有摘要（这一点会如实反映在候选里，不编造）。
"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work

_BASE_URL = "https://api.crossref.org/works"
_USER_AGENT = "AdvisorFitAI/0.3 (mailto:user@example.com)"
SOURCE_LABEL = "Crossref"

_SELECT_FIELDS = (
    "DOI,title,author,issued,abstract,container-title,"
    "published-print,published-online,is-referenced-by-count"
)


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


def _parse_year(item: dict) -> int | None:
    for key in ("published-print", "published-online", "published", "issued"):
        date_parts = (item.get(key) or {}).get("date-parts") or [[None]]
        if date_parts and date_parts[0]:
            try:
                year = int(date_parts[0][0])
            except (TypeError, ValueError):
                continue
            if year:
                return year
    return None


def _parse_authors(item: dict) -> list[str]:
    authors: list[str] = []
    for author in item.get("author", []) or []:
        if not isinstance(author, dict):
            continue
        name = _clean(f"{author.get('given', '')} {author.get('family', '')}") or _clean(
            author.get("name")
        )
        if name:
            authors.append(name)
    return authors


class CrossrefProvider:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": _USER_AGENT})

    def _items(self, params: dict) -> list[dict]:
        resp = self.client.get(
            _BASE_URL, params={**params, "select": params.get("select", _SELECT_FIELDS)}
        )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError:
            return []
        return ((payload.get("message") or {}).get("items")) or []

    def _to_works(self, items: list[dict]) -> list[Work]:
        works: list[Work] = []
        for item in items:
            doi = _clean(item.get("DOI"))
            title = _clean((item.get("title") or [""])[0])
            if not title:
                continue
            works.append(
                Work(
                    id=doi or title,
                    title=title,
                    year=_parse_year(item),
                    doi=doi or None,
                    venue=_clean((item.get("container-title") or [""])[0]) or None,
                    abstract=_clean(item.get("abstract")),
                    citation_count=item.get("is-referenced-by-count"),
                    source_url=f"https://doi.org/{doi}" if doi else None,
                    source_platform=SOURCE_LABEL,
                    authors=_parse_authors(item),
                )
            )
        return works

    def search_publications(
        self,
        name: str,
        *,
        institution: str | None = None,
        source: str = "en",
        limit: int = 20,
    ) -> list[Work]:
        params: dict = {"query.author": name, "rows": str(max(1, min(limit, 100)))}
        if institution:
            params["query.affiliation"] = institution
        return self._to_works(self._items(params))

    def search_by_title(self, title: str, *, source: str = "en", limit: int = 5) -> list[Work]:
        return self._to_works(
            self._items({"query.title": title, "rows": str(max(1, min(limit, 100)))})
        )
