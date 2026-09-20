"""多源学术库适配器测试（全部用 MockTransport，不触真实网络）。"""

from __future__ import annotations

import httpx
import pytest

from advisor_fit.providers.arxiv import ArxivProvider, ArxivUnavailable
from advisor_fit.providers.crossref import CrossrefProvider
from advisor_fit.providers.dblp import DblpProvider, DblpUnavailable
from advisor_fit.providers.europepmc import EuropePmcProvider
from advisor_fit.providers.openalex import OpenAlexProvider, OpenAlexUnavailable

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2412.01234v1</id>
    <published>2024-12-02T10:00:00Z</published>
    <title>Robust   Retrieval
      Augmented Generation</title>
    <summary>
      We study retrieval augmented generation.
    </summary>
    <author><name>Wei Zhang</name></author>
    <author><name>Li Wang</name></author>
    <arxiv:primary_category term="cs.CL"/>
    <arxiv:journal_ref>NeurIPS 2024</arxiv:journal_ref>
  </entry>
</feed>
"""


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------- Europe PMC ----------


def test_europepmc_maps_fields_and_marks_medicine():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "AUTH" in request.url.params["query"]
        return httpx.Response(
            200,
            json={
                "resultList": {
                    "result": [
                        {
                            "id": "12345",
                            "source": "MED",
                            "pmid": "12345",
                            "doi": "10.1000/med",
                            "title": "Tumour immunology review",
                            "pubYear": "2023",
                            "citedByCount": 17,
                            "abstractText": "A review of tumour immunology.",
                            "journalInfo": {"journal": {"title": "Nature Medicine"}},
                            "authorList": {
                                "author": [
                                    {"fullName": "Wei Zhang", "affiliation": "Wuhan University"},
                                    {"fullName": "Li Wang"},
                                ]
                            },
                        }
                    ]
                }
            },
        )

    provider = EuropePmcProvider(client=client_for(handler))
    works = provider.search_publications("Wei Zhang", institution="Wuhan University")
    assert len(works) == 1
    work = works[0]
    assert work.title == "Tumour immunology review"
    assert work.year == 2023
    assert work.doi == "10.1000/med"
    assert work.venue == "Nature Medicine"
    assert work.abstract == "A review of tumour immunology."
    assert work.authors == ["Wei Zhang", "Li Wang"]
    assert work.institution == "Wuhan University"
    assert work.disciplines == ["medicine"]
    assert work.source_platform == "Europe PMC"
    assert work.source_url == "https://doi.org/10.1000/med"


def test_europepmc_does_not_send_chinese_institution_as_filter():
    """中文校名和库里的英文机构名对不上，加进去只会把结果筛成 0。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["query"])
        return httpx.Response(200, json={"resultList": {"result": []}})

    provider = EuropePmcProvider(client=client_for(handler))
    provider.search_publications("王伟", institution="武汉大学")
    assert seen == ['AUTH:"王伟"']


def test_europepmc_keeps_english_institution_as_filter():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["query"])
        return httpx.Response(200, json={"resultList": {"result": []}})

    provider = EuropePmcProvider(client=client_for(handler))
    provider.search_publications("Wei Zhang", institution="Wuhan University")
    assert seen == ['AUTH:"Wei Zhang" AND AFF:"Wuhan University"']


# ---------- arXiv ----------


def test_arxiv_parses_atom_feed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert 'au:"Wei Zhang"' in str(request.url.params["search_query"])
        return httpx.Response(200, text=ARXIV_XML)

    provider = ArxivProvider(client=client_for(handler))
    works = provider.search_publications("Wei Zhang")
    assert len(works) == 1
    work = works[0]
    assert work.title == "Robust Retrieval Augmented Generation"
    assert work.year == 2024
    assert work.abstract == "We study retrieval augmented generation."
    assert work.authors == ["Wei Zhang", "Li Wang"]
    assert work.venue == "NeurIPS 2024"
    assert work.disciplines == ["cs"]
    assert work.source_url == "http://arxiv.org/abs/2412.01234v1"


def test_arxiv_raises_unavailable_on_non_xml_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>rate limited</html" )  # 故意不合法

    provider = ArxivProvider(client=client_for(handler))
    with pytest.raises(ArxivUnavailable):
        provider.search_publications("Wei Zhang")


# ---------- OpenAlex ----------


