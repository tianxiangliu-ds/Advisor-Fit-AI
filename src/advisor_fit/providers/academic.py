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
    authors: list[str] = []
    institution: str = ""
    # 跨库合并后填充：收录这篇论文的所有来源（如 ["OpenAlex", "DBLP"]）。
    # 「被多个库收录」本身就是可信度信号，展示时给用户看。
    sources: list[str] = []
    # 学科分类（我们的内部 key，如 "medicine"），来自库自带的学科标签或关键词判断。
    disciplines: list[str] = []
