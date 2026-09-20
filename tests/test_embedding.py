"""向量后端与语义召回测试。

三条主线：
1. **后端可替换且能降级**：默认档零依赖、离线、确定性；配了接口才升级。
2. **门槛跟着后端走**：离线档与真模型的分值尺度差很远。实测 1200 位真实导师，
   离线档最高相似度只有 0.33~0.41，用一套通用阈值会让它的"仅向量召回"永远触发不了。
3. **措辞不许夸大**：离线档只能叫「字面相近」，配了真模型才配叫「语义相近」。
   这是项目的红线——理由必须说得清，不能把表层重合包装成语义理解。
"""

from __future__ import annotations

import pytest

from advisor_fit.analysis.direction_search import rank_candidates
from advisor_fit.analysis.semantic import advisor_document, semantic_scores
from advisor_fit.models.advisor import Advisor
from advisor_fit.providers.embedding import (
    ApiEmbedder,
    HashingEmbedder,
    build_embedder,
    cosine,
)

# -- 默认档 --------------------------------------------------------------------


def test_hashing_embedder_is_deterministic():
    """同输入必须同输出——否则缓存、排序、测试都无从谈起。"""
    embedder = HashingEmbedder()

    first = embedder.embed(["知识图谱", "图神经网络"])
    second = embedder.embed(["知识图谱", "图神经网络"])

    assert first == second


def test_hashing_embedder_produces_normalised_vectors():
    vectors = HashingEmbedder(dim=64).embed(["知识图谱与实体抽取"])

    norm = sum(value * value for value in vectors[0]) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_hashing_embedder_does_not_claim_to_be_semantic():
    """它是表层相似，不是同义词理解。这个属性决定界面上怎么措辞。"""
    assert HashingEmbedder().semantic is False


def test_hashing_embedder_scores_related_text_higher_than_unrelated():
    embedder = HashingEmbedder()

    query, related, unrelated = embedder.embed(
        ["知识图谱与实体抽取", "实体抽取与知识图谱", "油画创作与色彩理论"]
    )

    assert cosine(query, related) > cosine(query, unrelated)


def test_hashing_embedder_separates_scores_clearly():
    """相关与不相关之间要有明显区分度，否则门槛无从设起。"""
    embedder = HashingEmbedder()

    query, related, unrelated = embedder.embed(
        ["知识图谱", "知识图谱", "油画"]
    )
    assert cosine(query, related) == pytest.approx(1.0)
    assert cosine(query, unrelated) == pytest.approx(0.0)


def test_empty_text_yields_a_zero_vector_instead_of_nan():
    vector = HashingEmbedder(dim=16).embed([""])[0]

    assert all(value == 0 for value in vector)
    assert cosine(vector, vector) == 0.0


def test_cosine_handles_mismatched_dimensions():
    assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0
    assert cosine([], [1.0]) == 0.0


# -- 后端选择 ------------------------------------------------------------------


def test_build_embedder_defaults_to_the_offline_tier():
    """什么都不配时必须能跑，且不下载任何东西。"""
    class NoConfig:
        pass

    assert isinstance(build_embedder(settings_obj=NoConfig()), HashingEmbedder)


def test_build_embedder_uses_api_when_configured():
    class Configured:
        embedding_base_url = "https://api.example.com/v1"
        embedding_api_key = "sk-x"
        embedding_model = "text-embedding-3-small"
        embedding_local_model = ""

    class FakeClient:
        headers: dict = {}

    embedder = build_embedder(client=FakeClient(), settings_obj=Configured())

    assert isinstance(embedder, ApiEmbedder)
    assert embedder.name == "api:text-embedding-3-small"
    assert embedder.semantic is True


