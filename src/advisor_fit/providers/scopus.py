"""Scopus Provider：可选的国际作者与论文索引。

Scopus 不是默认依赖：只有配置 API Key 且账号具备对应访问权限时才由路由器启用。
先检索作者档案、再用 Scopus Author ID 查论文，避免把同名作者的结果直接混入候选池。
"""

from __future__ import annotations

from typing import Any

import httpx

from advisor_fit.providers.academic import Work

_AUTHOR_URL = "https://api.elsevier.com/content/search/author"
_SCOPUS_URL = "https://api.elsevier.com/content/search/scopus"
SOURCE_LABEL = "Scopus"


class ScopusUnavailable(Exception):
    """Scopus Key、访问权限或响应格式不可用。"""


def _entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries = payload.get("search-results", {}).get("entry", [])
    return entries if isinstance(entries, list) else []


def _author_id(value: str) -> str:
    return str(value or "").removeprefix("AUTHOR_ID:").strip()


def _year(value: str) -> int | None:
    head = str(value or "")[:4]
    return int(head) if head.isdigit() else None


class ScopusProvider:
    """将 Scopus JSON 映射为统一的 Work，供路由器合并与人工核验。"""

    def __init__(
        self, api_key: str, client: httpx.Client | None = None, timeout: float = 20.0
    ) -> None:
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=timeout)
        self._last_author: dict[str, str] | None = None

    @property
    def last_author_note(self) -> str:
        if not self._last_author:
            return "未匹配到与目标机构一致的 Scopus 作者档案"
        return (
            f"Scopus 作者档案：{self._last_author['name']}"
            f"（{self._last_author['institution'] or '机构未标注'}）"
        )

    def _get(self, url: str, *, params: dict[str, str]) -> dict[str, Any]:
        response = self.client.get(
            url,
            params=params,
            headers={"Accept": "application/json", "X-ELS-APIKey": self.api_key},
        )
        if response.status_code in (401, 403):
            raise ScopusUnavailable("Scopus 访问权限不足或 API Key 无效")
        if response.status_code == 429:
            raise ScopusUnavailable("Scopus 请求过于频繁，请稍后重试")
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower() and response.text.lstrip().startswith("<"):
            raise ScopusUnavailable("Scopus 返回的不是 JSON")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ScopusUnavailable("Scopus 返回的不是 JSON") from exc
        if not isinstance(payload, dict):
            raise ScopusUnavailable("Scopus 响应格式异常")
        return payload

    @staticmethod
    def _display_name(entry: dict[str, Any]) -> str:
        name = entry.get("preferred-name") or {}
        if not isinstance(name, dict):
            return ""
        return " ".join(
            part for part in (name.get("given-name"), name.get("surname")) if part
        ).strip()

    @staticmethod
    def _institution(entry: dict[str, Any]) -> str:
        affiliation = entry.get("affiliation-current") or {}
        if isinstance(affiliation, dict):
            return str(affiliation.get("affiliation-name") or "")
        if isinstance(affiliation, list) and affiliation:
            return str((affiliation[0] or {}).get("affiliation-name") or "")
        return ""

    def _find_author(
        self, query_name: str, institution: str | None
    ) -> dict[str, str] | None:
        payload = self._get(
            _AUTHOR_URL,
            params={"query": f"authname({query_name})", "count": "10"},
        )
        candidates: list[dict[str, str]] = []
        for entry in _entries(payload):
            author_id = _author_id(str(entry.get("dc:identifier") or ""))
            if not author_id:
                continue
            candidates.append(
                {
                    "id": author_id,
                    "name": self._display_name(entry) or query_name,
                    "institution": self._institution(entry),
                }
            )
        if not candidates:
            return None
        if institution:
            needle = institution.casefold()
            candidates = [
                candidate
                for candidate in candidates
                if needle in candidate["institution"].casefold()
                or candidate["institution"].casefold() in needle
            ]
        return candidates[0] if candidates else None

    @staticmethod
    def _map_work(entry: dict[str, Any]) -> Work | None:
        title = str(entry.get("dc:title") or "").strip()
        if not title:
            return None
        doi = str(entry.get("prism:doi") or "").strip() or None
        eid = str(entry.get("eid") or "").strip()
        source_url = str(entry.get("prism:url") or "").strip() or None
        if doi:
            source_url = f"https://doi.org/{doi}"
        citation = str(entry.get("citedby-count") or "")
        date = str(
            entry.get("prism:coverDate") or entry.get("prism:coverDisplayDate") or ""
        )
        return Work(
            id=eid or doi or title,
            title=title,
            year=_year(date),
            doi=doi,
            venue=str(entry.get("prism:publicationName") or "") or None,
            source_url=source_url,
            source_platform=SOURCE_LABEL,
            authors=[str(entry.get("dc:creator"))] if entry.get("dc:creator") else [],
            institution=str(entry.get("affilname") or ""),
            citation_count=int(citation) if citation.isdigit() else None,
        )

    def search_publications(
        self,
        name: str,
        *,
        institution: str | None = None,
        english_name: str | None = None,
        limit: int = 20,
    ) -> list[Work]:
        query_name = (english_name or name).strip()
        author = self._find_author(query_name, institution)
        self._last_author = author
        if author is None:
            return []
        payload = self._get(
            _SCOPUS_URL,
            params={
                "query": f"AU-ID({author['id']})",
                "count": str(max(1, min(limit, 25))),
                "sort": "-coverDate",
            },
        )
        return [work for entry in _entries(payload) if (work := self._map_work(entry))]

    def search_by_title(self, title: str, *, limit: int = 5) -> list[Work]:
        payload = self._get(
            _SCOPUS_URL,
            params={
                "query": f"TITLE({title})",
                "count": str(max(1, min(limit, 25))),
                "sort": "-coverDate",
            },
        )
        return [work for entry in _entries(payload) if (work := self._map_work(entry))]
