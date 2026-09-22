"""Scopus 适配器：全部用模拟 HTTP，绝不使用真实密钥或真实网络。"""

from __future__ import annotations

import httpx
import pytest

from advisor_fit.providers.scopus import ScopusProvider, ScopusUnavailable


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_scopus_resolves_matching_author_then_maps_recent_works():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/author"):
            return httpx.Response(
                200,
                json={
                    "search-results": {
                        "entry": [
                            {
                                "dc:identifier": "AUTHOR_ID:123",
                                "preferred-name": {"given-name": "Yongchao", "surname": "Xu"},
                                "affiliation-current": {"affiliation-name": "Wuhan University"},
                            }
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "search-results": {
                    "entry": [
                        {
                            "eid": "2-s2.0-1",
                            "dc:title": "Medical image segmentation",
                            "prism:coverDate": "2025-04-01",
                            "prism:doi": "10.1000/example",
                            "prism:publicationName": "Pattern Recognition",
                            "prism:url": "https://api.elsevier.com/content/abstract/eid/2-s2.0-1",
                            "dc:creator": "Xu, Yongchao",
                            "citedby-count": "8",
                            "affilname": "Wuhan University",
                        }
                    ]
                }
            },
        )

    provider = ScopusProvider("secret-is-never-asserted", client=_client(handler))
    works = provider.search_publications(
        "许永超", english_name="Yongchao Xu", institution="Wuhan University", limit=5
    )

    assert [work.title for work in works] == ["Medical image segmentation"]
    assert works[0].doi == "10.1000/example"
    assert works[0].year == 2025
    assert works[0].institution == "Wuhan University"
    assert provider.last_author_note == "Scopus 作者档案：Yongchao Xu（Wuhan University）"
    assert calls[0].headers["X-ELS-APIKey"] == "secret-is-never-asserted"
    assert calls[1].url.params["query"] == "AU-ID(123)"


def test_scopus_returns_no_result_when_no_author_matches_institution():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "search-results": {
                    "entry": [
                        {
                            "dc:identifier": "AUTHOR_ID:999",
                            "preferred-name": {"given-name": "Other", "surname": "Xu"},
                            "affiliation-current": {"affiliation-name": "Another University"},
                        }
                    ]
                }
            },
        )

    provider = ScopusProvider("key", client=_client(handler))
    assert provider.search_publications(
        "许永超", english_name="Yongchao Xu", institution="Wuhan University"
    ) == []
    assert "未匹配到" in provider.last_author_note


def test_scopus_turns_forbidden_response_into_an_unavailable_error():
    provider = ScopusProvider("key", client=_client(lambda _request: httpx.Response(403)))

    with pytest.raises(ScopusUnavailable, match="访问权限"):
        provider.search_publications("许永超", english_name="Yongchao Xu")
