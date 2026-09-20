"""统一导师库（data/advisors.db）：全项目唯一的一份导师数据。

两张表：
- `advisors`：一位导师一行（按 学校+学院+姓名 定位），合并所有来源的字段；
- `reviews`：学生评价，一位导师可以有多条。

合并规则：
- 同一个 (学校, 学院, 姓名) 只保留一行，新来源**只补空字段**，不覆盖已有值；
- 每个字段记下来自哪个来源（`field_sources`），保证可追溯；
- `sources` 记录这位导师在哪些来源里出现过。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from advisor_fit.models.advisor import ENRICHABLE_FIELDS, Advisor, merge_into

_SCHEMA = """
CREATE TABLE IF NOT EXISTS advisors (
  university TEXT NOT NULL,
  department TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  research_directions TEXT NOT NULL DEFAULT '[]',
  research_areas TEXT NOT NULL DEFAULT '[]',
  publications TEXT NOT NULL DEFAULT '[]',
  homepage_url TEXT NOT NULL DEFAULT '',
  profile_text TEXT NOT NULL DEFAULT '',
  orcid TEXT NOT NULL DEFAULT '',
  openalex_id TEXT NOT NULL DEFAULT '',
  school_cate TEXT NOT NULL DEFAULT '',
  sources TEXT NOT NULL DEFAULT '[]',
  source_url TEXT NOT NULL DEFAULT '',
  retrieved_at TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  field_sources TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (university, department, name)
);
CREATE INDEX IF NOT EXISTS idx_advisors_lookup ON advisors(university, department);
CREATE INDEX IF NOT EXISTS idx_advisors_name ON advisors(name);

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

_LIST_FIELDS = ("research_directions", "research_areas", "publications")


class AdvisorRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- 读写 -----------------------------------------------------------------

    @staticmethod
    def _row_to_advisor(row: sqlite3.Row) -> Advisor:
        return Advisor(
            university=row["university"],
            department=row["department"],
            name=row["name"],
            note=row["note"],
            title=row["title"],
            email=row["email"],
            research_directions=json.loads(row["research_directions"] or "[]"),
            research_areas=json.loads(row["research_areas"] or "[]"),
            publications=json.loads(row["publications"] or "[]"),
            homepage_url=row["homepage_url"],
            profile_text=row["profile_text"],
            orcid=row["orcid"],
            openalex_id=row["openalex_id"],
            school_cate=row["school_cate"],
            sources=json.loads(row["sources"] or "[]"),
            source_url=row["source_url"],
            retrieved_at=row["retrieved_at"],
            content_hash=row["content_hash"],
            field_sources=json.loads(row["field_sources"] or "{}"),
        )

    @staticmethod
    def _values(advisor: Advisor) -> tuple:
        return (
            advisor.university,
            advisor.department,
            advisor.name,
            advisor.note,
            advisor.title,
            advisor.email,
            json.dumps(advisor.research_directions, ensure_ascii=False),
            json.dumps(advisor.research_areas, ensure_ascii=False),
            json.dumps(advisor.publications, ensure_ascii=False),
            advisor.homepage_url,
            advisor.profile_text,
            advisor.orcid,
            advisor.openalex_id,
            advisor.school_cate,
            json.dumps(advisor.sources, ensure_ascii=False),
            advisor.source_url,
            advisor.retrieved_at or datetime.now(UTC).isoformat(),
            advisor.content_hash,
            json.dumps(advisor.field_sources, ensure_ascii=False),
        )

    _COLUMNS = (
        "university, department, name, note, title, email,"
        " research_directions, research_areas, publications, homepage_url, profile_text,"
        " orcid, openalex_id, school_cate, sources, source_url, retrieved_at,"
        " content_hash, field_sources"
    )

    def get(self, university: str, department: str, name: str) -> Advisor | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM advisors WHERE university = ? AND department = ? AND name = ?",
                (university, department, name),
            ).fetchone()
        return self._row_to_advisor(row) if row else None

    def upsert(self, advisor: Advisor, *, source: str) -> Advisor:
        """写入一位导师：已存在则只补空字段，不覆盖已有值。"""
        existing = self.get(advisor.university, advisor.department, advisor.name)
        if existing is None:
            merged = _with_source(advisor, source)
        else:
            merged = merge_into(existing, advisor, source)
        placeholders = ", ".join("?" * len(merged.model_dump()))
        with closing(self._connect()) as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO advisors ({self._COLUMNS}) VALUES ({placeholders})",
                self._values(merged),
            )
            conn.commit()
        return merged

    def upsert_many(self, advisors: list[Advisor], *, source: str) -> int:
        for item in advisors:
            self.upsert(item, source=source)
        return len(advisors)

    def import_reviews(self, rows: list[dict], *, replace: bool = True) -> int:
        with closing(self._connect()) as conn:
            if replace:
                conn.execute("DELETE FROM reviews")
            conn.executemany(
                "INSERT INTO reviews (university, department, supervisor, rate, summary, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        row.get("university", ""),
                        row.get("department", ""),
                        row.get("supervisor", ""),
                        row.get("rate"),
                        row.get("summary", ""),
                        row.get("detail", ""),
                    )
                    for row in rows
                ],
            )
            conn.commit()
        return len(rows)

    # -- 查询与统计 -----------------------------------------------------------

    def count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM advisors").fetchone()
        return int(row["n"])

    def delete(self, university: str, department: str, name: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM advisors WHERE university = ? AND department = ? AND name = ?",
                (university, department, name),
            )
            conn.commit()

    def clear_official(self, university: str | None = None) -> int:
        """清掉官网采集的痕迹（重爬前清理用），社区/旧库数据保留。

        - 只来自官网的行：直接删除；
        - 官网 + 社区混合的行：保留内容，但**必须把 official 标记摘掉**，
          否则重爬后的统计会把它们误算成"官网采集"。
        """
        where = " AND university = ?" if university else ""
        params: list[str] = [university] if university else []
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM advisors WHERE sources LIKE '%official%'{where}", params
            ).fetchall()
            removed = 0
            for row in rows:
                sources = json.loads(row["sources"] or "[]")
                if sources == ["official"]:
                    conn.execute(
                        "DELETE FROM advisors"
                        " WHERE university = ? AND department = ? AND name = ?",
                        (row["university"], row["department"], row["name"]),
                    )
                    removed += 1
                else:
                    kept = [item for item in sources if item != "official"]
                    conn.execute(
                        "UPDATE advisors SET sources = ?"
                        " WHERE university = ? AND department = ? AND name = ?",
                        (
                            json.dumps(kept, ensure_ascii=False),
                            row["university"],
                            row["department"],
                            row["name"],
                        ),
                    )
            conn.commit()
        return removed

    def normalize_all_names(self, normalize) -> tuple[int, int]:
        """把全库姓名规范化（剥职称后缀/单位前缀/空格）。

        规范化后若与同校同院的已有条目重名，则**合并**：保留字段更全的那条，
        把两条的来源并起来，另一条删除。

        返回 (改名数, 合并数)。

        为什么必须先做这一步：清洗时若直接按"姓名里含职称词"删除，
        「黄军教授」「李杰老师」这些真人会被误删。规范化把它们还原成
        「黄军」「李杰」，人就保住了。
        """
        renamed = merged = 0

        def _richness(row: sqlite3.Row) -> int:
            fields = ("title", "email", "homepage_url", "profile_text", "orcid", "openalex_id")
            score = sum(1 for f in fields if (row[f] or "").strip())
            for f in ("research_directions", "research_areas", "publications"):
                try:
                    score += 1 if json.loads(row[f] or "[]") else 0
                except (ValueError, TypeError):
                    pass
            return score

        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM advisors").fetchall()
            for row in rows:
                old = row["name"]
                new = normalize(old)
                if not new or new == old:
                    continue
                key = (row["university"], row["department"], new)
                existing = conn.execute(
                    "SELECT * FROM advisors WHERE university=? AND department=? AND name=?",
                    key,
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "UPDATE advisors SET name=? WHERE university=? AND department=? AND name=?",
                        (new, row["university"], row["department"], old),
                    )
                    renamed += 1
                    continue
                keep, drop = (
                    (existing, row) if _richness(existing) >= _richness(row) else (row, existing)
                )
                try:
                    sources = json.loads(keep["sources"] or "[]")
                except (ValueError, TypeError):
                    sources = []
                for item in json.loads(drop["sources"] or "[]"):
                    if item not in sources:
                        sources.append(item)
                conn.execute(
                    "UPDATE advisors SET sources=? WHERE university=? AND department=? AND name=?",
                    (
                        json.dumps(sources, ensure_ascii=False),
                        keep["university"],
                        keep["department"],
                        keep["name"],
                    ),
                )
                if drop["name"] != keep["name"] or drop["department"] != keep["department"]:
                    conn.execute(
                        "DELETE FROM advisors WHERE university=? AND department=? AND name=?",
                        (drop["university"], drop["department"], drop["name"]),
                    )
                merged += 1
            conn.commit()
        return renamed, merged

    def delete_by_name_rule(self, reject) -> tuple[int, list[tuple[str, str, str]]]:
        """按姓名规则删除条目（不分来源）。

        reject(name) 返回 True 表示这个姓名不合规、应删除。
        返回 (删除数, 被删样例(学校, 姓名, 来源))。
        """
        removed = 0
        samples: list[tuple[str, str, str]] = []
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM advisors").fetchall()
            for row in rows:
                if not reject(row["name"]):
                    continue
                conn.execute(
                    "DELETE FROM advisors WHERE university = ? AND department = ? AND name = ?",
                    (row["university"], row["department"], row["name"]),
                )
                removed += 1
                if len(samples) < 20:
                    sources = json.loads(row["sources"] or "[]")
                    samples.append((row["university"], row["name"], ",".join(sources)))
            conn.commit()
        return removed, samples

    def review_count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()
        return int(row["n"])

    def universities(self) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT university, COUNT(*) AS n FROM advisors"
                " GROUP BY university ORDER BY n DESC, university"
            ).fetchall()
        return [row["university"] for row in rows]

    def departments(self, university: str) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT department, COUNT(*) AS n FROM advisors WHERE university = ?"
                " GROUP BY department ORDER BY n DESC, department",
                (university.strip(),),
            ).fetchall()
        return [row["department"] for row in rows if row["department"]]

    def search(self, university: str, department: str | None = None) -> list[Advisor]:
        sql = "SELECT * FROM advisors WHERE university = ?"
        params: list[str] = [university.strip()]
        if department:
            sql += " AND department = ?"
            params.append(department.strip())
        sql += " ORDER BY department, name"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_advisor(row) for row in rows]

    def all_advisors(self) -> list[Advisor]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM advisors").fetchall()
        return [self._row_to_advisor(row) for row in rows]

    def reviews_for(self, university: str, supervisor: str) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE university = ? AND supervisor = ? ORDER BY id",
                (university.strip(), supervisor.strip()),
            ).fetchall()
        return [dict(row) for row in rows]

    def field_fill_rates(self) -> dict[str, float]:
        """每个字段的填充率，用来看"数据库还缺什么"。"""
        advisors = self.all_advisors()
        total = len(advisors)
        if not total:
            return {}
        rates = {}
        for field in ("name", "university", "department", *ENRICHABLE_FIELDS):
            filled = 0
            for item in advisors:
                value = getattr(item, field, None)
                if isinstance(value, str):
                    ok = bool(value.strip())
                elif isinstance(value, list | dict):
                    ok = len(value) > 0
                else:
                    ok = value is not None
                filled += 1 if ok else 0
            rates[field] = filled / total
        rates["_total"] = float(total)
        return rates

    def source_breakdown(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT sources FROM advisors").fetchall()
        counter: dict[str, int] = {}
        for row in rows:
            for name in json.loads(row["sources"] or "[]"):
                counter[name] = counter.get(name, 0) + 1
        return counter


def _with_source(advisor: Advisor, source: str) -> Advisor:
    item = advisor.model_copy(deep=True)
    if source not in item.sources:
        item.sources.append(source)
    if not item.retrieved_at:
        item.retrieved_at = datetime.now(UTC).isoformat()
    for field in ENRICHABLE_FIELDS:
        value = getattr(item, field)
        if value and field not in item.field_sources:
            item.field_sources[field] = source
    return item
