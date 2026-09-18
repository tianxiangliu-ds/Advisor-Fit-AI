"""DBLP Provider 测试（不触真实网络）。"""

import httpx

from advisor_fit.providers.dblp import DblpProvider


def test_search_publications_maps_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "hits": {
                        "hit": [
                            {
                                "info": {
                                    "title": "Knowledge Graph Construction",
                                    "year": "2024",
                                    "doi": "10.1000/xyz",
                                    "key": "conf/kg/kg24",
                                    "ee": "https://doi.org/10.1000/xyz",
                                }
                            }
                        ]
                    }
                }
            },
        )

    provider = DblpProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    works = provider.search_publications("王伟")
    assert len(works) == 1
    work = works[0]
    assert work.title == "Knowledge Graph Construction"
    assert work.year == 2024
    assert work.source_url == "https://doi.org/10.1000/xyz"
    assert work.abstract == ""
    assert work.source_platform == "DBLP"


def test_search_publications_handles_single_hit_as_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "hits": {
                        "hit": {
                            "info": {
                                "title": "One Paper",
                                "year": "2023",
                                "key": "conf/x/x23",
                            }
                        }
                    }
                }
            },
        )

    provider = DblpProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    works = provider.search_publications("张三")
    assert [w.title for w in works] == ["One Paper"]
