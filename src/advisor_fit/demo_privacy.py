"""公开 Demo 的访客可见范围与上传目录隔离。"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from advisor_fit.storage.repository import Repository


def visible_run_ids(
    repository: Repository,
    session_run_ids: set[str],
    *,
    demo_mode: bool,
) -> set[str] | None:
    """返回本次访客可看的记录；本地模式用 ``None`` 表示不过滤。"""
    if not demo_mode:
        return None
    seeded = repository.find_run_ids_by_artifact_field(
        "student_profile", "student_id", "demo_student"
    )
    return seeded | set(session_run_ids)


def uploads_dir_for_visitor(
    base_dir: Path | str,
    *,
    demo_mode: bool,
    visitor_id: str,
) -> Path:
    """公开 Demo 每位访客使用独立目录；本地模式保持原目录。"""
    base = Path(base_dir)
    return base / visitor_id if demo_mode else base


def cleanup_stale_visitor_dirs(
    base_dir: Path | str,
    *,
    older_than_days: float,
    now: float | None = None,
) -> list[Path]:
    """删除过期的 UUID 会话目录；其它目录一律不碰。"""
    base = Path(base_dir)
    if not base.is_dir():
        return []
    current = time.time() if now is None else now
    removed: list[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        try:
            uuid.UUID(child.name)
        except ValueError:
            continue
        age_days = (current - child.stat().st_mtime) / 86400
        if age_days < older_than_days:
            continue
        try:
            shutil.rmtree(child)
        except OSError:
            continue
        removed.append(child)
    return removed
