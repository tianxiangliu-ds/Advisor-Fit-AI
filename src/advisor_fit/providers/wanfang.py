"""万方学术文献检索 Provider（官方开放 API，返回候选，绝不自动确认）。

万方数据 openwanfang API：POST https://api.wfdata.com/openwanfang/getQuery
需要请求头 X-Ca-AppKey（在 .env 配置 WANFANG_APP_KEY）。
"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work

_BASE_URL = "https://api.wfdata.com/openwanfang/getQuery"

# 实测可用的 collection 预设（source -> collections）。
# 中文库按中文名检索；英文库 OpenPeriodicalEng 只收英文记录，必须用英文名。
COLLECTION_PRESETS: dict[str, list[str]] = {
    "zh": ["OpenPeriodical", "OpenConference"],
    "en": ["OpenPeriodicalEng"],
}

SOURCE_LABELS: dict[str, str] = {
    "zh": "万方 · 中文（期刊 + 会议）",
    "en": "万方 · 英文（英文期刊）",
}


class WanfangUnavailable(Exception):
    """万方不可用；调用方应降级而不是伪造空成功。"""


def group_english_authors(tokens: list[str]) -> list[str]:
    """英文库的 Creator 把姓与缩写拆成扁平 token，按「姓 + 连续缩写」重组回作者。

    例：["Fan","A.","Y.","Zhang","Q."] -> ["Fan A. Y.", "Zhang Q."]
    """
    grouped: list[str] = []
    for token in tokens:
        if grouped and token.endswith("."):
            grouped[-1] = f"{grouped[-1]} {token}"
        else:
            grouped.append(token)
    return grouped


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

    def _post(self, collections: list[str], query: str, limit: int) -> list[dict]:
        body = {
            "collections": collections,
            "query": query,
            "returned_fields": [
                "Title",
                "Creator",
                "PublishYear",
                "Abstract",
                "Keywords",
                "DOI",
                "Id",
                "PeriodicalTitle",
                "OrganizationNorm",
            ],
            "rows": limit,
            "sort": {"sorts": [{"by": "PublishYear", "order": "DESC"}]},
        }
        resp = self.client.post(
            _BASE_URL,
            headers={"Content-Type": "application/json", "X-Ca-AppKey": self.app_key},
            json=body,
        )
        resp.raise_for_status()
        return resp.json().get("documents", []) or []

    def _parse_works(self, documents: list[dict], source: str) -> list[Work]:
        works: list[Work] = []
        seen_titles: set[str] = set()
        for doc in documents:
            fields = doc.get("fields", {})
            title = _first_str(fields.get("Title"))
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            year_str = _first_str(fields.get("PublishYear"))
            year = int(year_str) if year_str.isdigit() else None
            doi = _first_str(fields.get("DOI"))
            doc_id = _first_str(fields.get("Id"))
            source_url = (
                f"https://doi.org/{doi}"
                if doi
                else (f"https://d.wanfangdata.com.cn/periodical/{doc_id}" if doc_id else None)
            )
            authors = _all_str(fields.get("Creator"))
            if source == "en":
                authors = group_english_authors(authors)
            works.append(
                Work(
                    id=doc_id or title,
                    title=title,
                    year=year,
                    doi=doi or None,
                    abstract=_first_str(fields.get("Abstract")),
                    venue=_first_str(fields.get("PeriodicalTitle")) or None,
                    source_url=source_url,
                    source_platform="万方",
                    topics=_all_str(fields.get("Keywords")),
                    authors=authors,
                    institution=_first_str(fields.get("OrganizationNorm")),
                )
            )
        return works

    def search_publications(
        self,
        name: str,
        *,
        institution: str | None = None,
        source: str = "zh",
        limit: int = 20,
    ) -> list[Work]:
        collections = COLLECTION_PRESETS.get(source) or COLLECTION_PRESETS["zh"]
        query = f"Creator:{name}"
        if institution:
            query = f"Creator:{name} AND OrganizationForSearch:{institution}"
        works = self._parse_works(self._post(collections, query, limit), source)
        if institution:
            works = self._filter_by_institution(works, institution)
        return works

    @staticmethod
    def _filter_by_institution(works: list[Work], institution: str) -> list[Work]:
        """客户端兜底过滤：万方的 OrganizationForSearch 不可靠，按 OrganizationNorm 再筛一次。

        保留机构为空（无法判断）或与目标机构互含的论文，剔除明显不符的同名论文。
        """
        kept: list[Work] = []
        for work in works:
            winst = (work.institution or "").strip()
            if not winst or institution in winst or winst in institution:
                kept.append(work)
        return kept

    def search_by_title(
        self, title: str, *, source: str = "zh", limit: int = 5
    ) -> list[Work]:
        """按论文标题检索（作者名查不到时的兜底）。"""
        collections = COLLECTION_PRESETS.get(source) or COLLECTION_PRESETS["zh"]
        query = f'Title:"{title}"'
        return self._parse_works(self._post(collections, query, limit), source)
