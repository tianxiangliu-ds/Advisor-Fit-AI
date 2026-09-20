"""身份确认结果模型。

v0.1 的身份确认由用户手动完成；v0.2 起在此之上增加「多锚点自动核对」：
系统用若干条线索先自己判断一遍，判断不了或有冲突时才请用户确认。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ResolutionStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class IdentitySignal(BaseModel):
    """一条身份线索：matched=True 支持是同一个人，False 表示对不上，None 表示无法判断。"""

    key: str
    label: str
    matched: bool | None = None
    detail: str = ""


class ResolutionResult(BaseModel):
    status: ResolutionStatus = ResolutionStatus.REVIEW_REQUIRED
    selected_author_id: str | None = None
    reasons: list[str] = []
    conflicts: list[str] = []
    confirmed_by: str | None = None  # "user"
    # 多锚点自动核对的产出（v0.2 起）
    score: int = 0
    signals: list[IdentitySignal] = []

    def get_signal(self, key: str) -> IdentitySignal:
        for signal in self.signals:
            if signal.key == key:
                return signal
        return IdentitySignal(key=key, label=key)
