"""万方 Provider 测试（不触真实网络）。"""

import json

import httpx

from advisor_fit.providers.wanfang import WanfangProvider, group_english_authors


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
                            "OrganizationNorm": {
                                "listValue": {"values": [{"stringValue": "武汉大学"}]}
                            },
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
    assert work.authors == ["周飞燕", "金林鹏"]
    assert work.institution == "武汉大学"


def test_wanfang_search_adds_institution_and_sorts_by_year():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"documents": [], "numFound": "0"})

    provider = WanfangProvider(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    provider.search_publications("陆伟", institution="武汉大学")
    body = captured["body"]
    assert body["query"] == "Creator:陆伟 AND OrganizationForSearch:武汉大学"
    assert body["sort"] == {"sorts": [{"by": "PublishYear", "order": "DESC"}]}
    assert body["collections"] == ["OpenPeriodical", "OpenConference"]


def test_wanfang_english_source_uses_english_collection():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"documents": [], "numFound": "0"})

    provider = WanfangProvider(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    provider.search_publications("Wei Lu", source="en")
    assert captured["body"]["collections"] == ["OpenPeriodicalEng"]


def test_wanfang_english_source_groups_split_author_tokens():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "documents": [
                    {
                        "fields": {
                            "Id": {"stringValue": "abc123"},
                            "Title": {"stringValue": "Deep learning for X"},
                            "Creator": {
                                "listValue": {
                                    "values": [
                                        {"stringValue": "Fan"},
                                        {"stringValue": "A."},
                                        {"stringValue": "Y."},
                                        {"stringValue": "Zhang"},
                                        {"stringValue": "Q."},
                                    ]
                                }
                            },
                            "PeriodicalTitle": {"stringValue": "IEEE Access"},
                        }
                    }
                ],
                "numFound": "1",
            },
        )

    provider = WanfangProvider(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    works = provider.search_publications("Fan", source="en")
    assert works[0].authors == ["Fan A. Y.", "Zhang Q."]
    assert works[0].venue == "IEEE Access"


def test_wanfang_client_side_institution_filter_drops_homonyms():
    from advisor_fit.providers.academic import Work

    works = [
        Work(id="1", title="匹配", institution="武汉大学信息管理学院"),
        Work(id="2", title="同名", institution="中国药科大学"),
        Work(id="3", title="空机构", institution=""),
    ]
    kept = WanfangProvider._filter_by_institution(works, "武汉大学")
    assert [w.title for w in kept] == ["匹配", "空机构"]


def test_group_english_authors_keeps_surname_initial_pairs():
    assert group_english_authors(["Fan", "A.", "Y.", "Zhang", "Q."]) == [
        "Fan A. Y.",
        "Zhang Q.",
    ]
    assert group_english_authors(["陆伟"]) == ["陆伟"]
    assert group_english_authors([]) == []
