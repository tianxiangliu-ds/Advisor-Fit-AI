"""导师名册的本地存储（SQLite）。

两张表：
- `roster`：导师名册，一行一位导师，按 (学校, 学院, 姓名) 去重——「不重不漏」。
  同一位导师在多个学院各留一条，不做跨学院合并。
- `reviews`：学生评价，一行一条，同一位导师可以有多条。

来源用两个标记区分，互不覆盖：`in_community`（社区开源数据）、`in_official`（官网采集）。
`note` 存括号里的备注（如「深圳,千人计划」），它是有用信息，不是脏数据。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from advisor_fit.ingest.supervisor_roster import (
    RosterEntry,
    RosterPayload,
    SupervisorReview,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roster (
  university TEXT NOT NULL,
  department TEXT NOT NULL DEFAULT '',
  supervisor TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  school_cate TEXT NOT NULL DEFAULT '',
  in_community INTEGER NOT NULL DEFAULT 0,
  in_official INTEGER NOT NULL DEFAULT 0,
  source_url TEXT NOT NULL DEFAULT '',
  retrieved_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (university, department, supervisor)
);
CREATE INDEX IF NOT EXISTS idx_roster_lookup ON roster(university, department);
CREATE INDEX IF NOT EXISTS idx_roster_name ON roster(supervisor);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  university TEXT NOT NULL,
  department TEXT NOT NULL DEFAULT '',
  supervisor TEXT NOT NULL,
  rate REAL,
  summary TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_reviews_lookup ON reviews(university, supervisor);
"""


class RosterRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- 导入 -----------------------------------------------------------------

    def import_community(self, payload: RosterPayload) -> tuple[int, int]:
        """导入社区名册与评价；只清理社区来源的数据，不动官网采集的结果。"""
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM reviews")
            conn.execute("DELETE FROM roster WHERE in_official = 0")
            conn.executemany(
                "INSERT OR IGNORE INTO roster"
                " (university, department, supervisor, note, school_cate, in_community)"
                " VALUES (?, ?, ?, ?, ?, 1)",
                [
                    (e.university, e.department, e.supervisor, e.note, e.school_cate)
                    for e in payload.entries
                ],
            )
            conn.executemany(
                "INSERT INTO reviews (university, department, supervisor, rate, summary, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (r.university, r.department, r.supervisor, r.rate, r.summary, r.detail)
                    for r in payload.reviews
                ],
            )
            conn.commit()
        return len(payload.entries), len(payload.reviews)

    def upsert_official(self, entries: list[RosterEntry], *, source_url: str = "") -> int:
        """写入官网采集到的导师；已存在则补标记，不覆盖已有备注。"""
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as conn:
            conn.executemany(
                "INSERT INTO roster"
                " (university, department, supervisor, note, in_official, source_url, retrieved_at)"
                " VALUES (?, ?, ?, ?, 1, ?, ?)"
                " ON CONFLICT(university, department, supervisor) DO UPDATE SET"
                "   in_official = 1,"
                "   source_url = excluded.source_url,"
                "   retrieved_at = excluded.retrieved_at,"
                "   note = CASE WHEN roster.note = '' THEN excluded.note ELSE roster.note END",
                [
                    (e.university, e.department, e.supervisor, e.note, source_url, now)
                    for e in entries
                ],
            )
            conn.commit()
        return len(entries)

    # -- 查询 -----------------------------------------------------------------

    def count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM roster").fetchone()
        return int(row["n"])

    def review_count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()
        return int(row["n"])

    def universities(self) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT university, COUNT(*) AS n FROM roster"
                " GROUP BY university ORDER BY n DESC, university"
            ).fetchall()
        return [row["university"] for row in rows]

    def departments(self, university: str) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT department, COUNT(*) AS n FROM roster WHERE university = ?"
                " GROUP BY department ORDER BY n DESC, department",
                (university.strip(),),
            ).fetchall()
        return [row["department"] for row in rows if row["department"]]

    def lookup(self, university: str, department: str | None = None) -> list[str]:
        sql = "SELECT DISTINCT supervisor FROM roster WHERE university = ?"
        params: list[str] = [university.strip()]
        if department:
            sql += " AND department = ?"
            params.append(department.strip())
        sql += " ORDER BY supervisor"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row["supervisor"] for row in rows]

    def entries(self, university: str, department: str | None = None) -> list[RosterEntry]:
        sql = "SELECT * FROM roster WHERE university = ?"
        params: list[str] = [university.strip()]
        if department:
            sql += " AND department = ?"
            params.append(department.strip())
        sql += " ORDER BY department, supervisor"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            RosterEntry(
                university=row["university"],
                department=row["department"],
                supervisor=row["supervisor"],
                note=row["note"],
                school_cate=row["school_cate"],
            )
            for row in rows
        ]

    def reviews_for(self, university: str, supervisor: str) -> list[SupervisorReview]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE university = ? AND supervisor = ?"
                " ORDER BY id",
                (university.strip(), supervisor.strip()),
            ).fetchall()
        return [
            SupervisorReview(
                university=row["university"],
                department=row["department"],
                supervisor=row["supervisor"],
                rate=row["rate"],
                summary=row["summary"],
                detail=row["detail"],
            )
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT"
                " SUM(in_community) AS community,"
                " SUM(in_official) AS official,"
                " SUM(in_community * in_official) AS both"
                " FROM roster"
            ).fetchone()
        return {
            "advisors": self.count(),
            "reviews": self.review_count(),
            "from_community": int(row["community"] or 0),
            "from_official": int(row["official"] or 0),
            "from_both": int(row["both"] or 0),
        }
