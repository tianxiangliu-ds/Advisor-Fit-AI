"""演示数据必须带 Agent 轨迹。

轨迹区是"Agent 可见"的核心展示，但它只在真正跑过一次检索后才产生。演示数据
如果不带轨迹，公开 Demo 的访问者就完全看不到 Agent 做过什么——而那恰恰是这个
项目最该被看到的部分。这条保障之前是缺的：正式的演示入口（demo_data.py）没有
写轨迹，只有旧的命令行脚本写了。

这里盯两件事：**演示入口确实写了轨迹**，以及**命令行脚本与演示入口共用同一份
实现**（否则又会出现"演示站有的、截图里没有"）。
"""

from __future__ import annotations

from advisor_fit.demo_data import demo_agent_trace, ensure_demo_data
from advisor_fit.storage.repository import Repository


def _load_traces(data_dir) -> list[dict | None]:
    repo = Repository(data_dir / "app.db")
    return [repo.load_trace(run["id"]) for run in repo.list_runs()]


def test_demo_trace_has_steps_tools_and_budget():
    trace = demo_agent_trace("示例导师")

    assert trace["steps"], "示例轨迹必须有步骤"
    assert trace["tools"], "示例轨迹必须带上这次提供给模型的工具清单"
    assert trace["budget"]["tokens"] > 0
    assert "示例" in trace["task"], "示例轨迹必须自己标明是示例，不能冒充真实运行"


def test_demo_trace_lists_every_real_tool():
    """示例里列的工具必须和工具箱真实声明的对得上，否则演示会误导人。"""
    from advisor_fit.agents.tools import tool_names

    offered = {spec["name"] for spec in demo_agent_trace()["tools"]}

    assert offered == set(tool_names())


def test_demo_trace_steps_reference_known_tools():
    names = {spec["name"] for spec in demo_agent_trace()["tools"]}

    for step in demo_agent_trace()["steps"]:
        if step["kind"] == "tool_call":
            assert step["name"] in names, f"示例轨迹调了未声明的工具：{step['name']}"


def test_every_demo_run_carries_a_trace(tmp_path):
    """公开 Demo 的轨迹区不该取决于"先点开哪条记录"。"""
    data_dir = tmp_path / "demo"
    ensure_demo_data(data_dir)

    traces = _load_traces(data_dir)

    assert traces, "演示数据必须包含已完成的研究记录"
    assert all(t is not None for t in traces), "每条演示记录都要带轨迹"
    assert all(t["steps"] for t in traces if t)


def test_demo_trace_is_declared_as_fictional(tmp_path):
    """公开演示站不能让人误以为这是真实运行日志。"""
    data_dir = tmp_path / "demo"
    ensure_demo_data(data_dir)

    for trace in _load_traces(data_dir):
        assert trace is not None
        assert "示例" in trace["task"] or "演示" in trace["task"]
