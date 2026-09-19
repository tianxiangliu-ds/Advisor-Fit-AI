"""Crossref 学术检索 Provider（按标题检索，万方查不到作者时的兜底）。

Crossref 免费、无需 Key、国内可访问；多数文献有 DOI/作者/年份，部分有摘要。
"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work

_BASE_URL = "https://api.crossref.org/works"


class CrossrefProvider:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self.client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": "AdvisorFitAI/0.3 (mailto:user@example.com)"}
        )

    def search_by_title(self, title: str, *, limit: int = 5) -> list[Work]:
        resp = self.client.get(
            _BASE_URL,
            params={"query.title": title, "rows": str(limit)},
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
        works: list[Work] = []
        for item in items:
            doi = item.get("DOI", "")
            title_text = (item.get("title") or [""])[0]
            if not title_text:
                continue
            year = None
            for key in ("published-print", "published", "published-online"):
                date_parts = item.get(key, {}).get("date-parts", [[None]])
                if date_parts and date_parts[0]:
                    try:
                        year = int(date_parts[0][0])
                    except (TypeError, ValueError):
                        year = None
                    if year:
                        break
            authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in item.get("author", [])
                if a.get("family")
            ]
            works.append(
                Work(
                    id=doi or title_text,
                    title=title_text,
                    year=year,
                    doi=doi or None,
                    abstract=item.get("abstract") or "",
                    source_url=f"https://doi.org/{doi}" if doi else None,
                    source_platform="Crossref",
                    authors=authors,
                    institution="",
                )
            )
        return works
