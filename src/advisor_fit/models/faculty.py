"""导师库记录模型。

从高校官网采集的导师结构化档案，用于后续的「导师指纹」建库与检索去重。
"""

from __future__ import annotations

from pydantic import BaseModel


class FacultyRecord(BaseModel):
    id: str  # 由 (university, college, name, homepage_url) 生成的稳定哈希
    name: str
    university: str
    college: str = ""
    department: str = ""
    title: str = ""  # 职称，如「教授」
    homepage_url: str = ""
    email: str = ""
    research_areas: list[str] = []       # 研究领域（大类）
    research_directions: list[str] = []  # 研究方向（具体）
    publications: list[str] = []         # 代表论文/著作标题
    profile_text: str = ""               # 原始简介文本，供后续抽取指纹
    source_url: str = ""                 # 采集来源页（学院师资列表）
    retrieved_at: str = ""
