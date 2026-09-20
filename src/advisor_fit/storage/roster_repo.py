"""导师名册的本地存储（data/supervisor_roster.db）。

只存学校 / 学院 / 导师姓名三列，用于回答「这个学院有哪些导师」。
它是**名单来源**，不是事实来源：论文归属、研究方向仍以官网与学术库为准。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from advisor_fit.ingest.supervisor_roster import RosterEntry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roster (
  university TEXT NOT NULL,
  department TEXT NOT NULL DEFAULT '',
  supervisor TEXT NOT NULL,
  PRIMARY KEY (university, department, supervisor)
);
CREATE INDEX IF NOT EXISTS idx_roster_lookup ON roster(university, department);
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

    def replace_all(self, entries: list[RosterEntry]) -> int:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM roster")
            conn.executemany(
                "INSERT OR IGNORE INTO roster (university, department, supervisor)"
                " VALUES (?, ?, ?)",
                [(item.university, item.department, item.supervisor) for item in entries],
            )
            conn.commit()
        return len(entries)

    def count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM roster").fetchone()
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
