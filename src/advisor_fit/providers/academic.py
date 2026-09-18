"""学术数据 Provider 的协议与数据模型。"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from advisor_fit.models.professor import AuthorCandidate


class AuthorQuery(BaseModel):
    name: str
    institution: str | None = None
    aliases: list[str] = []
    topics: list[str] = []


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


class AcademicProviderUnavailable(Exception):
    """学术 API 不可用；调用方应降级而不是伪造空成功。"""


class AcademicProvider(Protocol):
    async def search_authors(self, query: AuthorQuery) -> list[AuthorCandidate]: ...

    async def list_recent_works(self, author_id: str, since_year: int) -> list[Work]: ...
