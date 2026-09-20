"""运行状态：把"一次研究跑到哪儿了"做成一个可保存、可续跑的对象。

为什么需要它：确认门控要求 Agent 能"停下来问人，再接着走"。此前这件事靠两个松散的
参数传递（`resume_steps` + `granted_confirmations`），调用方得自己记住要带什么。
抽成 `RunState` 之后：

- **一个对象就是全部状态**：走到哪个阶段、已产生哪些步骤、用户授权了什么、
  攒下哪些候选论文——序列化成 JSON 就能跨请求、跨进程传递；
- 续跑变明确：把上次返回的 state 原样传回去即可，不必猜要带哪几个字段；
- 与界面无关：Streamlit 存在 session_state 里，HTTP 接口放在响应体里，
  命令行写进文件，用的是同一个形状。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from advisor_fit.harness.workflow import Stage


@dataclass
class RunState:
    """一次导师研究的可续跑状态。"""

    task: str = ""
    # 已产生的步骤（完整数据，不是喂给模型的压缩版）
    steps: list[dict[str, Any]] = field(default_factory=list)
    # 用户已经授权过的人工确认语，再次遇到同一条就不再暂停
    granted: list[str] = field(default_factory=list)
    # 累积的候选论文
    papers: list[dict[str, Any]] = field(default_factory=list)
    # 当前阶段
    stage: str = str(Stage.SEEDING)
    # 停在哪个问题上等用户答复（空串表示没在等）
    pending_confirmation: str = ""
    # 触发资源上限时的降级原因
    degraded_reason: str = ""

    @property
    def awaiting_confirmation(self) -> bool:
        return bool(self.pending_confirmation)

    def advance(self, stage: Stage | str) -> None:
        """记录进入某个阶段。"""
        self.stage = str(stage)

    def grant(self, message: str) -> None:
        """记下用户对某个确认问题的答复，并清空待确认状态。"""
        if message and message not in self.granted:
            self.granted.append(message)
        self.pending_confirmation = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> RunState:
        """从 JSON 还原。字段缺失或类型不对时按默认值处理——恢复状态不该让请求失败。"""
        if not payload:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in payload.items() if k in known}
        for key in ("steps", "granted", "papers"):
            if key in clean and not isinstance(clean[key], list):
                clean[key] = []
        for key in ("task", "stage", "pending_confirmation", "degraded_reason"):
            if key in clean and not isinstance(clean[key], str):
                clean[key] = str(clean[key])
        return cls(**clean)


def state_from_outcome(outcome, *, task: str = "") -> RunState:
    """把一次运行结果收敛成可续跑的状态对象。"""
    return RunState(
        task=task,
        steps=list(outcome.steps or []),
        papers=list(outcome.papers or []),
        stage=str(Stage.AWAITING_CONFIRMATION if outcome.needs_confirmation else Stage.DONE),
        pending_confirmation=outcome.needs_confirmation or "",
        degraded_reason=outcome.degraded_reason or "",
    )
