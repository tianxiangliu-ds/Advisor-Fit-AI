"""OpenAlex Provider：全学科免费主干库（无需申请 Key）。

为什么它是主干：
- 覆盖全学科约 2.5 亿条文献，自带**学科分类**（primary_topic.field）与**作者机构**，
  既能用来检索，也能用来判断"这位导师属于哪个学科"，从而决定还要补查哪些专业库；
- 免费、无需申请 Key，新用户装完即可用；
- 提供摘要（inverted index 形式，需要还原）。

礼貌使用：带上 `mailto` 参数进入 polite pool，限速由检索路由器统一控制。
"""

from __future__ import annotations

import httpx

from advisor_fit.providers.academic import Work
from advisor_fit.providers.affiliation import institution_matches
from advisor_fit.providers.author_match import author_matches
from advisor_fit.providers.disciplines import discipline_from_openalex_field

_BASE_URL = "https://api.openalex.org"
_USER_AGENT = "AdvisorFitAI/0.2 (academic search; contact via project README)"
SOURCE_LABEL = "OpenAlex"

# 只要期刊/会议论文，排除数据集、学位论文仓库快照等噪声（实测这些会排在最前面）
_SELECT_FIELDS = (
    "id,doi,title,publication_year,publication_date,abstract_inverted_index,"
    "authorships,primary_location,primary_topic,cited_by_count,type"
)


class OpenAlexUnavailable(Exception):
    """OpenAlex 不可用；调用方应降级而不是伪造空成功。"""


def _short_id(full_id: str) -> str:
    return (full_id or "").rstrip("/").split("/")[-1]


def _reconstruct_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, indices in inverted.items():
        for index in indices:
            positions[index] = word
    return " ".join(positions[index] for index in sorted(positions))


def _clean_doi(doi: str | None) -> str:
    if not doi:
        return ""
    return str(doi).replace("https://doi.org/", "").replace("http://doi.org/", "").strip()


def _is_json_response(resp: httpx.Response) -> bool:
    """OpenAlex 正常返回 JSON；被网关拦下时会返回 HTML，这里明确判掉。"""
    try:
        resp.json()
    except ValueError:
        return False
    return True


