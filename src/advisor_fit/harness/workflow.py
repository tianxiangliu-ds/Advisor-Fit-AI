"""工作流阶段：把"一次研究要经过哪几步"显式声明出来。

为什么值得单独抽：这套阶段原本只存在于 `research_professor` 的控制流里——
读代码能看出来，但**运行期看不出来**。结果是：
- 界面上没法告诉用户"现在卡在消歧这一步"；
- 轨迹里只知道调了哪些工具，不知道整条流水线走到哪了；
- 想给某个阶段单独加门控或超时，得先去控制流里找位置。

声明成阶段之后，运行会把经过的阶段记进轨迹（`RunTrace.stages`），
界面与接口都能直接读。
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    """一次导师研究经过的阶段。顺序即执行顺序。"""

    SEEDING = "seeding"              # 先用"姓名+学校"或代表论文标题确定性查一轮
    SEARCHING = "searching"          # Agent 循环：模型决定扩搜、换库、换机构
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # 机构冲突或同名，暂停请人确认
    DISAMBIGUATING = "disambiguating"  # 作者消歧：哪些论文确实属于这个人
    INVESTIGATING = "investigating"  # 履历调查：候选机构与填写学校不符时补证据
    DONE = "done"


STAGE_LABELS: dict[str, str] = {
    Stage.SEEDING: "确定性预查",
    Stage.SEARCHING: "Agent 检索",
    Stage.AWAITING_CONFIRMATION: "等待人工确认",
    Stage.DISAMBIGUATING: "作者消歧",
    Stage.INVESTIGATING: "履历调查",
    Stage.DONE: "完成",
}

# 正常走完一轮应当经过的阶段（等待确认只在需要时才出现）
EXPECTED_STAGES: tuple[Stage, ...] = (
    Stage.SEEDING,
    Stage.SEARCHING,
    Stage.DISAMBIGUATING,
    Stage.DONE,
)


def stage_label(stage: str) -> str:
    """阶段的中文名，给界面用。"""
    return STAGE_LABELS.get(stage, stage)