def test_build_embedder_falls_back_when_the_local_model_is_missing():
    """没装 sentence-transformers 时退回离线档，而不是报错。"""
    class WantsLocal:
        embedding_base_url = ""
        embedding_api_key = ""
        embedding_model = ""
        embedding_local_model = "some-model-that-is-not-installed"

    assert isinstance(build_embedder(settings_obj=WantsLocal()), HashingEmbedder)


def test_thresholds_are_calibrated_per_backend():
    """真模型的分值尺度更高，门槛必须跟着抬——否则某一档会彻底失效。"""
    offline = HashingEmbedder()

    assert offline.recall_threshold > offline.reason_threshold
    # 离线档实测最高只有 0.41，门槛不能高过它
    assert offline.recall_threshold < 0.41


def test_api_embedder_declares_higher_thresholds_than_offline():
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

    class FakeClient:
        headers: dict = {}
        def post(self, url, json): return FakeResponse()

    api = ApiEmbedder(client=FakeClient(), model="m", base_url="https://x/v1")

    assert api.recall_threshold > HashingEmbedder().recall_threshold


# -- 文本组织 ------------------------------------------------------------------


def test_advisor_document_uses_direction_fields():
    advisor = Advisor(
        university="U", department="计算机学院", name="甲",
        research_directions=["知识图谱"], research_areas=["实体抽取"],
        publications=["一篇论文"], profile_text="这里是一大段套话" * 50,
    )

    document = advisor_document(advisor)

    assert "知识图谱" in document
    assert "实体抽取" in document
    assert "计算机学院" in document
    assert "一篇论文" in document


def test_advisor_document_excludes_profile_text():
    """官网简介里常有大段套话，放进来会让所有导师看起来都差不多。"""
    advisor = Advisor(
        university="U", department="D", name="甲",
        research_directions=["知识图谱"], profile_text="独一无二的套话内容",
    )

    assert "独一无二的套话内容" not in advisor_document(advisor)


def test_semantic_scores_survive_a_broken_embedder():
    """向量服务挂了不该让检索失败——语义只是补充能力。"""
    class Broken:
        @property
        def name(self): return "broken"
        @property
        def dim(self): return 0
        @property
        def semantic(self): return True
        @property
        def reason_threshold(self): return 0.4
        @property
        def recall_threshold(self): return 0.6
        def embed(self, texts): raise RuntimeError("服务不可用")

    advisors = [Advisor(university="U", department="D", name="甲",
                        research_directions=["知识图谱"])]

    assert semantic_scores("知识图谱", advisors, Broken()) == [0.0]


def test_semantic_scores_are_empty_for_blank_query():
    advisors = [Advisor(university="U", department="D", name="甲")]

    assert semantic_scores("   ", advisors, HashingEmbedder()) == [0.0]


# -- 接进排序 ------------------------------------------------------------------


def _advisors() -> list[Advisor]:
    return [
        Advisor(university="U", department="计算机学院", name="甲",
                research_directions=["实体抽取与知识图谱"], title="教授"),
        Advisor(university="U", department="文学院", name="乙",
                research_directions=["数字人文", "古籍整理"], title="教授"),
    ]


def test_ranking_is_unchanged_without_an_embedder():
    """不传向量后端时行为与从前完全一致——这是一条纯增量的分支。"""
    hits = rank_candidates(_advisors(), ["知识图谱"], limit=5)

    assert [hit.advisor.name for hit in hits] == ["甲"]
    assert hits[0].has_semantic_reason is False
    assert hits[0].semantic_score == 0.0


def test_keyword_hits_gain_a_vector_reason():
    hits = rank_candidates(_advisors(), ["知识图谱"], limit=5, embedder=HashingEmbedder())

    assert hits[0].has_semantic_reason is True
    assert hits[0].semantic_score > 0
    assert hits[0].match_score >= 3, "关键词分不应被向量理由改变"


def test_offline_tier_wording_is_surface_not_semantic():
    """红线：离线档只做表层相似，不许说成"语义理解"。"""
    hits = rank_candidates(_advisors(), ["知识图谱"], limit=5, embedder=HashingEmbedder())

    assert "字面相近" in hits[0].reason_text
    assert "语义相近" not in hits[0].reason_text


