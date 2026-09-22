"""匹配环节的向量召回测试。

关键词匹配有个硬伤：学生写"实体抽取与知识图谱"、导师写"知识图谱与实体抽取"，
字面顺序不同就不算命中，于是报告里出现一个并不存在的"未覆盖"缺口。

这里补的正是那一段：**字面没命中时，用向量再捞一次**。三条底线：

1. 不传 `embedder` 时行为与从前**完全一致**（纯增量，不偷偷改变既有结论）；
2. 向量召回归 PARTIAL，**不能**算 STRONG——用词相近的证据强度弱于明确命中；
3. 措辞随后端能力变：真模型叫「语义相近」，离线档只叫「字面相近」。
"""

from __future__ import annotations

from advisor_fit.analysis.matching import build_match_report
from advisor_fit.models.common import FactStatus
from advisor_fit.models.match import FitLevel
from advisor_fit.models.professor import ObservedTopic, ProfessorProfile
from advisor_fit.models.student import StudentFact, StudentProfile
from advisor_fit.providers.embedding import HashingEmbedder


def _professor(topic: str = "知识图谱与实体抽取") -> ProfessorProfile:
    return ProfessorProfile(
        professor_id="p1",
        identity_confirmed=True,
        declared_interests=[ObservedTopic(topic=topic, trend="DECLARED", evidence_ids=["ev1"])],
    )


def _student(value: str, field: str = "interest") -> StudentProfile:
    return StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(id="f1", field=field, value=value,
                        status=FactStatus.FACT, user_confirmed=True)
        ],
    )


def test_without_an_embedder_nothing_changes():
    """没有向量后端时，结论必须与从前逐字一致。"""
    report = build_match_report(_student("实体抽取与知识图谱"), _professor())

    assert report.research_fit is FitLevel.WEAK
    assert report.gaps == ["知识图谱与实体抽取"]
    assert [d.key for d in report.dimensions] == ["research_topic", "skill", "evidence"]


def test_vector_recall_finds_overlap_the_keywords_missed():
    report = build_match_report(
        _student("实体抽取与知识图谱"), _professor(), embedder=HashingEmbedder()
    )

    assert report.research_fit is FitLevel.PARTIAL
    assert report.gaps == [], "用词不同但意思相同，不该报成未覆盖"
    assert "surface" in [d.key for d in report.dimensions]


def test_vector_recall_never_claims_a_strong_fit():
    """用词相近弱于明确命中，最多算 PARTIAL，不能进 strengths。"""
    report = build_match_report(
        _student("实体抽取与知识图谱"), _professor(), embedder=HashingEmbedder()
    )

    assert report.research_fit is not FitLevel.STRONG
    assert all(d.key != "surface" for d in report.strengths)


def test_literal_match_needs_no_vector_help():
    """字面已经命中时，不该再掺一条向量理由。

    否则同一份交集会被说两遍，用户无从判断哪条是硬证据。
    （兴趣命中本就是 PARTIAL、技能命中才是 STRONG，这是既有设计，不由向量层改变。）
    """
    report = build_match_report(
        _student("知识图谱与实体抽取"), _professor(), embedder=HashingEmbedder()
    )

    assert report.research_fit is FitLevel.PARTIAL
    assert "surface" not in [d.key for d in report.dimensions]
    assert report.gaps == []


def test_skill_match_reaches_strong_without_the_vector_layer():
    """技能命中走原路到 STRONG——向量层不该把既有结论改弱。"""
    report = build_match_report(
        _student("知识图谱与实体抽取", field="skill"), _professor(),
        embedder=HashingEmbedder(),
    )

    assert report.research_fit is FitLevel.STRONG
    assert "surface" not in [d.key for d in report.dimensions]


def test_offline_tier_is_labelled_as_surface_not_semantic():
    """红线：离线档只做表层重合，不许说成"语义理解"。"""
    report = build_match_report(
        _student("实体抽取与知识图谱"), _professor(), embedder=HashingEmbedder()
    )
    dimension = next(d for d in report.dimensions if d.key in ("semantic", "surface"))

    assert dimension.label == "字面相近"
    assert "用词相近" in dimension.summary


def test_true_semantic_backend_gets_the_semantic_wording():
    class FakeSemantic(HashingEmbedder):
        @property
        def name(self) -> str:
            return "fake-semantic"

        @property
        def semantic(self) -> bool:
            return True

    report = build_match_report(
        _student("实体抽取与知识图谱"), _professor(), embedder=FakeSemantic()
    )
    dimension = next(d for d in report.dimensions if d.key in ("semantic", "surface"))

    assert dimension.key == "semantic"
    assert dimension.label == "语义相近"


def test_unrelated_student_gains_nothing_from_the_vector_layer():
    """向量召回不能把不相干的学生也算成交集。"""
    report = build_match_report(
        _student("油画创作与色彩理论"), _professor(), embedder=HashingEmbedder()
    )

    assert report.research_fit is FitLevel.WEAK
    assert report.gaps == ["知识图谱与实体抽取"]


def test_vector_dimension_carries_the_evidence_it_rests_on():
    """向量结论也要能追溯——没有证据的交集不该进报告。"""
    report = build_match_report(
        _student("实体抽取与知识图谱"), _professor(), embedder=HashingEmbedder()
    )
    dimension = next(d for d in report.dimensions if d.key in ("semantic", "surface"))

    assert dimension.student_fact_ids == ["f1"]
    assert dimension.professor_evidence_ids == ["ev1"]


def test_broken_vector_backend_degrades_to_keyword_only():
    """向量服务挂了不能影响匹配——它只是补充。"""
    class Broken(HashingEmbedder):
        def embed(self, texts):
            raise RuntimeError("服务不可用")

    report = build_match_report(
        _student("实体抽取与知识图谱"), _professor(), embedder=Broken()
    )

    assert report.research_fit is FitLevel.WEAK
    assert report.gaps == ["知识图谱与实体抽取"]
