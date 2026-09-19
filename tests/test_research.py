"""导师研究 Agent 测试（不触真实网络/LLM）。"""

from advisor_fit.agents.research import (
    ResearchResult,
    _institutions_conflict,
    _investigate_affiliations,
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

    def search_by_title(self, title, *, source="zh", limit=5):
        self.calls.append(("title", title, source))
        return []


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


def test_rule_disambiguate_marks_conflicting_institution_for_review():
    papers = [
        {"title": "A", "institution": "武汉大学", "belongs": True,
         "needs_review": False, "disambig_reason": ""},
        {"title": "B", "institution": "中国药科大学", "belongs": True,
         "needs_review": False, "disambig_reason": ""},
        {"title": "C", "institution": "", "belongs": True,
         "needs_review": False, "disambig_reason": ""},
    ]
    result = _rule_disambiguate(papers, "武汉大学")
    assert result[0]["belongs"] is True
    assert result[0]["needs_review"] is False
    assert result[1]["belongs"] is True
    assert result[1]["needs_review"] is True
    assert result[2]["needs_review"] is False


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
    assert result[1]["belongs"] is True
    assert result[1]["needs_review"] is True


def test_merge_search_results_dedupes_by_title():
    steps = [
        {"tool": "search_by_author", "result": {"papers": [{"title": "A"}, {"title": "B"}]}},
        {"tool": "search_by_title", "result": {"papers": [{"title": "B"}, {"title": "C"}]}},
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
    assert result.papers[0]["needs_review"] is False
    assert result.papers[1]["belongs"] is True
    assert result.papers[1]["needs_review"] is True
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
                AgentDecision(action="tool", tool="search_by_author", args={"name": "陆伟"}),
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


def test_research_professor_searches_alternative_institution():
    provider = FakeProvider([FakeWork("匹配论文", institution="西南财经大学")])
    llm = FakeLLM([AgentDecision(action="done", message="完成")])
    research_professor(
        llm, provider, name="林华珍", institution="武汉大学",
        search_institution="西南财经大学", source="zh",
    )
    assert ("林华珍", "西南财经大学", "zh") in provider.calls
    assert ("林华珍", "武汉大学", "zh") in provider.calls


def test_investigate_affiliations_adds_previous_institution_papers():
    provider = FakeProvider([FakeWork("历史论文", institution="中国地质大学（武汉）")])
    papers = [
        {
            "title": "需审核论文",
            "institution": "中国地质大学（武汉）",
            "belongs": True,
            "needs_review": True,
            "disambig_reason": "机构不同",
            "affiliation_note": "",
        }
    ]
    result = _investigate_affiliations(
        provider, "李祖超", papers, "武汉大学", "zh", None
    )
    titles = [p["title"] for p in result]
    assert "历史论文" in titles
    added = next(p for p in result if p["title"] == "历史论文")
    assert added["affiliation_note"] == "曾任职单位：中国地质大学（武汉）"


def test_research_without_llm_falls_back_to_titles():
    class TitleProvider(FakeProvider):
        def search_publications(self, name, *, institution=None, source="zh", limit=20):
            self.calls.append((name, institution, source))
            return []

        def search_by_title(self, title, *, source="zh", limit=5):
            self.calls.append(("title", title, source))
            return [FakeWork(f"标题匹配:{title}", institution="西南财经大学")]

    provider = TitleProvider()
    result = research_professor(
        NullLLM(), provider, name="林华珍", institution="武汉大学", source="zh",
        seed_titles=["某代表论文"],
    )
    assert any("标题匹配" in p["title"] for p in result.papers)