def test_true_semantic_backend_gets_the_semantic_wording():
    class FakeSemantic(HashingEmbedder):
        @property
        def name(self): return "fake-semantic"
        @property
        def semantic(self): return True

    hits = rank_candidates(_advisors(), ["知识图谱"], limit=5, embedder=FakeSemantic())

    assert "语义相近" in hits[0].reason_text


def test_vector_recall_can_surface_something_keywords_missed():
    """这套机制存在的意义：捞回"没命中字面但很接近"的候选。"""
    class AlwaysSimilar:
        @property
        def name(self): return "always"
        @property
        def dim(self): return 2
        @property
        def semantic(self): return True
        @property
        def reason_threshold(self): return 0.4
        @property
        def recall_threshold(self): return 0.6
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    hits = rank_candidates(_advisors(), ["完全不相干的词"], limit=5, embedder=AlwaysSimilar())

    assert hits, "相似度高时应当召回，即使关键词一个都没中"
    assert all(hit.match_score == 0 for hit in hits)
    assert hits[0].semantic_score == pytest.approx(1.0)


def test_low_similarity_does_not_recall_anything():
    """门槛必须真的拦得住——否则向量召回会变成噪声源。"""
    class WeaklySimilar:
        @property
        def name(self): return "weak"
        @property
        def dim(self): return 2
        @property
        def semantic(self): return True
        @property
        def reason_threshold(self): return 0.4
        @property
        def recall_threshold(self): return 0.6
        def embed(self, texts):
            # query 与文档只共享一点点方向，余弦约 0.5，低于召回门槛
            return [[1.0, 0.0]] + [[0.5, 0.866] for _ in texts[1:]]

    hits = rank_candidates(_advisors(), ["完全不相干的词"], limit=5, embedder=WeaklySimilar())

    assert hits == []


def test_recall_threshold_takes_precedence_over_the_nudge_threshold():
    """两道门槛有先后：相似度够高时走"直接召回"那条，补理由那条管不着它。

    所以要压住向量理由，得抬 `min_semantic_only`（召回门槛），而不是 `min_similarity`。
    """
    embedder = HashingEmbedder()

    # 只抬补理由的门槛：相似度已经高过召回门槛，理由照样会出现
    nudge_only = rank_candidates(
        _advisors(), ["知识图谱"], limit=5,
        embedder=embedder, min_similarity=0.99,
    )
    assert nudge_only[0].has_semantic_reason is True

    # 抬召回门槛：两条路都进不去，理由才真的消失
    both = rank_candidates(
        _advisors(), ["知识图谱"], limit=5,
        embedder=embedder, min_similarity=0.99, min_semantic_only=0.99,
    )
    assert both[0].has_semantic_reason is False


def test_backend_thresholds_are_used_by_default():
    """不显式传门槛时，用后端自己声明的那套。"""
    embedder = HashingEmbedder()
    hits = rank_candidates(
        _advisors(), ["知识图谱"], limit=5,
        embedder=embedder,
        min_semantic_only=0.0,   # 强制走"直接召回"那条路
    )

    assert hits[0].has_semantic_reason is True
    assert hits[0].match_score >= 3, "关键词分仍照常计算"


def test_vector_reason_never_outranks_a_real_keyword_hit():
    """向量是补充，不能盖过关键词——否则排序就不再可解释了。"""
    advisors = [
        Advisor(university="U", department="D", name="关键词命中",
                research_directions=["知识图谱"]),
        Advisor(university="U", department="D", name="只有向量相近",
                research_directions=["图谱知识"]),
    ]
    hits = rank_candidates(advisors, ["知识图谱"], limit=5, embedder=HashingEmbedder())

    assert hits[0].advisor.name == "关键词命中"
