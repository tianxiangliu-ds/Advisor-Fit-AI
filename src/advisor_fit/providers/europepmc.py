"""Europe PMC Provider：生物医学与生命科学文献（免费、无需申请 Key）。

为什么用 Europe PMC 而不是直接调 PubMed：PubMed 的 E-utilities 要先 esearch 再 efetch
（每次检索两个来回），而 Europe PMC 一次请求就返回标题、摘要、作者、机构、DOI、年份，
对"每次研究不超过 25 次网络请求"的预算闸门更友好，且同样覆盖 PubMed 记录与预印本。
"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work

_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_USER_AGENT = "AdvisorFitAI/0.2 (academic search; contact via project README)"

SOURCE_LABEL = "Europe PMC"


class EuropePmcUnavailable(Exception):
    """Europe PMC 不可用；调用方应降级而不是伪造空成功。"""


def _is_ascii(text: str) -> bool:
    return bool(text) and all(ord(char) < 128 for char in text)


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


def _first_affiliation(item: dict) -> str:
    for author in (item.get("authorList") or {}).get("author", []) or []:
        affiliation = _clean(author.get("affiliation"))
        if affiliation:
            return affiliation
    return ""


class EuropePmcProvider:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self.client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )

    def _search(self, query: str, limit: int) -> list[dict]:
        resp = self.client.get(
            _BASE_URL,
            params={
                "query": query,
                "format": "json",
                "pageSize": str(max(1, min(limit, 100))),
                "resultType": "core",
                "sort": "P_PDATE_D desc",
            },
        )
        resp.raise_for_status()
        results = (resp.json().get("resultList") or {}).get("result") or []
        return [item for item in results if isinstance(item, dict)]

    def _to_works(self, items: list[dict]) -> list[Work]:
        works: list[Work] = []
        for item in items:
            title = _clean(item.get("title"))
            if not title:
                continue
            doi = _clean(item.get("doi")) or None
            pmid = _clean(item.get("pmid")) or _clean(item.get("id"))
            journal = ((item.get("journalInfo") or {}).get("journal") or {}).get("title")
            year_text = str(item.get("pubYear") or "")
            authors = [
                _clean(author.get("fullName") or author.get("lastName"))
                for author in (item.get("authorList") or {}).get("author", []) or []
            ]
            works.append(
                Work(
                    id=doi or pmid or title,
                    title=title,
                    year=int(year_text) if year_text.isdigit() else None,
                    doi=doi,
                    venue=_clean(journal) or None,
                    abstract=_clean(item.get("abstractText")),
                    citation_count=item.get("citedByCount"),
                    source_url=(
                        f"https://doi.org/{doi}"
                        if doi
                        else (
                            f"https://europepmc.org/article/{item.get('source', 'MED')}/{pmid}"
                            if pmid
                            else None
                        )
                    ),
                    source_platform=SOURCE_LABEL,
                    authors=[name for name in authors if name],
                    institution=_first_affiliation(item),
                    disciplines=["medicine"],
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
        """按作者名（必要时叠加机构）检索生物医学文献。"""
        query = f'AUTH:"{name}"'
        # 中文机构名与库里的英文机构名对不上，加进去只会把结果筛成 0，所以只在纯英文时叠加
        if institution and _is_ascii(institution):
            query += f' AND AFF:"{institution}"'
        return self._to_works(self._search(query, limit))

    def search_by_title(self, title: str, *, source: str = "en", limit: int = 5) -> list[Work]:
        return self._to_works(self._search(f'TITLE:"{title}"', limit))


__all__ = ["EuropePmcProvider", "EuropePmcUnavailable", "SOURCE_LABEL"]
