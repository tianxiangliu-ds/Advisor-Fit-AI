"""上传简历的清理规则：本地文件不能无限堆积。

约定：上传的简历一律命名为 `{run_id}.pdf`（run_id 是研究记录的 UUID）。
于是"还有人用"和"已经没人用"可以精确判断，不需要猜：

- **有对应研究记录的 PDF**：属于某条 run，永远不动（删数据时由删除流程处理）；
- **孤儿 PDF**（文件名是合法 UUID，但库里已经没有这条 run）：按保留期清理。
  在界面上会先告诉用户"有几份已无对应记录"，并提供一键清理；
- **其余文件**（不是 `{UUID}.pdf` 的）一律**不碰**——用户自己放进来的东西不能替他删。

保留期默认 30 天：给用户一个月的反悔时间，之后自动清理，避免无限堆积。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class UploadsReport:
    total_files: int = 0
    total_bytes: int = 0
    referenced_files: int = 0
    orphan_files: tuple[str, ...] = ()
    orphan_bytes: int = 0

    @property
    def orphan_count(self) -> int:
        return len(self.orphan_files)

    def describe(self) -> str:
        if not self.total_files:
            return "本机还没有保存任何简历。"
        text = (
            f"本机保存了 {self.total_files} 份简历（{human_size(self.total_bytes)}）"
            f"，其中 {self.referenced_files} 份仍对应着研究记录"
        )
        if self.orphan_count:
            text += (
                f"，{self.orphan_count} 份已无对应记录（{human_size(self.orphan_bytes)}）"
            )
        return text + "。"


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"  # pragma: no cover - 上面的循环已经覆盖


def is_run_pdf(path: Path) -> bool:
    """只认 `{合法UUID}.pdf`，其它文件名不参与清理（用户自己的文件不动）。"""
    if path.suffix.lower() != ".pdf":
        return False
    try:
        uuid.UUID(path.stem)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def scan_uploads(uploads_dir: Path | str, run_ids: Iterable[str]) -> UploadsReport:
    """盘点上传目录：多少份还在用、多少份已是孤儿、各占多大。"""
    directory = Path(uploads_dir)
    known = {str(run_id) for run_id in run_ids}
    if not directory.is_dir():
        return UploadsReport()

    total_files = total_bytes = referenced = 0
    orphans: list[str] = []
    orphan_bytes = 0
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not is_run_pdf(path):
            continue
        size = path.stat().st_size
        total_files += 1
        total_bytes += size
        if path.stem in known:
            referenced += 1
        else:
            orphans.append(path.name)
            orphan_bytes += size
    return UploadsReport(
        total_files=total_files,
        total_bytes=total_bytes,
        referenced_files=referenced,
        orphan_files=tuple(orphans),
        orphan_bytes=orphan_bytes,
    )


def cleanup_orphans(
    uploads_dir: Path | str,
    run_ids: Iterable[str],
    *,
    older_than_days: float = 0,
    now: float | None = None,
) -> list[Path]:
    """删除孤儿简历，返回被删掉的文件列表。

    older_than_days=0 表示"立刻删"（用户在界面上点了清理）；
    自动清理传 30，给用户留出反悔时间。
    """
    directory = Path(uploads_dir)
    if not directory.is_dir():
        return []
    known = {str(run_id) for run_id in run_ids}
    current = time.time() if now is None else now
    removed: list[Path] = []
    for name in scan_uploads(directory, known).orphan_files:
        path = directory / name
        try:
            age_days = (current - path.stat().st_mtime) / 86400
            if age_days < older_than_days:
                continue
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed
