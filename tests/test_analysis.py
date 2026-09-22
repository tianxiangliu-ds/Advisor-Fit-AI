"""LLM 深度匹配分析测试。"""

import advisor_fit.llm.analysis as analysis_module
from advisor_fit.llm.analysis import (
    AnalysisPoint,
    DeepAnalysis,
    DirectionSummary,
    generate_deep_analysis,
    generate_direction_summary,
    sanitize_analysis,
)
from advisor_fit.models.common import FactStatus
from advisor_fit.models.evidence import Evidence
from advisor_fit.models.professor import ProfessorProfile, RecentPublication
from advisor_fit.models.student import Education, Project, Publication, StudentFact, StudentProfile


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


def test_deep_analysis_only_receives_confirmed_student_facts():
    """若未确认的结构化经历进入匹配模型，这个测试会失败。"""

    class CapturingLLM:
        payload = None

        def generate(self, *, schema, instructions, payload):
            self.payload = payload
            return DeepAnalysis()

    student = _student().model_copy(
        update={
            "education": [Education(institution="未确认学校")],
            "projects": [Project(name="未确认项目")],
            "publications": [Publication(title="未确认论文")],
        }
    )
    llm = CapturingLLM()

    generate_deep_analysis(llm, student, ProfessorProfile(professor_id="p1"))

    assert llm.payload is not None
    assert "student_education" not in llm.payload
    assert "student_projects" not in llm.payload
    assert "student_publications" not in llm.payload
    assert [fact["id"] for fact in llm.payload["student_facts"]] == ["f1"]


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


def test_sanitize_analysis_removes_claims_that_lost_required_grounding():
    """若模型文案失去学生事实或导师证据后仍被展示，这个测试会失败。"""
    evidences = {"ev1": Evidence(id="ev1", source_type="user_confirmed_paper")}
    analysis = DeepAnalysis(
        research_intersection=[
            AnalysisPoint(text="没有学生依据的交集", evidence_ids=["ev1"]),
            AnalysisPoint(text="有双向依据的交集", fact_ids=["f1"], evidence_ids=["ev1"]),
        ],
        recommended_papers=[
            AnalysisPoint(text="没有论文依据的推荐"),
            AnalysisPoint(text="有论文依据的推荐", evidence_ids=["ev1"]),
        ],
    )

    cleaned = sanitize_analysis(analysis, _student(), evidences)

    assert [point.text for point in cleaned.research_intersection] == ["有双向依据的交集"]
    assert [point.text for point in cleaned.recommended_papers] == ["有论文依据的推荐"]


def test_generate_direction_summary_returns_output():
    llm = FakeLLM(
        DirectionSummary(summary="研究方向是知识图谱", topics=["知识图谱"], evidence_ids=["ev1"])
    )
    professor = ProfessorProfile(
        professor_id="p1",
        recent_publications=[RecentPublication(id="w1", title="T", source_ids=["ev1"])],
    )
    result = generate_direction_summary(llm, professor)
    assert result.summary == "研究方向是知识图谱"
    assert result.topics == ["知识图谱"]


def test_generate_direction_summary_empty_without_publications():
    result = generate_direction_summary(
        FakeLLM(DirectionSummary()), ProfessorProfile(professor_id="p1")
    )
    assert result.summary == ""


def test_direction_summary_is_hidden_when_all_evidence_refs_are_invalid():
    """若研究方向没有任何有效论文依据却仍显示，这个测试会失败。"""
    summary = DirectionSummary(
        summary="没有真实来源的归纳",
        topics=["虚构方向"],
        evidence_ids=["ghost"],
    )

    assert analysis_module.sanitize_direction_summary(summary, {"ev1"}) == DirectionSummary()
