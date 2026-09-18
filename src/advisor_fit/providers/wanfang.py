"""万方学术文献检索 Provider（官方开放 API，返回候选，绝不自动确认）。

万方数据 openwanfang API：POST https://api.wfdata.com/openwanfang/getQuery
需要请求头 X-Ca-AppKey（在 .env 配置 WANFANG_APP_KEY）。
"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work

_BASE_URL = "https://api.wfdata.com/openwanfang/getQuery"


class WanfangUnavailable(Exception):
    """万方不可用；调用方应降级而不是伪造空成功。"""


def _first_str(value) -> str:
    if not value:
        return ""
    if "stringValue" in value:
        return value["stringValue"]
    if "numberValue" in value:
        return str(int(value["numberValue"]))
    if "listValue" in value:
        for item in value["listValue"].get("values", []):
            result = _first_str(item)
            if result:
                return result
    return ""


def _all_str(value) -> list[str]:
    if not value:
        return []
    if "stringValue" in value:
        return [value["stringValue"]]
    if "numberValue" in value:
        return [str(int(value["numberValue"]))]
    if "listValue" in value:
        result: list[str] = []
        for item in value["listValue"].get("values", []):
            result.extend(_all_str(item))
        return result
    return []


class WanfangProvider:
    def __init__(
        self, app_key: str, client: httpx.Client | None = None, timeout: float = 20.0
    ) -> None:
        self.app_key = app_key
        self.client = client or httpx.Client(timeout=timeout)

    def search_publications(
        self, name: str, *, collections: list[str] | None = None, limit: int = 20
    ) -> list[Work]:
        collections = collections or ["OpenPeriodical", "OpenConference"]
        body = {
            "collections": collections,
            "query": f"Creator:{name}",
            "returned_fields": [
                "Title",
                "Creator",
                "PublishYear",
                "Abstract",
                "Keywords",
                "DOI",
                "Id",
                "PeriodicalTitle",
            ],
            "rows": limit,
        }
        resp = self.client.post(
            _BASE_URL,
            headers={"Content-Type": "application/json", "X-Ca-AppKey": self.app_key},
            json=body,
        )
        resp.raise_for_status()
        documents = resp.json().get("documents", []) or []
        works: list[Work] = []
        for doc in documents:
            fields = doc.get("fields", {})
            title = _first_str(fields.get("Title"))
            if not title:
                continue
            year_str = _first_str(fields.get("PublishYear"))
            year = int(year_str) if year_str.isdigit() else None
            doi = _first_str(fields.get("DOI"))
            doc_id = _first_str(fields.get("Id"))
            source_url = (
                f"https://doi.org/{doi}"
                if doi
                else (f"https://d.wanfangdata.com.cn/periodical/{doc_id}" if doc_id else None)
            )
            works.append(
                Work(
                    id=doc_id or title,
                    title=title,
                    year=year,
                    doi=doi or None,
                    abstract=_first_str(fields.get("Abstract")),
                    source_url=source_url,
                    source_platform="万方",
                    topics=_all_str(fields.get("Keywords")),
                )
            )
        return works
