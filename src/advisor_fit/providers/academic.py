"""论文 Work 数据模型。

v0.1 手动证据输入版只保留 Work；作者候选召回与学术 Provider 协议已随
自动抓取能力移除（历史实现见 Git 标签 v0）。
"""

from __future__ import annotations

from pydantic import BaseModel


class Work(BaseModel):
    id: str
    title: str
    year: int | None = None
    doi: str | None = None
    venue: str | None = None
    topics: list[str] = []
    citation_count: int | None = None
    abstract: str = ""
    source_url: str | None = None
    source_platform: str | None = None
