"""LLM 深度匹配分析测试。"""

from advisor_fit.llm.analysis import (
    AnalysisPoint,
    DeepAnalysis,
    generate_deep_analysis,
    sanitize_analysis,
)
from advisor_fit.models.common import FactStatus
from advisor_fit.models.evidence import Evidence
from advisor_fit.models.professor import ProfessorProfile
from advisor_fit.models.student import StudentFact, StudentProfile


class FakeLLM:
    def __init__(self, output):
        self._output = output

    def generate(self, *, schema, instructions, payload):
        return self._output


class UnavailableLLM:
    def generate(self, **kwargs):
        raise RuntimeError("no llm")


def _student():
    return StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(
                id="f1", field="skill", value="Python",
                status=FactStatus.FACT, user_confirmed=True,
            )
        ],
    )


def test_generate_deep_analysis_returns_output():
    llm = FakeLLM(
        DeepAnalysis(research_intersection=[AnalysisPoint(text="交集一")])
    )
    result = generate_deep_analysis(llm, _student(), ProfessorProfile(professor_id="p1"))
    assert result.research_intersection[0].text == "交集一"


def test_generate_deep_analysis_empty_when_unavailable():
    result = generate_deep_analysis(
        UnavailableLLM(), _student(), ProfessorProfile(professor_id="p1")
    )
    assert result.research_intersection == []


def test_sanitize_analysis_drops_invalid_refs():
    evidences = {"ev1": Evidence(id="ev1", source_type="user_confirmed_paper")}
    analysis = DeepAnalysis(
        method_match=[
            AnalysisPoint(text="方法匹配", fact_ids=["f1", "ghost"], evidence_ids=["ev1", "nope"]),
            AnalysisPoint(text="   "),
        ]
    )
    cleaned = sanitize_analysis(analysis, _student(), evidences)
    point = cleaned.method_match[0]
    assert point.fact_ids == ["f1"]
    assert point.evidence_ids == ["ev1"]
