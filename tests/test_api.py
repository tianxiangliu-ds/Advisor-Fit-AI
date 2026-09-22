"""HTTP 接口层测试。

全部用注入的假 provider / 假模型，**不联网、不花钱**。这里盯四件事：

1. 三个端点都能用，且返回结构稳定；
2. `/tools` 暴露的确实是 Agent 真实拥有的工具（不是另抄一份）；
3. 异常情况按约定处理：参数非法 422、来源全挂 503（让调用方知道"可以重试"，
   而不是把空结果当成"这位导师没有论文"）；
4. **服务层不依赖任何 Web 框架**——这是"Harness 与界面解耦"的可检验形式。
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from advisor_fit.agents.tools import TOOL_SPECS
from advisor_fit.api import create_app
from advisor_fit.llm.provider import NullLLM
from advisor_fit.services.research import ResearchRequest, run_research


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


class FakeOutcome:
    def __init__(self, label, status):
        self.key = label
        self.label = label
        self.status = status
        self.count = 0
        self.reason = "超时" if status == "error" else ""
        self.cost = 0.0

    @property
    def searched(self) -> bool:
        return self.status in ("ok", "empty")


class FakeProvider:
    counts_external_calls = False

    def __init__(self, works=None, outcomes=None):
        self._works = works or []
        self._outcomes = outcomes or [FakeOutcome("OpenAlex", "ok")]

    def search_publications(self, name, **kwargs):
        return list(self._works)

    def search_by_title(self, title, **kwargs):
        return []

    def describe(self):
        return "FakeProvider · 测试用"

    def discipline_text(self):
        return "通用"

    def scope_label(self, *args, **kwargs):
        return "全部来源"

    def outcome(self):
        class _O:
            outcomes = self._outcomes
        return _O()


def _client(**kwargs) -> TestClient:
    return TestClient(create_app(**kwargs))


def _provider_factory(provider):
    return lambda budget: provider


# -- /health -------------------------------------------------------------------


def test_health_reports_version_mode_and_tool_count():
    body = _client().get("/health").json()

    assert body["status"] == "ok"
    assert body["version"]
    assert body["tools"] == len(TOOL_SPECS)
    assert body["mode"] in ("agent", "rule")


def test_health_says_so_when_no_model_is_configured():
    """没配 Key 时要明说，而不是让调用方以为背后有个大模型。"""
    body = _client(llm_factory=NullLLM).get("/health").json()

    assert body["mode"] == "rule"
    assert "LLM_API_KEY" in body["note"]


# -- /tools --------------------------------------------------------------------


def test_tools_endpoint_exposes_the_real_tool_catalog():
    """工具清单必须来自真实契约，不能是另抄一份——抄一份就会和实际能力脱节。"""
    tools = _client().get("/tools").json()

    assert {tool["name"] for tool in tools} == set(TOOL_SPECS)
    for tool in tools:
        spec = TOOL_SPECS[tool["name"]]
        assert tool["description"] == spec.description
        assert tool["parameters"] == spec.parameters
        assert tool["permission"] == spec.permission


def test_network_tools_expose_their_rate_limit():
    tools = {tool["name"]: tool for tool in _client().get("/tools").json()}

    assert tools["search_by_author"]["rate_limit_per_run"] == 4
    assert tools["search_by_author"]["permission"] == "network"
    assert tools["check_paper_affiliations"]["permission"] == "read"


# -- /research -----------------------------------------------------------------


def test_research_returns_papers_trace_and_mode():
    provider = FakeProvider([FakeWork("一篇论文", institution="武汉大学")])
    client = _client(llm_factory=NullLLM, provider_factory=_provider_factory(provider))

    response = client.post("/research", json={"name": "陆伟", "institution": "武汉大学"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["papers"]) == 1
    assert body["mode"] == "rule"
    assert body["trace"]["steps"], "必须带回运行轨迹"
    assert body["trace"]["mode"] == "rule"


def test_research_trace_carries_real_tool_steps():
    provider = FakeProvider([FakeWork("一篇论文", institution="武汉大学")])
    client = _client(llm_factory=NullLLM, provider_factory=_provider_factory(provider))

    body = client.post(
        "/research", json={"name": "陆伟", "institution": "武汉大学"}
    ).json()

    names = [s["name"] for s in body["trace"]["steps"] if s["kind"] == "tool_call"]
    assert "search_by_author" in names


def test_research_notes_that_membership_needs_human_confirmation():
    provider = FakeProvider([FakeWork("一篇论文", institution="武汉大学")])
    client = _client(llm_factory=NullLLM, provider_factory=_provider_factory(provider))

    body = client.post(
        "/research", json={"name": "陆伟", "institution": "武汉大学"}
    ).json()

    assert "人工确认" in body["note"]


def test_research_without_a_name_is_rejected():
    assert _client().post("/research", json={"name": ""}).status_code == 422


def test_research_returns_503_when_no_source_could_be_reached():
    """一个来源都没查成属于服务侧故障，要让调用方知道"可以重试"。

    若返回 200 + 空列表，调用方会把"检索挂了"读成"这位导师没有论文"——
    那是把一个检索问题当成了关于人的事实。
    """
    provider = FakeProvider(
        [], outcomes=[FakeOutcome("OpenAlex", "error"), FakeOutcome("Crossref", "blocked")]
    )
    client = _client(llm_factory=NullLLM, provider_factory=_provider_factory(provider))

    response = client.post("/research", json={"name": "陆伟"})

    assert response.status_code == 503
    assert "不代表这位导师没有论文" in response.json()["detail"]


def test_research_returns_200_when_sources_worked_but_found_nothing():
    """来源正常但没有结果，是正常业务结果，不能当故障。"""
    provider = FakeProvider([], outcomes=[FakeOutcome("OpenAlex", "empty")])
    client = _client(llm_factory=NullLLM, provider_factory=_provider_factory(provider))

    response = client.post("/research", json={"name": "陆伟"})

    assert response.status_code == 200
    assert response.json()["papers"] == []


# -- 服务层与 Web 框架解耦 ------------------------------------------------------


def test_service_layer_does_not_import_any_web_framework():
    """服务层必须能脱离 Web 框架使用——这是"Harness 与界面解耦"的可检验形式。

    做法：在干净的解释器里只导入服务层，断言 streamlit / fastapi 都没被拉进来。
    """
    import subprocess

    code = (
        "import sys; import advisor_fit.services.research;"
        "assert 'streamlit' not in sys.modules, '服务层不该依赖 Streamlit';"
        "assert 'fastapi' not in sys.modules, '服务层不该依赖 FastAPI';"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_run_research_is_usable_without_any_web_layer():
    """不经 HTTP 也能直接调用服务层——命令行、批处理、评测脚本都靠这条路。"""
    provider = FakeProvider([FakeWork("一篇论文", institution="武汉大学")])

    outcome = run_research(
        ResearchRequest(name="陆伟", institution="武汉大学"),
        llm=NullLLM(),
        provider=provider,
    )

    assert len(outcome.papers) == 1
    assert outcome.trace["steps"]
    assert outcome.health == {"total": 1, "searched": 1, "failed": 0, "failed_labels": []}


def test_research_request_prefers_the_correction_institution():
    """若纠错机构仍被原学校覆盖，这个测试会失败。"""
    request = ResearchRequest(
        name="王老师",
        institution="原任职单位",
        alternate_institution="清华大学",
    )

    assert request.effective_institution == "清华大学"


def test_research_request_includes_department_in_deterministic_routing_hints():
    """院系是无需模型参与的检索分流依据，不能在进入研究页后丢失。"""
    request = ResearchRequest(
        name="许永超",
        institution="武汉大学",
        department="计算机学院",
        known_directions=["计算机视觉"],
    )

    assert request.routing_hints == ["计算机学院", "计算机视觉"]


@pytest.mark.parametrize("source", ["auto", "zh", "en"])
def test_research_accepts_every_source_mode(source):
    provider = FakeProvider([FakeWork("一篇论文", institution="武汉大学")])
    client = _client(llm_factory=NullLLM, provider_factory=_provider_factory(provider))

    assert client.post("/research", json={"name": "陆伟", "source": source}).status_code == 200
