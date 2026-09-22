"""Springer Nature Provider：出版社范围内的题名元数据补充。"""

from __future__ import annotations

from typing import Any

import httpx

from advisor_fit.providers.academic import Work

_META_URL = "https://api.springernature.com/meta/v2/json"
_OPEN_ACCESS_URL = "https://api.springernature.com/openaccess/json"
SOURCE_LABEL = "Springer Nature"


class SpringerUnavailable(Exception):
    """Springer Nature 的权限、限额或响应不可用。"""


class SpringerProvider:
    def __init__(
        self,
        meta_api_key: str = "",
        open_access_api_key: str = "",
        client: httpx.Client | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = meta_api_key or open_access_api_key
        self.endpoint = _META_URL if meta_api_key else _OPEN_ACCESS_URL
        self.client = client or httpx.Client(timeout=timeout)

    def _get(self, title: str, limit: int) -> dict[str, Any]:
        response = self.client.get(
            self.endpoint,
            params={
                "q": f'title:"{title}"',
                "p": str(max(1, min(limit, 20))),
                "api_key": self.api_key,
            },
        )
        if response.status_code in (401, 403):
            raise SpringerUnavailable("Springer Nature 访问权限不足或 API Key 无效")
        if response.status_code == 429:
            raise SpringerUnavailable("Springer Nature 请求过于频繁，请稍后重试")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise SpringerUnavailable("Springer Nature 返回的不是 JSON") from exc
        if not isinstance(payload, dict):
            raise SpringerUnavailable("Springer Nature 响应格式异常")
        return payload

    @staticmethod
    def _work(record: dict[str, Any]) -> Work | None:
        title = str(record.get("title") or "").strip()
        if not title:
            return None
        doi = str(record.get("doi") or "").strip() or None
        date = str(record.get("publicationDate") or "")
        creators = record.get("creators") or []
        authors = [str(item.get("creator")) for item in creators if item.get("creator")]
        urls = record.get("url") or []
        source_url = next((str(item.get("value")) for item in urls if item.get("value")), None)
        return Work(
            id=doi or title,
            title=title,
            year=int(date[:4]) if date[:4].isdigit() else None,
            doi=doi,
            venue=str(record.get("publicationName") or "") or None,
            source_url=f"https://doi.org/{doi}" if doi else source_url,
            source_platform=SOURCE_LABEL,
            authors=authors,
        )

    def search_by_title(self, title: str, *, limit: int = 5) -> list[Work]:
        records = self._get(title, limit).get("records", [])
        return [
            work
            for record in records
            if isinstance(record, dict) and (work := self._work(record))
        ]
