"""OpenAlex 学术检索 Provider（可选工具：返回候选，绝不自动确认）。"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work

_BASE_URL = "https://api.openalex.org"
_USER_AGENT = "AdvisorFitAI/0.2"


class OpenAlexUnavailable(Exception):
    """OpenAlex 不可用；调用方应降级而不是伪造空成功。"""


def _short_id(full_id: str) -> str:
    return full_id.rstrip("/").split("/")[-1]


def _reconstruct_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, indices in inverted.items():
        for index in indices:
            positions[index] = word
    return " ".join(positions[index] for index in sorted(positions))


class OpenAlexProvider:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 15.0) -> None:
        self.client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )

    def search_authors(self, name: str, institution: str | None = None) -> list[dict]:
        query = f"{name} {institution}".strip() if institution else name
        resp = self.client.get(
            f"{_BASE_URL}/authors", params={"search": query, "per-page": "5"}
        )
        resp.raise_for_status()
        return [
            {
                "id": _short_id(author.get("id", "")),
                "name": author.get("display_name", ""),
                "works_count": author.get("works_count"),
            }
            for author in resp.json().get("results", [])
        ]

    def list_works(self, author_id: str, since_year: int | None = None) -> list[Work]:
        filters = [f"author.id:{author_id}"]
        if since_year:
            filters.append(f"from_publication_date:{since_year}-01-01")
        resp = self.client.get(
            f"{_BASE_URL}/works", params={"filter": ",".join(filters), "per-page": "25"}
        )
        resp.raise_for_status()
        works: list[Work] = []
        for item in resp.json().get("results", []):
            wid = _short_id(item.get("id", ""))
            doi = (item.get("doi") or "").replace("https://doi.org/", "")
            source_url = (
                f"https://doi.org/{doi}"
                if doi
                else (f"https://openalex.org/{wid}" if wid else None)
            )
            works.append(
                Work(
                    id=wid,
                    title=item.get("title") or "",
                    year=item.get("publication_year"),
                    doi=doi or None,
                    abstract=_reconstruct_abstract(item.get("abstract_inverted_index")),
                    source_url=source_url,
                    source_platform="OpenAlex",
                )
            )
        return works


def search_candidate_works(
    provider: OpenAlexProvider, name: str, institution: str | None = None
) -> list[Work]:
    """检索候选作者并返回其论文；结果一律待用户确认。"""
    authors = provider.search_authors(name, institution)
    if not authors:
        return []
    return provider.list_works(authors[0]["id"])
