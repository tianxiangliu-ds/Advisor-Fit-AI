"""Task 5：OpenAlex Provider、缓存与论文归一化测试。"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from advisor_fit.providers.academic import (
    AcademicProviderUnavailable,
    AuthorQuery,
    Work,
)
from advisor_fit.providers.openalex import OpenAlexProvider, _normalize_doi, dedupe_works

AUTHORS_URL = re.compile(r"https://api\.openalex\.org/authors.*")
WORKS_URL = re.compile(r"https://api\.openalex\.org/works.*")


@pytest.fixture
def provider(tmp_path) -> OpenAlexProvider:
    return OpenAlexProvider(cache_dir=tmp_path / "cache", retry_backoff=0.01)


def _load(fixtures_dir, name: str) -> dict:
    return json.loads((fixtures_dir / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_search_authors_maps_affiliations_and_ids(respx_mock, provider, fixtures_dir):
    respx_mock.get(AUTHORS_URL).respond(
        200, json=_load(fixtures_dir, "openalex_candidates.json")
    )
    candidates = await provider.search_authors(AuthorQuery(name="王伟", institution="武汉大学"))
    assert candidates[0].external_ids.openalex == "A123"
    assert candidates[0].external_ids.orcid == "0000-0002-1234-5678"
    assert "武汉大学" in candidates[0].affiliations


@pytest.mark.asyncio
async def test_list_recent_works_maps_fields(respx_mock, provider, fixtures_dir):
    respx_mock.get(WORKS_URL).respond(200, json=_load(fixtures_dir, "openalex_works.json"))
    works = await provider.list_recent_works("A123", 2022)
    assert works[0].doi == "10.1234/rag"
    assert works[0].year == 2026
    assert works[0].venue == "ACL"


@pytest.mark.asyncio
async def test_429_retries_then_succeeds(respx_mock, provider, fixtures_dir):
    payload = _load(fixtures_dir, "openalex_candidates.json")
    route = respx_mock.get(AUTHORS_URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=payload)]
    )
    result = await provider.search_authors(AuthorQuery(name="王伟", institution="武汉大学"))
    assert route.call_count == 2
    assert result


@pytest.mark.asyncio
async def test_provider_failure_returns_explicit_error(respx_mock, provider):
    respx_mock.get(AUTHORS_URL).respond(500, json={"error": "boom"})
    with pytest.raises(AcademicProviderUnavailable):
        await provider.search_authors(AuthorQuery(name="王伟"))


@pytest.mark.asyncio
async def test_cache_avoids_second_request(respx_mock, provider, fixtures_dir):
    payload = _load(fixtures_dir, "openalex_candidates.json")
    route = respx_mock.get(AUTHORS_URL).respond(200, json=payload)
    await provider.search_authors(AuthorQuery(name="王伟", institution="武汉大学"))
    await provider.search_authors(AuthorQuery(name="王伟", institution="武汉大学"))
    assert route.call_count == 1


def test_doi_normalization():
    assert _normalize_doi("https://doi.org/10.1234/RAG") == "10.1234/rag"
    assert _normalize_doi("10.1234/RAG") == "10.1234/rag"
    assert _normalize_doi(None) is None


def test_dedupe_works_by_doi_and_title():
    works = [
        Work(id="W1", title="A", year=2026, doi="10.1/a"),
        Work(id="W2", title="A (dup)", year=2026, doi="10.1/A"),
        Work(id="W3", title="B", year=2025, doi=None),
        Work(id="W4", title="B", year=2025, doi=None),
    ]
    assert len(dedupe_works(works)) == 2
