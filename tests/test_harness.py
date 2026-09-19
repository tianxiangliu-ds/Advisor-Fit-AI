"""最小 Agent 循环测试。"""

from advisor_fit.harness.loop import AgentDecision, run_loop


class FakeLLM:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def generate(self, *, schema, instructions, payload):
        return self.decisions.pop(0)


def test_run_loop_calls_tool_then_done():
    calls = []

    def search(name):
        calls.append(name)
        return {"papers": ["p1"]}

    llm = FakeLLM(
        [
            AgentDecision(action="tool", tool="search", args={"name": "王伟"}),
            AgentDecision(action="done", message="完成"),
        ]
    )
    steps = run_loop(llm, [("search", "按姓名检索论文", search)], "整理王伟的论文")
    assert calls == ["王伟"]
    assert any(
        step.get("tool") == "search" and step["result"]["papers"] == ["p1"] for step in steps
    )
    assert steps[-1] == {"done": "完成"}


def test_run_loop_stops_on_confirmation_request():
    llm = FakeLLM(
        [AgentDecision(action="confirm", message="请确认王伟是否为武汉大学教授")]
    )
    steps = run_loop(llm, [], "确认身份")
    assert steps[-1] == {"needs_confirmation": "请确认王伟是否为武汉大学教授"}


def test_run_loop_resumes_past_granted_confirmation():
    calls = []

    def search(name):
        calls.append(name)
        return {"papers": [name]}

    llm = FakeLLM(
        [
            AgentDecision(action="confirm", message="确认是武汉大学的王伟吗？"),
            AgentDecision(action="tool", tool="search", args={"name": "王伟"}),
            AgentDecision(action="done", message="完成"),
        ]
    )
    steps = run_loop(
        llm,
        [("search", "检索", search)],
        "整理王伟的论文",
        resume_steps=[{"needs_confirmation": "确认是武汉大学的王伟吗？"}],
        granted_confirmations=["确认是武汉大学的王伟吗？"],
    )
    assert calls == ["王伟"]
    assert steps[0] == {"needs_confirmation": "确认是武汉大学的王伟吗？"}
    assert steps[-1] == {"done": "完成"}


def test_run_loop_survives_tool_error():
    def broken(**kwargs):
        raise RuntimeError("boom")

    llm = FakeLLM(
        [
            AgentDecision(action="tool", tool="broken", args={}),
            AgentDecision(action="done", message="结束"),
        ]
    )
    steps = run_loop(llm, [("broken", "会报错的工具", broken)], "测试")
    assert steps[0]["tool"] == "broken"
    assert "boom" in steps[0]["result"]["error"]
    assert steps[-1] == {"done": "结束"}
