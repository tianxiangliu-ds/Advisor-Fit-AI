"""身份确认结果模型。

v0.1 的身份确认由用户手动完成；ResolutionResult 只记录确认状态与依据，
不再包含自动作者候选排名（该能力已在 v0.1 移除，历史实现见 Git 标签 v0）。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ResolutionStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class ResolutionResult(BaseModel):
    status: ResolutionStatus = ResolutionStatus.REVIEW_REQUIRED
    selected_author_id: str | None = None
    reasons: list[str] = []
    conflicts: list[str] = []
    confirmed_by: str | None = None  # "user"
