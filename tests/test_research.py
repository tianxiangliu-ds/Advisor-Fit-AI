"""导师研究 Agent 测试（不触真实网络/LLM）。"""

from advisor_fit.agents.research import (
    ResearchResult,
    _institutions_conflict,
    _merge_search_results,
    _rule_disambiguate,
    disambiguate_papers,
    research_professor,
)
from advisor_fit.harness.loop import AgentDecision
from advisor_fit.llm.provider import NullLLM


class FakeWork:
    def __init__(self, title, year=2020, institution="", authors=None):
        self.title = title
        self.year = year
        self.abstract = "摘要"
        self.source_url = "https://example.com/p"
        self.source_platform = "万方"
        self.topics = []
        self.authors = authors or []
        self.institution = institution
        self.venue = ""


class FakeProvider:
    def __init__(self, works=None):
        self.works = works or []
        self.calls = []

    def search_publications(self, name, *, institution=None, source="zh", limit=20):
        self.calls.append((name, institution, source))
        return self.works


class FakeLLM:
    """按顺序返回决策；用于检索循环。"""

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def generate(self, *, schema, instructions, payload):
        return self.decisions.pop(0)


class VerdictLLM:
    """只处理消歧：返回给每篇论文的 verdict。"""

    def __init__(self, verdicts):
        self.verdicts = verdicts

    def generate(self, *, schema, instructions, payload):
        from advisor_fit.agents.research import DisambiguationOutput

        return DisambiguationOutput(verdicts=self.verdicts)


def test_institutions_conflict():
    assert _institutions_conflict("武汉大学信息管理学院", "武汉大学") is False
    assert _institutions_conflict("中国药科大学", "武汉大学") is True
    assert _institutions_conflict("", "武汉大学") is False
    assert _institutions_conflict("武汉大学", None) is False


def test_rule_disambiguate_marks_conflicting_institution():
    papers = [
        {"title": "A", "institution": "武汉大学", "belongs": True, "disambig_reason": ""},
        {"title": "B", "institution": "中国药科大学", "belongs": True, "disambig_reason": ""},
        {"title": "C", "institution": "", "belongs": True, "disambig_reason": ""},
    ]
    result = _rule_disambiguate(papers, "武汉大学")
    assert result[0]["belongs"] is True
    assert result[1]["belongs"] is False
    assert result[2]["belongs"] is True


def test_disambiguate_papers_uses_llm_verdicts():
    from advisor_fit.agents.research import PaperVerdict

    papers = [
        {"title": "A", "institution": "武汉大学", "belongs": True, "disambig_reason": ""},
        {"title": "B", "institution": "中国药科大学", "belongs": True, "disambig_reason": ""},
    ]
    llm = VerdictLLM(
        [PaperVerdict(index=0, belongs=True, reason="机构匹配"),
         PaperVerdict(index=1, belongs=False, reason="同名作者")]
    )
    result = disambiguate_papers(llm, papers, professor_name="陆伟", institution="武汉大学")
    assert result[0]["belongs"] is True
    assert result[1]["belongs"] is False
    assert result[1]["disambig_reason"] == "同名作者"


def test_disambiguate_papers_falls_back_to_rule_without_llm():
    papers = [
        {"title": "A", "institution": "武汉大学", "belongs": True, "disambig_reason": ""},
        {"title": "B", "institution": "中国药科大学", "belongs": True, "disambig_reason": ""},
    ]
    result = disambiguate_papers(
        NullLLM(), papers, professor_name="陆伟", institution="武汉大学"
    )
    assert result[1]["belongs"] is False


def test_merge_search_results_dedupes_by_title():
    steps = [
        {"tool": "search_publications", "result": {"papers": [{"title": "A"}, {"title": "B"}]}},
        {"tool": "search_publications", "result": {"papers": [{"title": "B"}, {"title": "C"}]}},
    ]
    merged = _merge_search_results(steps)
    assert [p["title"] for p in merged] == ["A", "B", "C"]


def test_research_professor_without_llm_searches_and_disambiguates():
    provider = FakeProvider(
        [
            FakeWork("匹配论文", institution="武汉大学"),
            FakeWork("同名论文", institution="中国药科大学"),
        ]
    )
    result = research_professor(
        NullLLM(), provider, name="陆伟", institution="武汉大学", source="zh"
    )
    assert len(result.papers) == 2
    assert result.papers[0]["belongs"] is True
    assert result.papers[1]["belongs"] is False
    assert result.needs_confirmation == ""


def test_research_professor_surfaces_confirmation_gate():
    provider = FakeProvider([FakeWork("一篇论文", institution="武汉大学")])
    llm = FakeLLM([AgentDecision(action="confirm", message="请确认是否只保留武汉大学的论文")])
    result = research_professor(
        llm, provider, name="陆伟", institution="武汉大学", source="zh"
    )
    assert result.needs_confirmation == "请确认是否只保留武汉大学的论文"


def test_research_professor_runs_loop_and_disambiguates():
    from advisor_fit.agents.research import DisambiguationOutput, PaperVerdict

    provider = FakeProvider(
        [FakeWork("匹配论文", institution="武汉大学"),
         FakeWork("同名论文", institution="中国药科大学")]
    )

    class BothLLM:
        def __init__(self):
            self.queue = [
                AgentDecision(action="tool", tool="search_publications", args={"name": "陆伟"}),
                AgentDecision(action="done", message="检索完成"),
                DisambiguationOutput(
                    verdicts=[
                        PaperVerdict(index=0, belongs=True),
                        PaperVerdict(index=1, belongs=False, reason="同名"),
                    ]
                ),
            ]

        def generate(self, *, schema, instructions, payload):
            return self.queue.pop(0)

    result = research_professor(BothLLM(), provider, name="陆伟", institution="武汉大学")
    assert len(result.papers) == 2
    assert result.papers[1]["belongs"] is False


def test_research_result_is_dataclass():
    result = ResearchResult()
    assert result.papers == []
    assert result.needs_confirmation == ""


def test_research_professor_seeds_institution_first():
    provider = FakeProvider([FakeWork("匹配论文", institution="武汉大学")])
    llm = FakeLLM([AgentDecision(action="done", message="完成")])
    research_professor(llm, provider, name="陆伟", institution="武汉大学", source="zh")
    assert provider.calls[0] == ("陆伟", "武汉大学", "zh")