def openalex_payload() -> dict:
    return {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1000/xyz",
                "title": "RAG Survey",
                "publication_year": 2025,
                "cited_by_count": 9,
                "abstract_inverted_index": {"RAG": [0], "survey": [1]},
                "authorships": [
                    {
                        "author": {"display_name": "Wei Zhang"},
                        "institutions": [{"display_name": "Wuhan University"}],
                    },
                    {
                        "author": {"display_name": "Li Wang"},
                        "institutions": [{"display_name": "Wuhan University"}],
                    },
                ],
                "primary_location": {"source": {"display_name": "NeurIPS"}},
                "primary_topic": {
                    "display_name": "Information Retrieval",
                    "field": {"display_name": "Computer Science"},
                    "subfield": {"display_name": "Artificial Intelligence"},
                },
            }
        ]
    }


def openalex_handler(authors: list[dict], works: list[dict], seen: list[str]):
    """按路径分发：/authors 返回作者实体候选，/works 返回论文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/authors"):
            return httpx.Response(200, json={"results": authors})
        return httpx.Response(200, json={"results": works})

    return handler


def author_candidate(**overrides) -> dict:
    item = {
        "id": "https://openalex.org/A1",
        "display_name": "Wei Lü",
        "works_count": 230,
        "last_known_institutions": [{"display_name": "Wuhan University"}],
        "orcid": "https://orcid.org/0000-0002-1825-0097",
        "topics": [{"display_name": "Information Retrieval"}],
    }
    item.update(overrides)
    return item


def test_openalex_uses_author_entity_then_lists_that_authors_works():
    seen: list[str] = []
    handler = openalex_handler([author_candidate()], openalex_payload()["results"], seen)
    provider = OpenAlexProvider(client=client_for(handler), mailto="dev@example.com")
    works = provider.search_publications("陆伟", institution="武汉大学")

    assert seen == ["/authors", "/works"]
    work = works[0]
    assert work.title == "RAG Survey"
    assert work.abstract == "RAG survey"
    assert work.authors == ["Wei Zhang", "Li Wang"]
    assert work.institution == "Wuhan University"
    assert work.venue == "NeurIPS"
    assert work.citation_count == 9
    assert work.disciplines == ["cs"]
    assert work.source_url == "https://doi.org/10.1000/xyz"


def test_openalex_works_filter_targets_the_chosen_author_id():
    seen_filters: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authors"):
            return httpx.Response(200, json={"results": [author_candidate()]})
        seen_filters.append(request.url.params["filter"])
        return httpx.Response(200, json=openalex_payload())

    provider = OpenAlexProvider(client=client_for(handler))
    provider.search_publications("陆伟", institution="武汉大学")
    # 作者实体查得到论文时，不再走"原始署名"兜底，所以只会有一次 /works 请求
    assert seen_filters == ["author.id:A1,type:article"]


def test_openalex_falls_back_to_raw_name_and_verifies_every_author():
    """查不到作者实体时退回原始署名检索，并且必须逐篇核对作者名单。

    实测「陆伟」的分词模糊匹配会带出「陆鑫」「陆长峰」的论文——这些必须被过滤掉。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authors"):
            return httpx.Response(200, json={"results": []})
        assert request.url.params["filter"].startswith("raw_author_name.search:陆伟")
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "title": "真论文",
                        "publication_year": 2024,
                        "authorships": [{"author": {"display_name": "陆伟"}}],
                    },
                    {
                        "id": "https://openalex.org/W2",
                        "title": "别人的论文",
                        "publication_year": 2024,
                        "authorships": [{"author": {"display_name": "陆鑫"}}],
                    },
                ]
            },
        )

    provider = OpenAlexProvider(client=client_for(handler))
    works = provider.search_publications("陆伟", institution="武汉大学")
    assert [work.title for work in works] == ["真论文"]


def test_pick_author_prefers_institution_match_over_more_works():
    candidates = [
        {
            "id": "A_many",
            "name": "Wei Zhang",
            "works_count": 9999,
            "institutions": ["University of Miami"],
        },
        {
            "id": "A_match",
            "name": "Wei Zhang",
            "works_count": 120,
            "institutions": ["Tsinghua University"],
        },
    ]
    picked = OpenAlexProvider.pick_author(candidates, "张伟", "清华大学")
    assert picked is not None
    assert picked["id"] == "A_match"


def test_pick_author_returns_none_without_candidates():
    assert OpenAlexProvider.pick_author([], "张伟", "清华大学") is None


