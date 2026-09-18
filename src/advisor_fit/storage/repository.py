"""SQLite 证据账本：来源、证据、声明、画像与运行的本地持久化。

设计要点：
- 外键 ON DELETE CASCADE 保证 delete_run 级联删除该运行的全部 artifact；
- 每个写入都在事务中完成（with conn: commit/rollback）；
- delete_run 只接受精确 UUID，拒绝路径或通配符。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from advisor_fit.models.common import SourceRecord
from advisor_fit.models.evidence import Claim, Evidence
from advisor_fit.models.match import Draft, MatchReport
from advisor_fit.models.professor import ProfessorProfile
from advisor_fit.models.student import StudentProfile

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# kind -> 用于确定 artifact id 的属性名（无则生成 UUID）
_KIND_ID_ATTR: dict[str, str] = {
    "source": "id",
    "evidence": "id",
    "claim": "id",
    "student_profile": "student_id",
    "professor_profile": "professor_id",
}


class Repository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._init_schema()

    # -- 连接与事务 -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as conn:
            with conn:
                yield conn

    def _init_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # -- 运行生命周期 ---------------------------------------------------------

    def create_run(self) -> str:
        run_id = str(uuid.uuid4())
        now = self._now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO runs (id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (run_id, "STARTED", now, now),
            )
        return run_id

    def set_run_status(self, run_id: str, status: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (status, self._now(), run_id),
            )

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def delete_run(self, run_id: str) -> None:
        # 只接受精确 UUID，拒绝路径或通配符
        try:
            uuid.UUID(run_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("run_id must be a valid UUID") from exc
        with self._tx() as conn:
            conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    def count_rows_for_run(self, run_id: str) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM artifacts WHERE run_id = ?", (run_id,)
            ).fetchone()
        return int(row["n"])

    # -- 持久化 artifact ------------------------------------------------------

    def save_artifact(
        self,
        run_id: str,
        kind: str,
        payload: BaseModel | dict[str, Any],
        artifact_id: str | None = None,
    ) -> str:
        data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        if artifact_id is None:
            attr = _KIND_ID_ATTR.get(kind)
            if attr and isinstance(payload, BaseModel):
                artifact_id = getattr(payload, attr, None)
        artifact_id = artifact_id or str(uuid.uuid4())
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO artifacts (id, run_id, kind, payload_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (artifact_id, run_id, kind, json.dumps(data, ensure_ascii=False), self._now()),
            )
            conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (self._now(), run_id))
        return artifact_id

    def save_source(self, run_id: str, source: SourceRecord) -> str:
        return self.save_artifact(run_id, "source", source, source.id)

    def save_evidence(self, run_id: str, evidence: Evidence) -> str:
        return self.save_artifact(run_id, "evidence", evidence, evidence.id)

    def save_claim(self, run_id: str, claim: Claim) -> str:
        return self.save_artifact(run_id, "claim", claim, claim.id)

    def save_student_profile(self, run_id: str, profile: StudentProfile) -> str:
        return self.save_artifact(run_id, "student_profile", profile, profile.student_id)

    def save_professor_profile(self, run_id: str, profile: ProfessorProfile) -> str:
        return self.save_artifact(run_id, "professor_profile", profile, profile.professor_id)

    def save_match(self, run_id: str, report: MatchReport) -> str:
        return self.save_artifact(run_id, "match", report)

    def save_draft(self, run_id: str, draft: Draft) -> str:
        return self.save_artifact(run_id, "draft", draft)

    # -- 读取 ----------------------------------------------------------------

    def load_artifacts(self, run_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            if kind is None:
                rows = conn.execute(
                    "SELECT payload_json FROM artifacts WHERE run_id = ? ORDER BY created_at",
                    (run_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload_json FROM artifacts WHERE run_id = ? AND kind = ?"
                    " ORDER BY created_at",
                    (run_id, kind),
                ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def load_latest(self, run_id: str, kind: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload_json FROM artifacts WHERE run_id = ? AND kind = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (run_id, kind),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None
