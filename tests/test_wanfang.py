"""万方 Provider 测试（不触真实网络）。"""

import json

import httpx

from advisor_fit.providers.wanfang import WanfangProvider


def test_wanfang_search_parses_documents():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Ca-AppKey"] == "test-key"
        body = json.loads(request.content)
        assert body["query"].startswith("Creator:")
        return httpx.Response(
            200,
            json={
                "documents": [
                    {
                        "fields": {
                            "Id": {"stringValue": "jsjxb201706001"},
                            "Title": {
                                "listValue": {"values": [{"stringValue": "卷积神经网络研究综述"}]}
                            },
                            "Creator": {
                                "listValue": {
                                    "values": [{"stringValue": "周飞燕"}, {"stringValue": "金林鹏"}]
                                }
                            },
                            "PublishYear": {"numberValue": 2017},
                            "Abstract": {
                                "listValue": {
                                    "values": [{"stringValue": "作为一个崭新领域"}]
                                }
                            },
                            "Keywords": {
                                "listValue": {
                                    "values": [
                                        {"stringValue": "卷积神经网络"},
                                        {"stringValue": "深度学习"},
                                    ]
                                }
                            },
                            "DOI": {"stringValue": "10.11897/SP.J.1016.2017.01229"},
                        }
                    }
                ],
                "numFound": "1",
            },
        )

    provider = WanfangProvider(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    works = provider.search_publications("周飞燕")
    assert len(works) == 1
    work = works[0]
    assert work.title == "卷积神经网络研究综述"
    assert work.year == 2017
    assert work.abstract.startswith("作为")
    assert work.doi == "10.11897/SP.J.1016.2017.01229"
    assert work.source_url == "https://doi.org/10.11897/SP.J.1016.2017.01229"
    assert work.topics == ["卷积神经网络", "深度学习"]
    assert work.source_platform == "万方"
