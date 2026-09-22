"""Springer Nature 题名补充适配器，不触真实网络。"""

from __future__ import annotations

import httpx
import pytest

from advisor_fit.providers.springer import SpringerProvider, SpringerUnavailable


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_springer_meta_maps_title_search_to_work():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "title": "Vision research",
                        "publicationDate": "2025-02-01",
                        "doi": "10.1000/springer",
                        "publicationName": "Nature Machine Intelligence",
                        "creators": [{"creator": "Yongchao Xu"}],
                        "url": [{"value": "https://link.springer.com/article/example"}],
                    }
                ]
            },
        )

    works = SpringerProvider(meta_api_key="configured", client=_client(handler)).search_by_title(
        "Vision research"
    )

    assert works[0].title == "Vision research"
    assert works[0].doi == "10.1000/springer"
    assert works[0].authors == ["Yongchao Xu"]
    assert seen[0].url.params["api_key"] == "configured"


def test_springer_permission_failure_is_explicit():
    provider = SpringerProvider(
        meta_api_key="configured", client=_client(lambda _request: httpx.Response(403))
    )
    with pytest.raises(SpringerUnavailable, match="访问权限"):
        provider.search_by_title("Vision research")


def test_springer_open_access_uses_its_own_key_and_endpoint():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"records": []})

    provider = SpringerProvider(
        open_access_api_key="oa-configured",
        client=_client(handler),
    )

    assert provider.search_by_title("Vision research") == []
    assert seen[0].url.path.endswith("/openaccess/json")
    assert seen[0].url.params["api_key"] == "oa-configured"