class OpenAlexProvider:
    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        mailto: str = "",
    ) -> None:
        self.client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )
        self.mailto = mailto
        # 最近一次检索选中的作者实体，用于向用户交代"这条线索是按谁的档案查的"
        self.last_author: dict | None = None

    def _params(self, params: dict) -> dict:
        if self.mailto:
            params = {**params, "mailto": self.mailto}
        return params

    # ---------- 作者检索（旧接口，保留兼容） ----------

    def search_authors(self, name: str, institution: str | None = None) -> list[dict]:
        query = f"{name} {institution}".strip() if institution else name
        resp = self.client.get(
            f"{_BASE_URL}/authors", params=self._params({"search": query, "per-page": "5"})
        )
        resp.raise_for_status()
        if not _is_json_response(resp):
            raise OpenAlexUnavailable("返回内容不是 JSON（可能被网关拦截）")
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
            f"{_BASE_URL}/works",
            params=self._params({"filter": ",".join(filters), "per-page": "25"}),
        )
        resp.raise_for_status()
        if not _is_json_response(resp):
            raise OpenAlexUnavailable("返回内容不是 JSON（可能被网关拦截）")
        return [self._to_work(item) for item in resp.json().get("results", [])]

    # ---------- 检索接口（供检索路由器调用） ----------

    def _to_work(self, item: dict) -> Work:
        wid = _short_id(item.get("id", ""))
        doi = _clean_doi(item.get("doi"))
        authorships = item.get("authorships") or []
        authors = [
            (entry.get("author") or {}).get("display_name", "")
            for entry in authorships
            if isinstance(entry, dict)
        ]
        institutions: list[str] = []
        for entry in authorships:
            if not isinstance(entry, dict):
                continue
            for inst in entry.get("institutions") or []:
                name = (inst or {}).get("display_name")
                if name and name not in institutions:
                    institutions.append(name)
        topic = item.get("primary_topic") or {}
        field_name = (topic.get("field") or {}).get("display_name")
        discipline = discipline_from_openalex_field(field_name)
        topics = [
            value
            for value in (
                topic.get("display_name"),
                (topic.get("subfield") or {}).get("display_name"),
                field_name,
            )
            if value
        ]
        venue = ((item.get("primary_location") or {}).get("source") or {}).get("display_name")
        return Work(
            id=wid or doi or (item.get("title") or ""),
            title=item.get("title") or "",
            year=item.get("publication_year"),
            doi=doi or None,
            venue=venue,
            topics=topics,
            citation_count=item.get("cited_by_count"),
            abstract=_reconstruct_abstract(item.get("abstract_inverted_index")),
            source_url=(
                f"https://doi.org/{doi}"
                if doi
                else (f"https://openalex.org/{wid}" if wid else None)
            ),
            source_platform=SOURCE_LABEL,
            authors=[name for name in authors if name],
            institution=" / ".join(institutions[:3]),
            disciplines=[discipline] if discipline else [],
        )

    def search_works_by_author(
        self,
        name: str,
        *,
        institution: str | None = None,
        english_name: str | None = None,
        limit: int = 25,
        since_year: int | None = None,
    ) -> list[Work]:
        """按姓名检索论文：先找"作者实体"，找不到再退回原始署名检索。

        为什么不直接按姓名搜论文：OpenAlex 的 `raw_author_name.search` 是**分词模糊匹配**，
        实测用「陆伟」会查出「陆鑫」「陆长峰」的论文。正确做法是先查 `/authors` 拿到
        作者实体（OpenAlex 已做过作者消歧），再用 `author.id` 取论文——精度高得多。
        只有查不到作者实体时（例如新入职、没被收录），才退回原始署名检索，
        并且**逐篇核对作者名单**，对不上的一律不要。
        """
        candidates = self.find_authors(name, extra_name=english_name)
        best = self.pick_author(candidates, name, institution, english_name)
        if best is not None:
            works = self.list_works_of_author(best["id"], limit=limit, since_year=since_year)
            if works:
                self.last_author = best
                return works
            self.last_author = None
        else:
            self.last_author = None
        return self.search_by_raw_author_name(
            name, institution=institution, limit=limit, since_year=since_year
        )

    @property
    def last_author_note(self) -> str:
        """给用户看的一句话：本次是按哪个作者档案查的（没有就是没匹配上）。"""
        if not self.last_author:
            return "未匹配到作者档案，已按论文上的署名检索并逐篇核对姓名"
        institutions = "、".join(self.last_author.get("institutions", [])[:2])
        return f"作者档案：{self.last_author['name']}（{institutions or '机构未标注'}）"

    def find_authors(
        self, name: str, per_page: int = 10, extra_name: str | None = None
    ) -> list[dict]:
        """查作者实体候选（含机构、论文数、是否带 ORCID），供人工/规则挑选。

        中文名与英文名都查一遍再合并：中文名能查到「陆伟」本人的档案，
        英文名能查到用罗马化写法（Wei Lü）登记的同一个人的档案，两边都查召回更全。
        """
        queries = [name]
        if extra_name and extra_name.strip() and extra_name.strip() != name:
            queries.append(extra_name.strip())
        merged: dict[str, dict] = {}
        for query in queries:
            for author in self._search_authors_once(query, per_page):
                merged.setdefault(author["id"], author)
        return list(merged.values())

    def _search_authors_once(self, query: str, per_page: int) -> list[dict]:
        resp = self.client.get(
            f"{_BASE_URL}/authors",
            params=self._params({"search": query, "per-page": str(max(1, min(per_page, 50)))}),
        )
        resp.raise_for_status()
        if not _is_json_response(resp):
            raise OpenAlexUnavailable("返回内容不是 JSON（可能被网关拦截）")
        authors: list[dict] = []
        for item in resp.json().get("results", []):
            institutions = [
                (inst or {}).get("display_name", "")
                for inst in (item.get("last_known_institutions") or [])
            ]
            authors.append(
                {
                    "id": _short_id(item.get("id", "")),
                    "name": item.get("display_name", ""),
                    "works_count": item.get("works_count") or 0,
                    "institutions": [name for name in institutions if name],
                    "orcid": item.get("orcid") or "",
                    "topics": [
                        (topic or {}).get("display_name", "")
                        for topic in (item.get("topics") or [])[:5]
                    ],
                }
            )
        return authors

    @staticmethod
    def pick_author(
        candidates: list[dict],
        name: str,
        institution: str | None = None,
        english_name: str | None = None,
    ) -> dict | None:
        """挑出最可能是本人的作者实体：机构对得上才认，对不上就返回 None。

        为什么机构对不上时宁可不认：同名同姓的作者实体有几十个（「Wei Zhang」在 OpenAlex
        里有几十个），如果只按"论文数最多"去挑，会把某位不相干教授的全部论文端给用户——
        这比"没结果"更糟。机构对不上时退回「按署名检索 + 逐篇核对姓名」，召回一堆同名
        候选，交给后续消歧与人工确认，不会误导用户。

        姓名核对分两档：姓名+机构都对上最可信；只对上机构次之——因为中文名的罗马化写法
        千差万别（陆伟 = Wei Lü = Lu Wei），字符串核对认不出同一个人，但机构能。
        """
        if not candidates:
            return None
        targets = [value for value in (name, english_name) if value]
        matched_name = [
            author
            for author in candidates
            if any(author_matches(target, [author["name"]]) for target in targets)
        ]
        if not institution:
            pool = matched_name or candidates
            return max(pool, key=lambda author: author["works_count"])

        has_institution = [
            author
            for author in candidates
            if any(institution_matches(inst, institution) for inst in author["institutions"])
        ]
        both = [author for author in has_institution if author in matched_name]
        pool = both or has_institution
        if not pool:
            return None
        return max(pool, key=lambda author: author["works_count"])

    def list_works_of_author(
        self, author_id: str, *, limit: int = 25, since_year: int | None = None
    ) -> list[Work]:
        filters = [f"author.id:{author_id}", "type:article"]
        if since_year:
            filters.append(f"from_publication_date:{since_year}-01-01")
        resp = self.client.get(
            f"{_BASE_URL}/works",
            params=self._params(
                {
                    "filter": ",".join(filters),
                    "per-page": str(max(1, min(limit, 100))),
                    "sort": "publication_date:desc",
                    "select": _SELECT_FIELDS,
                }
            ),
        )
        resp.raise_for_status()
        if not _is_json_response(resp):
            raise OpenAlexUnavailable("返回内容不是 JSON（可能被网关拦截）")
        return [self._to_work(item) for item in resp.json().get("results", [])]

    def search_by_raw_author_name(
        self,
        name: str,
        *,
        institution: str | None = None,
        limit: int = 25,
        since_year: int | None = None,
    ) -> list[Work]:
        """兜底：按论文上的原始署名检索，并逐篇核对作者名单。"""
        filters = [f"raw_author_name.search:{name}", "type:article"]
        if since_year:
            filters.append(f"from_publication_date:{since_year}-01-01")
        resp = self.client.get(
            f"{_BASE_URL}/works",
            params=self._params(
                {
                    "filter": ",".join(filters),
                    "per-page": str(max(1, min(limit, 100))),
                    "sort": "publication_date:desc",
                    "select": _SELECT_FIELDS,
                }
            ),
        )
        resp.raise_for_status()
        if not _is_json_response(resp):
            raise OpenAlexUnavailable("返回内容不是 JSON（可能被网关拦截）")
        works = [self._to_work(item) for item in resp.json().get("results", [])]
        # 模糊匹配会带出「陆鑫」这类同姓同字的别人，这里按作者名单严格核对
        return [work for work in works if author_matches(name, work.authors)]

    def search_publications(
        self,
        name: str,
        *,
        institution: str | None = None,
        english_name: str | None = None,
        source: str = "en",
        limit: int = 20,
    ) -> list[Work]:
        """检索路由器统一调用的入口。"""
        return self.search_works_by_author(
            name, institution=institution, english_name=english_name, limit=limit
        )

    def search_by_title(self, title: str, *, source: str = "en", limit: int = 5) -> list[Work]:
        resp = self.client.get(
            f"{_BASE_URL}/works",
            params=self._params(
                {
                    "filter": f"title.search:{title}",
                    "per-page": str(max(1, min(limit, 100))),
                    "select": _SELECT_FIELDS,
                }
            ),
        )
        resp.raise_for_status()
        if not _is_json_response(resp):
            raise OpenAlexUnavailable("返回内容不是 JSON（可能被网关拦截）")
        return [self._to_work(item) for item in resp.json().get("results", [])]


def search_candidate_works(
    provider: OpenAlexProvider, name: str, institution: str | None = None
) -> list[Work]:
    """检索候选作者并返回其论文；结果一律待用户确认。"""
    authors = provider.search_authors(name, institution)
    if not authors:
        return []
    return provider.list_works(authors[0]["id"])
