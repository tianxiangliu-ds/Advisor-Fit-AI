"""LLM 深度匹配与推理：基于学生画像 + 导师证据生成可追溯分析。"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.models.professor import ProfessorProfile
from advisor_fit.models.student import StudentProfile

_ANALYSIS_INSTRUCTIONS = (
    "基于学生画像和导师证据，生成深度匹配分析，分五个部分："
    "research_intersection（研究交集）、method_match（方法能力匹配）、"
    "background_gaps（背景缺口）、recommended_papers（最值得读的论文）、"
    "knowledge_to_supplement（联系前应补的知识）。"
    "每个分析点 text 用中文，fact_ids 只引用 student_facts 里的 id，"
    "evidence_ids 只引用 professor 证据里的 id。不得编造事实。"
)


class AnalysisPoint(BaseModel):
    text: str
    fact_ids: list[str] = []
    evidence_ids: list[str] = []


class DeepAnalysis(BaseModel):
    research_intersection: list[AnalysisPoint] = []
    method_match: list[AnalysisPoint] = []
    background_gaps: list[AnalysisPoint] = []
    recommended_papers: list[AnalysisPoint] = []
    knowledge_to_supplement: list[AnalysisPoint] = []


def generate_deep_analysis(
    llm, student: StudentProfile, professor: ProfessorProfile
) -> DeepAnalysis:
    payload = {
        "student_facts": [
            {"id": f.id, "field": f.field, "value": f.value}
            for f in student.draftable_facts()
        ],
        "professor_topics": [
            {"topic": t.topic, "evidence_ids": t.evidence_ids}
            for t in [*professor.observed_recent_topics, *professor.declared_interests]
        ],
        "publications": [
            {
                "title": p.title,
                "abstract": p.abstract,
                "keywords": p.keywords,
                "source_ids": p.source_ids,
            }
            for p in professor.recent_publications
        ],
    }
    try:
        output = llm.generate(
            schema=DeepAnalysis, instructions=_ANALYSIS_INSTRUCTIONS, payload=payload
        )
    except Exception:  # noqa: BLE001 - LLM 不可用时返回空
        return DeepAnalysis()
    return output if isinstance(output, DeepAnalysis) else DeepAnalysis()


def sanitize_analysis(
    analysis: DeepAnalysis, student: StudentProfile, evidence_map: dict
) -> DeepAnalysis:
    """过滤空文本与无效 fact/evidence 引用，避免 LLM 编造的引用进入展示。"""
    valid_facts = student.confirmed_fact_ids()
    valid_evs = set(evidence_map)

    def clean(points: list[AnalysisPoint]) -> list[AnalysisPoint]:
        result: list[AnalysisPoint] = []
        for point in points:
            if not point.text.strip():
                continue
            result.append(
                AnalysisPoint(
                    text=point.text.strip(),
                    fact_ids=[fid for fid in point.fact_ids if fid in valid_facts],
                    evidence_ids=[eid for eid in point.evidence_ids if eid in valid_evs],
                )
            )
        return result

    return DeepAnalysis(
        research_intersection=clean(analysis.research_intersection),
        method_match=clean(analysis.method_match),
        background_gaps=clean(analysis.background_gaps),
        recommended_papers=clean(analysis.recommended_papers),
        knowledge_to_supplement=clean(analysis.knowledge_to_supplement),
    )


class DirectionSummary(BaseModel):
    summary: str = ""
    topics: list[str] = []
    evidence_ids: list[str] = []


_DIRECTION_INSTRUCTIONS = (
    "根据导师近年论文的标题、摘要和关键词，归纳其研究方向。"
    "summary 用 2-3 句中文概述研究方向；topics 列出 3-5 个具体研究主题；"
    "evidence_ids 只引用 payload 中论文的 source_ids。不得编造。"
)


def generate_direction_summary(llm, professor: ProfessorProfile) -> DirectionSummary:
    publications = [
        {
            "title": p.title,
            "abstract": p.abstract,
            "keywords": p.keywords,
            "source_ids": p.source_ids,
        }
        for p in professor.recent_publications
    ]
    if not publications:
        return DirectionSummary()
    try:
        output = llm.generate(
            schema=DirectionSummary,
            instructions=_DIRECTION_INSTRUCTIONS,
            payload={"publications": publications},
        )
    except Exception:  # noqa: BLE001 - LLM 不可用时返回空
        return DirectionSummary()
    return output if isinstance(output, DirectionSummary) else DirectionSummary()
