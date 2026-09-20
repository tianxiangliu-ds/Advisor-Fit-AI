"""网页缓存：同一个链接在「保质期」内不再重复抓取。

为什么要做：每次查同一位导师都要重新访问学校网站，慢、还可能被限流。
缓存把抓过的页面（以及它是不是学校允许抓取、内容指纹）存下来，
保质期内直接用，过期了才重新去抓。

分层保质期（当前先按页面类型粗分，后续需要时再细分到字段）：
- 导师主页：90 天（身份/院系/职称这类信息变化很慢）
- 其它页面：默认 30 天
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel

DEFAULT_TTL_SECONDS = 30 * 24 * 3600
PROFILE_TTL_SECONDS = 90 * 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  url TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  content_hash TEXT NOT NULL DEFAULT '',
  etag TEXT,
  robots_allowed INTEGER,
  fetched_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0
);
"""


class CachedPage(BaseModel):
    url: str
    text: str
    content_hash: str = ""
    etag: str | None = None
    robots_allowed: bool | None = None
    fetched_at: float = 0.0
    expires_at: float = 0.0


class PageCache:
    """按链接缓存网页正文，并记录抓取时间与内容指纹。"""

    def __init__(
        self,
        db_path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self.default_ttl_seconds = default_ttl_seconds
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, url: str) -> CachedPage | None:
        """保质期内的缓存才返回；过期返回 None，由调用方重新抓取。"""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM pages WHERE url = ?", (url,)).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= self._clock():
                return None
            conn.execute("UPDATE pages SET hits = hits + 1 WHERE url = ?", (url,))
            conn.commit()
        return CachedPage(
            url=row["url"],
            text=row["text"],
            content_hash=row["content_hash"],
            etag=row["etag"],
            robots_allowed=None if row["robots_allowed"] is None else bool(row["robots_allowed"]),
            fetched_at=float(row["fetched_at"]),
            expires_at=float(row["expires_at"]),
        )

    def peek(self, url: str) -> CachedPage | None:
        """不看保质期，只取原始记录（给"上次抓到的是什么"这类展示用）。"""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM pages WHERE url = ?", (url,)).fetchone()
        if row is None:
            return None
        return CachedPage(
            url=row["url"],
            text=row["text"],
            content_hash=row["content_hash"],
            etag=row["etag"],
            robots_allowed=None if row["robots_allowed"] is None else bool(row["robots_allowed"]),
            fetched_at=float(row["fetched_at"]),
            expires_at=float(row["expires_at"]),
        )

    def put(
        self,
        url: str,
        *,
        text: str,
        content_hash: str = "",
        etag: str | None = None,
        robots_allowed: bool | None = None,
        ttl_seconds: int | None = None,
    ) -> CachedPage:
        now = self._clock()
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = now + ttl
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pages"
                " (url, text, content_hash, etag, robots_allowed, fetched_at, expires_at, hits)"
                " VALUES (?, ?, ?, ?, ?, ?, ?,"
                "  COALESCE((SELECT hits FROM pages WHERE url = ?), 0))",
                (
                    url,
                    text,
                    content_hash,
                    etag,
                    None if robots_allowed is None else int(robots_allowed),
                    now,
                    expires_at,
                    url,
                ),
            )
            conn.commit()
        return CachedPage(
            url=url,
            text=text,
            content_hash=content_hash,
            etag=etag,
            robots_allowed=robots_allowed,
            fetched_at=now,
            expires_at=expires_at,
        )

    def count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()
        return int(row["n"])

    def clear(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM pages")
            conn.commit()
