"""检索路由器与源登记表测试：按学科分流、跨库合并、缺 Key/反爬/超预算的降级。

全部用 MockTransport 与假 sleeper，不触真实网络、不真的等待。
"""

from __future__ import annotations

import json

import httpx

from advisor_fit.harness.budget import BudgetLimits, BudgetTracker
from advisor_fit.providers.router import SearchRouter
from advisor_fit.providers.sources import (
    DEFAULT_SOURCES,
    SourceSpec,
    classify_sources,
    load_discipline_keywords,
    load_sources,
)

SPECS = (
    SourceSpec(key="openalex", label="OpenAlex", rank=10, min_interval_seconds=0.0),
    SourceSpec(
        key="europepmc",
        label="Europe PMC",
        disciplines=("medicine",),
        rank=5,
        min_interval_seconds=0.0,
    ),
    SourceSpec(key="dblp", label="DBLP", disciplines=("cs",), rank=5, min_interval_seconds=0.0),
    SourceSpec(
        key="wanfang",
        label="万方",
        language="zh",
        requires_key="wanfang_app_key",
        rank=5,
        min_interval_seconds=0.0,
    ),
)


def openalex_client(works: list[dict], authors: list[dict] | None = None) -> httpx.Client:
    """/authors 返回作者实体候选（默认空 → 走"按署名检索 + 核对姓名"），/works 返回论文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authors"):
            return httpx.Response(200, json={"results": authors or []})
        return httpx.Response(200, json={"results": works})

    return httpx.Client(transport=httpx.MockTransport(handler))


def text_client(body: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def failing_client(exc: Exception) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.Client(transport=httpx.MockTransport(handler))


def openalex_work(title: str, authors: tuple[str, ...] = ("王伟",), **overrides) -> dict:
    item = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1000/a",
        "title": title,
        "publication_year": 2024,
        "abstract_inverted_index": {"hello": [0]},
        "authorships": [
            {"author": {"display_name": name}, "institutions": [{"display_name": "WHU"}]}
            for name in authors
        ],
        "primary_location": {"source": {"display_name": "NeurIPS"}},
        "primary_topic": {"field": {"display_name": "Computer Science"}},
    }
    item.update(overrides)
    return item


def europepmc_client(items: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"resultList": {"result": items}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def medicine_item(title: str, doi: str) -> dict:
    return {
        "doi": doi,
        "title": title,
        "pubYear": "2024",
        "abstractText": "medical abstract",
        "authorList": {"author": [{"fullName": "Wei Zhang"}]},
    }


def build_router(**kwargs) -> SearchRouter:
    kwargs.setdefault("specs", SPECS)
    kwargs.setdefault("sleeper", lambda _seconds: None)
    return SearchRouter(**kwargs)


# ---------- 按学科分流 ----------


def test_medicine_professor_queries_medical_source_not_dblp():
    router = build_router(
        clients={"openalex": openalex_client([]), "europepmc": europepmc_client([])}
    )
    router.search_publications("王伟", institution="武汉大学", discipline="medicine")
    keys = {outcome.key for outcome in router.outcomes}
    assert "europepmc" in keys
    assert "dblp" not in keys


def test_cs_professor_queries_dblp():
    router = build_router(clients={"openalex": openalex_client([]), "dblp": openalex_client([])})
    router.search_publications("王伟", institution="武汉大学", discipline="cs")
    searched = {outcome.key for outcome in router.outcomes if outcome.searched}
    assert searched == {"dblp", "openalex"}


def test_discipline_is_learned_from_openalex_results_then_specialist_is_added():
    """用户没选学科时：先查主干库，用结果里的学科标签反推，再补查专业库。

    反推要求至少 3 篇论文支持（避免被个别噪声论文带偏），所以这里给 3 篇。
    """
    medicine_topic = {"field": {"display_name": "Medicine"}}
    router = build_router(
        clients={
            "openalex": openalex_client(
                [
                    openalex_work(
                        "Tumour immunity", doi="10.1/med", primary_topic=medicine_topic
                    ),
                    openalex_work("Cancer therapy", doi="10.1/a", primary_topic=medicine_topic),
                    openalex_work("Clinical trial", doi="10.1/b", primary_topic=medicine_topic),
                ]
            ),
            "europepmc": europepmc_client([medicine_item("Tumour immunity", "10.1/med")]),
        }
    )
    works = router.search_publications("王伟", institution="武汉大学")
    assert router.discipline == "medicine"
    assert router.discipline_text() == "医学 / 生命科学"
    assert "europepmc" in {outcome.key for outcome in router.outcomes}
    # Europe PMC 返回的那篇与 OpenAlex 是同一篇（同一 DOI），合并后 sources 里两个库都在
    merged = next(work for work in works if work.title == "Tumour immunity")
    assert set(merged.sources) == {"OpenAlex", "Europe PMC"}


def test_discipline_is_not_learned_from_a_single_noisy_paper():
    """只有 1 篇噪声论文时不许改学科，否则会把检索分流到错误的专业库。"""
    router = build_router(
        clients={
            "openalex": openalex_client(
                [
                    openalex_work(
                        "Outlier", primary_topic={"field": {"display_name": "Medicine"}}
                    )
                ]
            ),
            "europepmc": europepmc_client([]),
        }
    )
    router.search_publications("王伟", institution="武汉大学")
    assert router.discipline == "general"


def test_keyword_fallback_decides_discipline_without_any_result():
    router = build_router(
        clients={"openalex": openalex_client([]), "europepmc": europepmc_client([])}
    )
    router.search_publications("王伟", institution="武汉大学", known_directions=["临床医学"])
    assert router.discipline == "medicine"
    assert "europepmc" in {outcome.key for outcome in router.outcomes}


# ---------- 降级与容错 ----------


def test_missing_key_source_is_reported_as_skipped_not_failed():
    router = build_router(clients={"openalex": openalex_client([openalex_work("A Paper")])})
    works = router.search_publications("王伟", institution="武汉大学")
    statuses = {outcome.key: outcome.status for outcome in router.outcomes}
    assert statuses["wanfang"] == "no_key"
    assert works and works[0].title == "A Paper"


def test_source_error_does_not_break_the_rest():
    router = build_router(
        clients={
            "openalex": failing_client(httpx.ConnectError("boom")),
            "europepmc": europepmc_client([medicine_item("Kept", "10.1/kept")]),
        }
    )
    works = router.search_publications("王伟", institution="武汉大学", discipline="medicine")
    statuses = {outcome.key: outcome.status for outcome in router.outcomes}
    assert statuses["openalex"] == "error"
    assert [work.title for work in works] == ["Kept"]


def test_blocked_source_reports_the_reason():
    router = build_router(
        clients={
            "openalex": openalex_client([]),
            "dblp": text_client("<html>Making sure you're not a bot!</html>"),
        }
    )
    router.search_publications("王伟", discipline="cs")
    blocked = [outcome for outcome in router.outcomes if outcome.status == "blocked"]
    assert blocked and blocked[0].key == "dblp"
    assert "反爬" in blocked[0].reason or "JSON" in blocked[0].reason


def test_budget_exhaustion_stops_further_sources():
    budget = BudgetTracker(BudgetLimits(max_external_calls=1))
    router = build_router(
        budget=budget,
        clients={
            "openalex": openalex_client([openalex_work("First")]),
            "europepmc": europepmc_client([medicine_item("Second", "10.1/second")]),
        },
    )
    router.search_publications("王伟", discipline="medicine")
    statuses = [
        outcome.status
        for outcome in router.outcomes
        if outcome.key in ("openalex", "europepmc")
    ]
    assert "budget" in statuses
    assert budget.external_calls == 1


def test_router_records_its_own_external_calls():
    """路由器自己记账，Agent 不能再重复计数。"""
    assert SearchRouter.counts_external_calls is True
    budget = BudgetTracker()
    router = build_router(budget=budget, clients={"openalex": openalex_client([])})
    router.search_publications("王伟", institution="武汉大学")
    assert budget.per_source.get("openalex") == 1


def test_rate_limit_sleeps_between_calls_to_the_same_source():
    waits: list[float] = []
    specs = (SourceSpec(key="openalex", label="OpenAlex", rank=10, min_interval_seconds=3.0),)
    ticks = iter([0.0, 0.0, 1.0, 1.0])  # 第二次调用只过了 1 秒，需要补睡 2 秒

    router = SearchRouter(
        specs=specs,
        clients={"openalex": openalex_client([])},
        sleeper=waits.append,
        clock=lambda: next(ticks, 99.0),
    )
    router.search_publications("王伟")
    router.search_publications("王伟")
    assert waits and waits[0] > 0


# ---------- 语言与标题检索 ----------


def test_zh_mode_only_uses_chinese_sources():
    def wanfang_client() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"documents": []})

        return httpx.Client(transport=httpx.MockTransport(handler))

    router = build_router(
        key_values={"wanfang_app_key": "k"},
        clients={"openalex": openalex_client([]), "wanfang": wanfang_client()},
    )
    router.search_publications("王伟", source="zh")
    statuses = {outcome.key: outcome.status for outcome in router.outcomes}
    assert statuses["openalex"] == "language"
    assert statuses["wanfang"] == "empty"
    assert "openalex" not in {outcome.key for outcome in router.outcomes if outcome.searched}


def test_title_search_merges_across_sources():
    router = build_router(
        clients={
            "openalex": openalex_client([openalex_work("Same Paper")]),
            "europepmc": europepmc_client([medicine_item("Same Paper", "10.1000/a")]),
        }
    )
    works = router.search_by_title("Same Paper", discipline="medicine")
    assert len(works) == 1
    assert set(works[0].sources) == {"OpenAlex", "Europe PMC"}


def test_scope_label_lists_available_sources():
    router = build_router()
    assert "OpenAlex" in router.scope_label("auto")
    assert "Europe PMC" in router.scope_label("auto", "medicine")


def test_describe_summarizes_what_was_searched():
    router = build_router(
        clients={
            "openalex": openalex_client([openalex_work("A Paper")]),
            "europepmc": europepmc_client([]),
        }
    )
    router.search_publications("王伟", discipline="medicine")
    text = router.describe()
    assert "OpenAlex 1 篇" in text


def test_describe_reports_blocked_sources_with_reason():
    router = build_router(
        clients={
            "openalex": openalex_client([]),
            "dblp": text_client("<html>Making sure you're not a bot!</html>"),
        }
    )
    router.search_publications("王伟", discipline="cs")
    assert "未返回" in router.describe()


# ---------- 源登记表 = 配置 ----------


def test_source_config_file_can_override_and_extend(tmp_path):
    config = tmp_path / "sources.json"
    config.write_text(
        json.dumps(
            {
                "sources": [
                    {"key": "openalex", "rank": 99, "enabled": False},
                    {"key": "semanticscholar", "label": "Semantic Scholar", "rank": 7},
                ]
            }
        ),
        encoding="utf-8",
    )
    specs = load_sources(config)
    by_key = {spec.key: spec for spec in specs}
    assert by_key["openalex"].rank == 99
    assert by_key["openalex"].enabled is False
    assert by_key["semanticscholar"].label == "Semantic Scholar"
    # 没写到的字段沿用内置默认
    assert by_key["dblp"] == next(spec for spec in DEFAULT_SOURCES if spec.key == "dblp")


def test_broken_config_falls_back_to_defaults(tmp_path):
    config = tmp_path / "sources.json"
    config.write_text("{ this is not json", encoding="utf-8")
    assert load_sources(config) == DEFAULT_SOURCES


def test_config_can_override_discipline_keywords(tmp_path):
    config = tmp_path / "sources.json"
    config.write_text(
        json.dumps({"discipline_keywords": {"cs": ["提示工程"]}}), encoding="utf-8"
    )
    assert load_discipline_keywords(config) == {"cs": ["提示工程"]}


def test_classify_sources_reports_why_a_source_was_skipped():
    selected, notes = classify_sources("cs", specs=SPECS, key_values={}, language="auto")
    selected_keys = [spec.key for spec in selected]
    assert "dblp" in selected_keys
    reasons = {spec.key: reason for spec, reason in notes}
    assert reasons["wanfang"] == "no_key"
    assert "europepmc" not in selected_keys


def test_default_registry_ships_free_sources_and_keeps_paid_ones_off():
    by_key = {spec.key: spec for spec in DEFAULT_SOURCES}
    # 默认主干必须全部免费免 Key，否则新用户开箱用不了
    for key in ("openalex", "crossref", "europepmc", "arxiv"):
        assert by_key[key].cost_per_call == 0.0
        assert by_key[key].requires_key == ""
        assert by_key[key].enabled is True
    # 需要申请 Key 与收费的源不能默认打开
    assert by_key["aminer"].enabled is False
    assert by_key["wanfang"].requires_key == "wanfang_app_key"
    # DBLP 目前被反爬拦下，默认关闭但保留配置入口
    assert by_key["dblp"].enabled is False
    assert by_key["dblp"].disciplines == ("cs",)
