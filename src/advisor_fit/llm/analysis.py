"""LLM 深度匹配与推理：基于学生画像 + 导师证据生成可追溯分析。"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.llm.prompts import prompt_text
from advisor_fit.models.professor import ProfessorProfile
from advisor_fit.models.student import StudentProfile

_ANALYSIS_INSTRUCTIONS = prompt_text("deep_analysis")


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

    def clean(
        points: list[AnalysisPoint], *, require_fact: bool = False,
        require_evidence: bool = False,
    ) -> list[AnalysisPoint]:
        result: list[AnalysisPoint] = []
        for point in points:
            if not point.text.strip():
                continue
            cleaned = AnalysisPoint(
                text=point.text.strip(),
                fact_ids=[fid for fid in point.fact_ids if fid in valid_facts],
                evidence_ids=[eid for eid in point.evidence_ids if eid in valid_evs],
            )
            if require_fact and not cleaned.fact_ids:
                continue
            if require_evidence and not cleaned.evidence_ids:
                continue
            result.append(cleaned)
        return result

    return DeepAnalysis(
        research_intersection=clean(
            analysis.research_intersection, require_fact=True, require_evidence=True
        ),
        method_match=clean(
            analysis.method_match, require_fact=True, require_evidence=True
        ),
        background_gaps=clean(analysis.background_gaps, require_evidence=True),
        recommended_papers=clean(analysis.recommended_papers, require_evidence=True),
        knowledge_to_supplement=clean(
            analysis.knowledge_to_supplement, require_evidence=True
        ),
    )


class DirectionSummary(BaseModel):
    summary: str = ""
    topics: list[str] = []
    evidence_ids: list[str] = []


_DIRECTION_INSTRUCTIONS = prompt_text("direction_summary")


def sanitize_direction_summary(
    summary: DirectionSummary, valid_evidence_ids: set[str]
) -> DirectionSummary:
    """没有有效论文引用时不展示研究方向归纳。"""
    evidence_ids = [
        evidence_id
        for evidence_id in summary.evidence_ids
        if evidence_id in valid_evidence_ids
    ]
    if not summary.summary.strip() or not evidence_ids:
        return DirectionSummary()
    return summary.model_copy(update={"evidence_ids": evidence_ids})


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