def test_pick_author_refuses_to_guess_when_no_institution_matches():
    """机构一个都对不上时不许"矬子里拔将军"——否则会把不相干教授的全部论文端给用户。"""
    candidates = [
        {
            "id": "A_many",
            "name": "Wei Zhang",
            "works_count": 9999,
            "institutions": ["University of Miami"],
        },
        {
            "id": "A_other",
            "name": "Wei Zhang",
            "works_count": 12,
            "institutions": ["Nanjing University of Chinese Medicine"],
        },
    ]
    assert OpenAlexProvider.pick_author(candidates, "张伟", "北京大学") is None
    # 没有机构可比时，仍然按论文数挑一个（此时不做机构判断）
    picked = OpenAlexProvider.pick_author(candidates, "张伟", None)
    assert picked is not None and picked["id"] == "A_many"


def test_openalex_falls_back_to_raw_name_when_author_profile_does_not_match():
    seen_filters: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authors"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/A_bad",
                            "display_name": "Wei Zhang",
                            "works_count": 9999,
                            "last_known_institutions": [{"display_name": "University of Miami"}],
                        }
                    ]
                },
            )
        seen_filters.append(request.url.params["filter"])
        return httpx.Response(200, json={"results": []})

    provider = OpenAlexProvider(client=client_for(handler))
    provider.search_publications("张伟", institution="北京大学")
    assert seen_filters == ["raw_author_name.search:张伟,type:article"]
    assert "未匹配到作者档案" in provider.last_author_note


def test_openalex_reports_which_author_profile_was_used():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authors"):
            return httpx.Response(200, json={"results": [author_candidate()]})
        return httpx.Response(200, json=openalex_payload())

    provider = OpenAlexProvider(client=client_for(handler))
    provider.search_publications("陆伟", institution="武汉大学")
    assert "Wei Lü" in provider.last_author_note
    assert "Wuhan University" in provider.last_author_note


def test_openalex_raises_unavailable_when_gateway_returns_html():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>blocked</html>")

    provider = OpenAlexProvider(client=client_for(handler))
    with pytest.raises(OpenAlexUnavailable):
        provider.search_publications("Wei Zhang")


def test_openalex_title_search_uses_title_filter():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["filter"])
        return httpx.Response(200, json={"results": []})

    provider = OpenAlexProvider(client=client_for(handler))
    provider.search_by_title("Attention is all you need")
    assert seen == ["title.search:Attention is all you need"]


# ---------- Crossref ----------


def test_crossref_author_search_parses_authors_and_year():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {
                            "DOI": "10.1000/cr",
                            "title": ["Cross-domain recommendation"],
                            "issued": {"date-parts": [[2022, 5]]},
                            "container-title": ["RecSys"],
                            "is-referenced-by-count": 5,
                            "author": [
                                {"given": "Wei", "family": "Zhang"},
                                {"family": "Wang"},
                            ],
                        }
                    ]
                }
            },
        )

    provider = CrossrefProvider(client=client_for(handler))
    works = provider.search_publications("Wei Zhang", institution="Wuhan University")
    assert seen[0]["query.author"] == "Wei Zhang"
    assert seen[0]["query.affiliation"] == "Wuhan University"
    work = works[0]
    assert work.title == "Cross-domain recommendation"
    assert work.year == 2022
    assert work.authors == ["Wei Zhang", "Wang"]
    assert work.venue == "RecSys"
    assert work.source_url == "https://doi.org/10.1000/cr"


# ---------- DBLP ----------


def test_dblp_detects_anti_bot_html_page():
    """DBLP 启用了 Anubis 反爬：HTTP 200 但返回 HTML 挑战页，必须当成'不可用'。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><title>Making sure you're not a bot!</title>")

    provider = DblpProvider(client=client_for(handler))
    with pytest.raises(DblpUnavailable):
        provider.search_publications("Wei Zhang")


def test_dblp_parses_authors_and_marks_cs():
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
                                    "doi": "10.1000/kg",
                                    "key": "conf/kg/kg24",
                                    "venue": "ISWC",
                                    "authors": {
                                        "author": [
                                            {"@pid": "1", "text": "Wei Zhang"},
                                            {"@pid": "2", "text": "Li Wang"},
                                        ]
                                    },
                                }
                            }
                        ]
                    }
                }
            },
        )

    provider = DblpProvider(client=client_for(handler))
    work = provider.search_publications("Wei Zhang")[0]
    assert work.authors == ["Wei Zhang", "Li Wang"]
    assert work.disciplines == ["cs"]
    assert work.venue == "ISWC"
