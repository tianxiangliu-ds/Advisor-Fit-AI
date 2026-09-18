"""共享枚举与来源记录。

这里冻结了全项目最重要的事实三态语义：
- FACT：来自证据且（学生侧）经用户确认的事实；
- INFERRED：仅用于召回/解释的推断，不得改写成经历；
- UNKNOWN：未提供，禁止被 LLM 或下游补全为肯定/否定。
"""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic import BaseModel


class FactStatus(StrEnum):
    FACT = "FACT"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SourceTier(int, Enum):
    """1 官方页；2 学术 API；3 第三方。"""

    OFFICIAL = 1
    ACADEMIC = 2
    THIRD_PARTY = 3


class SourceRecord(BaseModel):
    """一次抓取/查询产生的来源记录，进入证据账本。"""

    id: str
    source_type: str  # official_page | robots_txt | openalex | ...
    url: str | None = None
    final_url: str | None = None
    status_code: int | None = None
    title: str | None = None
    text: str | None = None
    retrieved_at: str | None = None
    content_hash: str | None = None
    robots_allowed: bool | None = None
    fetch_status: str = "OK"  # OK | FETCH_FAILED | ROBOTS_DENIED | UNSAFE_URL
