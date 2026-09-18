"""OpenAlex 学术 Provider：作者候选召回与近 5 年论文，含磁盘缓存与 429 重试。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

import httpx

from advisor_fit.models.professor import AuthorCandidate, ExternalIds
from advisor_fit.providers.academic import AcademicProviderUnavailable, AuthorQuery, Work

_BASE_URL = "https://api.openalex.org"
_USER_AGENT = "AdvisorFitBot/0.1 (contact: you@example.com)"
_MAX_CANDIDATES = 5
_MAX_RETRIES = 3
_CACHE_TTL_SECONDS = 7 * 24 * 3600


def _short_id(openalex_id: str | None) -> str | None:
    if not openalex_id:
        return None
    return openalex_id.rstrip("/").split("/")[-1]


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi or None


class _DiskCache:
    def __init__(self, cache_dir: Path, ttl_seconds: int = _CACHE_TTL_SECONDS) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(url: str, params: dict[str, str]) -> str:
        canonical = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get(self, url: str, params: dict[str, str]) -> dict | None:
        path = self.cache_dir / f"{self._key(url, params)}.json"
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.ttl_seconds:
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, url: str, params: dict[str, str], data: dict) -> None:
        path = self.cache_dir / f"{self._key(url, params)}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class _NullCache:
    def get(self, url: str, params: dict[str, str]) -> dict | None:
        return None

    def put(self, url: str, params: dict[str, str], data: dict) -> None:
        return None


class OpenAlexProvider:
    def __init__(
        self,
        cache_dir: Path | str | None = None,
        mailto: str | None = None,
        retry_backoff: float = 1.0,
        timeout: float = 15.0,
    ) -> None:
        self.cache: _DiskCache | _NullCache = (
            _DiskCache(Path(cache_dir)) if cache_dir else _NullCache()
        )
        self.mailto = mailto
        self.retry_backoff = retry_backoff
        self.timeout = timeout

    async def search_authors(self, query: AuthorQuery) -> list[AuthorCandidate]:
        search = query.name
        if query.institution:
            search = f"{query.name} {query.institution}"
        params = {"search": search, "per-page": str(_MAX_CANDIDATES)}
        data = await self._get_json("/authors", params)
        return [self._author_to_candidate(a) for a in data.get("results", [])][:_MAX_CANDIDATES]

    async def list_recent_works(self, author_id: str, since_year: int) -> list[Work]:
        params = {
            "filter": f"author.id:{author_id},from_publication_date:{since_year}-01-01",
            "per-page": "100",
            "select": "id,title,publication_year,doi,cited_by_count,primary_location,concepts",
        }
        data = await self._get_json("/works", params)
        return dedupe_works([self._work_to_model(w) for w in data.get("results", [])])

    @staticmethod
    def _author_to_candidate(author: dict) -> AuthorCandidate:
        affiliations: list[str] = []
        for aff in author.get("affiliations") or []:
            inst = (aff or {}).get("institution") or {}
            name = inst.get("display_name")
            if name and name not in affiliations:
                affiliations.append(name)
        last_known: str | None = None
        for entry in author.get("last_known_institutions") or []:
            if isinstance(entry, dict):
                name = entry.get("display_name")
            else:
                name = entry
            if name and name not in affiliations:
                affiliations.append(name)
            if last_known is None and name:
                last_known = name

        ids = author.get("ids") or {}
        orcid = ids.get("orcid")
        if orcid:
            orcid = orcid.rstrip("/").split("/")[-1]

        topics = [
            t.get("display_name")
            for t in (author.get("topics") or author.get("x_concepts") or [])
            if isinstance(t, dict) and t.get("display_name")
        ]

        openalex_id = _short_id(author.get("id"))
        return AuthorCandidate(
            id=openalex_id or "",
            name=author.get("display_name") or "",
            external_ids=ExternalIds(openalex=openalex_id, orcid=orcid),
            affiliations=affiliations,
            works_count=author.get("works_count"),
            topics=topics,
            last_known_institution=last_known,
        )

    @staticmethod
    def _work_to_model(work: dict) -> Work:
        venue = None
        primary = work.get("primary_location") or {}
        source = primary.get("source") or {}
        venue = source.get("display_name")

        topics = [
            c.get("display_name")
            for c in (work.get("concepts") or [])
            if isinstance(c, dict) and c.get("display_name")
        ]

        work_id = _short_id(work.get("id")) or work.get("id") or ""
        return Work(
            id=work_id,
            title=work.get("title") or "",
            year=work.get("publication_year"),
            doi=_normalize_doi(work.get("doi")),
            venue=venue,
            topics=topics,
            citation_count=work.get("cited_by_count"),
        )

    async def _get_json(self, path: str, params: dict[str, str]) -> dict:
        url = _BASE_URL + path
        cached = self.cache.get(url, params)
        if cached is not None:
            return cached

        full_params = dict(params)
        if self.mailto:
            full_params["mailto"] = self.mailto

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, headers={"User-Agent": _USER_AGENT}
                ) as client:
                    resp = await client.get(url, params=full_params)
            except httpx.HTTPError as exc:
                last_exc = exc
                await asyncio.sleep(self._backoff(attempt))
                continue

            if resp.status_code == 429:
                await asyncio.sleep(self._retry_delay(resp, attempt))
                continue
            if resp.status_code >= 400:
                raise AcademicProviderUnavailable(f"OpenAlex HTTP {resp.status_code} for {path}")

            data = resp.json()
            self.cache.put(url, params, data)
            return data

        raise AcademicProviderUnavailable(f"OpenAlex unavailable: {last_exc}")

    def _backoff(self, attempt: int) -> float:
        return min(self.retry_backoff * (2**attempt), 4.0)

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return self._backoff(attempt)


def dedupe_works(works: list[Work]) -> list[Work]:
    """按 DOI（小写规范化）去重；无 DOI 时按 规范化标题 + 年份 去重。"""
    seen: set[str] = set()
    out: list[Work] = []
    for work in works:
        key = _normalize_doi(work.doi)
        if not key:
            key = "title:" + (work.title or "").strip().lower() + ":" + str(work.year)
        if key in seen:
            continue
        seen.add(key)
        out.append(work)
    return out
