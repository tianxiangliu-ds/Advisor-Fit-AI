"""运行状态与工作流阶段测试。

这两样是 C 阶段抽出来的抽象，共同解决"一次研究跑到哪儿了"这个问题：
- `RunState`：把阶段、已产生的步骤、用户授权、候选论文装进**一个可序列化对象**，
  续跑时原样传回即可，不必猜要带哪几个字段（此前是两个松散参数）；
- `Stage`：把流水线阶段显式声明出来，让运行期也能看出走到哪一步（此前只存在于
  控制流里，读代码能看出来、运行时看不出来）。
"""

from __future__ import annotations

from advisor_fit.harness.state import RunState, state_from_outcome
from advisor_fit.harness.trace import RunTrace
from advisor_fit.harness.workflow import EXPECTED_STAGES, Stage, stage_label

# -- 运行状态 ------------------------------------------------------------------


def test_state_round_trips_through_json():
    """序列化再还原必须一模一样——这是"跨请求续跑"的前提。"""
    state = RunState(task="陆伟", granted=["只保留武汉大学的"], stage=str(Stage.DONE))
    state.steps.append({"tool": "search_by_author"})

    restored = RunState.from_dict(state.to_dict())

    assert restored == state


def test_state_accepts_a_dict_from_the_wire():
    """调用方从 HTTP 传回来的是普通字典，不能要求它非得是对象。"""
    state = RunState.from_dict({"task": "陆伟", "granted": ["保留"], "stage": "searching"})

    assert state.task == "陆伟"
    assert state.granted == ["保留"]
    assert state.stage == "searching"


def test_broken_state_falls_back_instead_of_failing():
    """恢复状态出错不该让整个请求失败——宁可按空状态重跑。"""
    assert RunState.from_dict(None) == RunState()
    assert RunState.from_dict({}) == RunState()

    messy = RunState.from_dict(
        {"steps": "不是列表", "granted": 42, "papers": None, "task": 123, "无关字段": "x"}
    )
    assert messy.steps == [] and messy.granted == [] and messy.papers == []
    assert messy.task == "123"


def test_grant_records_the_answer_and_clears_pending():
    state = RunState(pending_confirmation="要保留哪些？")

    state.grant("只保留武汉大学的")

    assert state.granted == ["只保留武汉大学的"]
    assert state.pending_confirmation == ""
    assert state.awaiting_confirmation is False


def test_grant_does_not_duplicate():
    state = RunState()

    state.grant("保留")
    state.grant("保留")

    assert state.granted == ["保留"]


def test_advance_updates_the_stage():
    state = RunState()

    state.advance(Stage.DISAMBIGUATING)

    assert state.stage == str(Stage.DISAMBIGUATING)


class _Outcome:
    def __init__(self, **kwargs):
        self.steps = kwargs.get("steps", [])
        self.papers = kwargs.get("papers", [])
        self.needs_confirmation = kwargs.get("needs_confirmation", "")
        self.degraded_reason = kwargs.get("degraded_reason", "")


def test_outcome_becomes_awaiting_confirmation_state():
    outcome = _Outcome(needs_confirmation="要保留哪些？", papers=[{"title": "A"}])

    state = state_from_outcome(outcome, task="陆伟")

    assert state.awaiting_confirmation is True
    assert state.pending_confirmation == "要保留哪些？"
    assert state.stage == str(Stage.AWAITING_CONFIRMATION)


def test_outcome_becomes_done_state_when_nothing_blocks():
    state = state_from_outcome(_Outcome(papers=[{"title": "A"}]), task="陆伟")

    assert state.awaiting_confirmation is False
    assert state.stage == str(Stage.DONE)


def test_outcome_state_carries_steps_for_resume():
    """续跑要靠 steps，丢了就得从头再来。"""
    outcome = _Outcome(steps=[{"tool": "x", "args": {}, "result": {}}])

    state = state_from_outcome(outcome)

    assert len(state.steps) == 1


# -- 工作流阶段 ----------------------------------------------------------------


def test_every_stage_has_a_chinese_label():
    for stage in Stage:
        assert stage_label(stage), f"{stage} 缺少中文名"


def test_expected_stages_cover_the_normal_path():
    assert Stage.SEEDING in EXPECTED_STAGES
    assert Stage.SEARCHING in EXPECTED_STAGES
    assert EXPECTED_STAGES[-1] == Stage.DONE


def test_trace_records_stages_in_order():
    trace = RunTrace(task="t")

    for stage in (Stage.SEEDING, Stage.SEARCHING, Stage.DISAMBIGUATING, Stage.DONE):
        trace.mark_stage(stage)

    assert trace.stages == [str(s) for s in EXPECTED_STAGES]


def test_mark_stage_does_not_repeat_consecutively():
    trace = RunTrace(task="t")

    trace.mark_stage(Stage.SEARCHING)
    trace.mark_stage(Stage.SEARCHING)

    assert trace.stages == [str(Stage.SEARCHING)]


def test_stages_survive_trace_serialisation():
    trace = RunTrace(task="t")
    trace.mark_stage(Stage.SEARCHING)

    assert trace.to_dict()["stages"] == [str(Stage.SEARCHING)]


def test_rule_path_records_the_stages_it_actually_goes_through():
    """没配大模型时也要能看出走了哪几步。"""
    from advisor_fit.agents.research import research_professor
    from advisor_fit.llm.provider import NullLLM
    from tests.test_research import FakeProvider, FakeWork

    trace = RunTrace(task="t")
    provider = FakeProvider([FakeWork("一篇论文", institution="武汉大学")])
    research_professor(
        NullLLM(), provider,
        name="陆伟", institution="武汉大学", source="zh",
        seed_titles=["一篇论文"], trace=trace, title_fallback=provider,
    )

    assert trace.stages[0] == str(Stage.SEEDING)
    assert str(Stage.SEARCHING) in trace.stages
    assert trace.stages[-1] == str(Stage.DONE)
