"""导师库 SQLite 存储：高校官网采集的导师档案。

独立于运行账本（data/faculty.db），可跨会话累积，用于建库与后续检索去重。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from advisor_fit.models.faculty import FacultyRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS faculty (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  university TEXT NOT NULL,
  college TEXT NOT NULL DEFAULT '',
  department TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  homepage_url TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  research_areas TEXT NOT NULL DEFAULT '[]',
  research_directions TEXT NOT NULL DEFAULT '[]',
  publications TEXT NOT NULL DEFAULT '[]',
  profile_text TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  retrieved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_faculty_univ_college ON faculty(university, college);
"""


class FacultyRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as conn:
            with conn:
                yield conn

    def upsert(self, record: FacultyRecord) -> None:
        data = record.model_dump(mode="json")
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO faculty "
                "(id, name, university, college, department, title, homepage_url, email,"
                " research_areas, research_directions, publications, profile_text,"
                " source_url, retrieved_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.name,
                    record.university,
                    record.college,
                    record.department,
                    record.title,
                    record.homepage_url,
                    record.email,
                    json.dumps(data["research_areas"], ensure_ascii=False),
                    json.dumps(data["research_directions"], ensure_ascii=False),
                    json.dumps(data["publications"], ensure_ascii=False),
                    record.profile_text,
                    record.source_url,
                    record.retrieved_at,
                ),
            )

    def upsert_many(self, records: list[FacultyRecord]) -> int:
        for record in records:
            self.upsert(record)
        return len(records)

    def count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM faculty").fetchone()
        return int(row["n"])

    def list_by_university(self, university: str) -> list[FacultyRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM faculty WHERE university = ? ORDER BY college, name",
                (university,),
            ).fetchall()
        return [self._row_to_record(dict(row)) for row in rows]

    def _row_to_record(self, row: dict[str, Any]) -> FacultyRecord:
        return FacultyRecord(
            id=row["id"],
            name=row["name"],
            university=row["university"],
            college=row["college"],
            department=row["department"],
            title=row["title"],
            homepage_url=row["homepage_url"],
            email=row["email"],
            research_areas=json.loads(row["research_areas"] or "[]"),
            research_directions=json.loads(row["research_directions"] or "[]"),
            publications=json.loads(row["publications"] or "[]"),
            profile_text=row["profile_text"],
            source_url=row["source_url"],
            retrieved_at=row["retrieved_at"],
        )
