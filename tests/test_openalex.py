"""OpenAlex Provider 测试（不触真实网络）。"""

import httpx

from advisor_fit.providers.openalex import OpenAlexProvider, _reconstruct_abstract


def test_reconstruct_abstract():
    assert _reconstruct_abstract({"RAG": [0], "survey": [1], "of": [2]}) == "RAG survey of"
    assert _reconstruct_abstract(None) == ""


def test_search_authors_maps_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "https://openalex.org/A1", "display_name": "王伟", "works_count": 10}
                ]
            },
        )

    provider = OpenAlexProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.search_authors("王伟", "武汉大学") == [
        {"id": "A1", "name": "王伟", "works_count": 10}
    ]


def test_list_works_maps_fields_and_reconstructs_abstract():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "title": "RAG Survey",
                        "publication_year": 2025,
                        "doi": "https://doi.org/10.1000/xyz",
                        "abstract_inverted_index": {"RAG": [0], "survey": [1]},
                    }
                ]
            },
        )

    provider = OpenAlexProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    works = provider.list_works("A123")
    assert len(works) == 1
    work = works[0]
    assert work.title == "RAG Survey"
    assert work.year == 2025
    assert work.abstract == "RAG survey"
    assert work.source_url == "https://doi.org/10.1000/xyz"
    assert work.source_platform == "OpenAlex"
