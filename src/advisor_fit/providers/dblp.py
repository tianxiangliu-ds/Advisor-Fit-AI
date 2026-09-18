"""DBLP 学术检索 Provider（可选工具：返回候选，绝不自动确认）。

DBLP 主要收录计算机领域的论文，支持中文姓名检索，公开 JSON API、无需 Key。
"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work

_BASE_URL = "https://dblp.org"
_USER_AGENT = "AdvisorFitAI/0.3"


class DblpUnavailable(Exception):
    """DBLP 不可用；调用方应降级而不是伪造空成功。"""


class DblpProvider:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 15.0) -> None:
        self.client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )

    def search_publications(self, name: str, limit: int = 20) -> list[Work]:
        resp = self.client.get(
            f"{_BASE_URL}/search/publ/api",
            params={"q": name, "format": "json", "h": str(limit)},
        )
        resp.raise_for_status()
        hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
        if hits is None:
            return []
        if isinstance(hits, dict):  # 单条命中时 DBLP 返回 dict 而非 list
            hits = [hits]

        works: list[Work] = []
        for hit in hits:
            info = hit.get("info", {})
            title = info.get("title") or ""
            if not title:
                continue
            year_text = info.get("year") or ""
            year = int(year_text) if year_text.isdigit() else None
            doi = info.get("doi")
            source_url = (
                f"https://doi.org/{doi}" if doi else (info.get("ee") or info.get("url"))
            )
            works.append(
                Work(
                    id=info.get("key") or title,
                    title=title,
                    year=year,
                    doi=doi,
                    abstract="",  # DBLP 不提供摘要
                    source_url=source_url,
                    source_platform="DBLP",
                )
            )
        return works
