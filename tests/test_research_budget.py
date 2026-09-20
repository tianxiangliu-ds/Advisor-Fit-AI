"""预算闸门与轨迹接入 Agent 生产路径：超限降级、每步可追溯、轨迹可落库。"""

from __future__ import annotations

from advisor_fit.agents.research import research_professor
from advisor_fit.harness.budget import BudgetLimits, BudgetTracker
from advisor_fit.harness.loop import AgentDecision
from advisor_fit.harness.trace import RunTrace
from advisor_fit.storage.repository import Repository


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
        self.sources = []
        self.disciplines = []
        self.citation_count = None


class FakeProvider:
    def __init__(self, works=None):
        self.works = works or []
        self.calls = []

    def search_publications(
        self, name, *, institution=None, source="zh", discipline=None, limit=20
    ):
        self.calls.append((name, institution, source))
        return self.works

    def search_by_title(self, title, *, source="zh", discipline=None, limit=5):
        self.calls.append(("title", title, source))
        return self.works


class RecordingLLM:
    """记录每次决策的 payload，用于断言 registry 元数据确实传给了 LLM。"""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.payloads = []

    def generate(self, *, schema, instructions, payload):
        self.payloads.append(payload)
        return self.decisions.pop(0)


def test_tool_registry_metadata_reaches_the_llm_payload():
    llm = RecordingLLM([AgentDecision(action="done", message="完成")])
    provider = FakeProvider(works=[FakeWork("论文A")])

    research_professor(llm, provider, name="王伟", institution="武汉大学")

    tools = {tool["name"]: tool for tool in llm.payloads[0]["tools"]}
    assert tools["search_by_author"]["permission"] == "network"
    assert tools["search_by_author"]["parameters"]["required"] == ["name"]


def test_budget_exhaustion_degrades_instead_of_raising():
    provider = FakeProvider(works=[FakeWork("论文A")])
    trace = RunTrace(task="t")
    budget = BudgetTracker(BudgetLimits(max_external_calls=1))

    result = research_professor(
        RecordingLLM([AgentDecision(action="done", message="完成")]),
        provider,
        name="王伟",
        institution="武汉大学",
        budget=budget,
        trace=trace,
    )

    assert result.log[-1] == {"budget_exhausted": "MAX_EXTERNAL_CALLS"}
    assert trace.degraded_reason == "MAX_EXTERNAL_CALLS"
    assert trace.budget["external_calls"] >= 1


def test_trace_records_decision_and_tool_steps_without_dumping_arguments():
    provider = FakeProvider(works=[FakeWork("论文A")])
    llm = RecordingLLM(
        [
            AgentDecision(action="tool", tool="search_by_title", args={"title": "论文A"}),
            AgentDecision(action="done", message="完成"),
        ]
    )
    trace = RunTrace(task="t")

    result = research_professor(
        llm, provider, name="王伟", institution="武汉大学", trace=trace
    )

    kinds = [step.kind for step in trace.steps]
    assert "llm_decision" in kinds
    assert "tool_call" in kinds
    tool_step = next(step for step in trace.steps if step.kind == "tool_call")
    assert tool_step.name == "search_by_title"
    assert tool_step.args_digest == "title=论文A"
    assert tool_step.status == "ok"
    assert result.trace is trace


def test_trace_survives_a_repository_round_trip(tmp_path):
    repo = Repository(tmp_path / "app.db")
    run_id = repo.create_run()
    trace = RunTrace(task="检索导师")
    trace.add(kind="tool_call", name="search_by_author", args_digest="name=王伟", duration_ms=42)
    trace.budget = {"external_calls": 3}

    repo.save_trace(run_id, trace)
    loaded = repo.load_trace(run_id)

    assert loaded is not None
    assert loaded["task"] == "检索导师"
    assert loaded["steps"][0]["name"] == "search_by_author"
    assert loaded["budget"]["external_calls"] == 3
