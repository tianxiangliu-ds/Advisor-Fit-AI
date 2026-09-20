"""DBLP 学术检索 Provider（计算机领域权威目录，公开 JSON API、无需 Key）。

⚠️ 现状（实测）：DBLP 已启用 Anubis 反爬校验，普通程序请求会被返回一个 HTML 挑战页
（HTTP 200 但不是 JSON），所有入口（dblp.org / dblp.uni-trier.de）表现一致。
因此本 Provider 在源登记表里**默认关闭**；计算机领域的召回由 OpenAlex 承担。
如果 DBLP 之后取消该校验，把 `config/sources.json` 里的 `enabled` 改成 true 即可启用，
不用改代码。
"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work

_BASE_URL = "https://dblp.org"
_USER_AGENT = "AdvisorFitAI/0.3"
SOURCE_LABEL = "DBLP"


class DblpUnavailable(Exception):
    """DBLP 不可用；调用方应降级而不是伪造空成功。"""


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


def _parse_authors(info: dict) -> list[str]:
    raw = (info.get("authors") or {}).get("author") or []
    if isinstance(raw, dict):
        raw = [raw]
    authors: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = _clean(item.get("text"))
        else:
            name = _clean(item)
        if name:
            authors.append(name)
    return authors


class DblpProvider:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 15.0) -> None:
        self.client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )

    def _search(self, query: str, limit: int) -> list[Work]:
        resp = self.client.get(
            f"{_BASE_URL}/search/publ/api",
            params={"q": query, "format": "json", "h": str(max(1, min(limit, 100)))},
        )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            # DBLP 的反爬挑战页就是这种情况：HTTP 200 + HTML
            raise DblpUnavailable(
                "DBLP 返回的不是 JSON（站点启用了浏览器校验/反爬），本次跳过该来源"
            ) from exc
        hits = ((payload.get("result") or {}).get("hits") or {}).get("hit") or []
        if isinstance(hits, dict):  # 单条命中时 DBLP 返回 dict 而非 list
            hits = [hits]

        works: list[Work] = []
        for hit in hits:
            info = hit.get("info", {})
            title = _clean(info.get("title"))
            if not title:
                continue
            year_text = str(info.get("year") or "")
            doi = info.get("doi")
            works.append(
                Work(
                    id=info.get("key") or title,
                    title=title,
                    year=int(year_text) if year_text.isdigit() else None,
                    doi=doi,
                    venue=_clean(info.get("venue")) or None,
                    abstract="",  # DBLP 不提供摘要
                    source_url=(
                        f"https://doi.org/{doi}" if doi else (info.get("ee") or info.get("url"))
                    ),
                    source_platform=SOURCE_LABEL,
                    authors=_parse_authors(info),
                    disciplines=["cs"],
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
        """按姓名检索（DBLP 只收计算机领域，不支持按机构过滤）。"""
        return self._search(name, limit)

    def search_by_title(self, title: str, *, source: str = "en", limit: int = 5) -> list[Work]:
        return self._search(title, limit)
