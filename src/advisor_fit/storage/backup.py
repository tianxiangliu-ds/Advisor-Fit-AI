"""数据备份：把本地数据打成 zip（命令行脚本与界面共用同一套逻辑）。

设计原则：**默认只备份"丢了最麻烦、体积又小"的运行数据**（研究记录 + 网页缓存）。
API Key（`.env`）与真实简历（`uploads/`）默认排除——它们属于敏感内容，
必须由用户显式要求才会进备份包；三个大库（导师库等）可以用采集脚本重建，
默认也不打包。
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 默认备份：运行数据（研究记录、证据、网页缓存）
DEFAULT_BACKUP_FILES: tuple[str, ...] = ("app.db", "page_cache.db")
# 大库：可用采集脚本重建，默认不打包
BIG_BACKUP_FILES: tuple[str, ...] = ("advisors.db", "faculty.db", "supervisor_roster.db")


@dataclass(frozen=True)
class BackupEntry:
    path: Path
    arcname: str


def collect_entries(
    root: Path,
    *,
    include_big: bool = False,
    include_uploads: bool = False,
    include_env: bool = False,
) -> tuple[list[BackupEntry], list[str], list[str]]:
    """挑出要备份的文件。

    返回 (要打包的文件, 缺失的文件名, 刻意排除的说明)。
    """
    data_dir = root / "data"
    names = list(DEFAULT_BACKUP_FILES) + (list(BIG_BACKUP_FILES) if include_big else [])
    entries: list[BackupEntry] = []
    missing: list[str] = []
    for name in names:
        path = data_dir / name
        if path.is_file():
            entries.append(BackupEntry(path=path, arcname=f"data/{name}"))
        else:
            missing.append(name)

    if include_uploads:
        for path in sorted((root / "uploads").glob("*.pdf")):
            entries.append(BackupEntry(path=path, arcname=f"uploads/{path.name}"))

    if include_env and (root / ".env").is_file():
        entries.append(BackupEntry(path=root / ".env", arcname=".env"))

    skipped: list[str] = []
    if not include_env:
        skipped.append("`.env`（含 API Key，加 --include-env 才会包含）")
    if not include_uploads:
        skipped.append("`uploads/`（含真实简历，加 --include-uploads 才会包含）")
    if not include_big:
        skipped.append("大库 advisors/faculty/supervisor_roster（加 --all 才会包含）")
    return entries, missing, skipped


def manifest_text(
    entries: list[BackupEntry],
    skipped: list[str],
    *,
    version: str = "",
    created_at: datetime | None = None,
) -> str:
    when = (created_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    lines = ["# Advisor-Fit AI 数据备份", ""]
    lines.append(f"- 备份时间：{when}")
    if version:
        lines.append(f"- 程序版本：{version}")
    lines += ["", "## 包含的内容", ""]
    lines += [f"- `{entry.arcname}`" for entry in entries] or ["- （空）"]
    if skipped:
        lines += ["", "## 没有包含的内容（按设计排除）", ""]
        lines += [f"- {item}" for item in skipped]
    lines += [
        "",
        "## 怎么恢复",
        "",
        "1. 把 zip 解压回项目根目录，数据库文件放进 `data/`；",
        "2. 重新启动应用即可。`app.db` 里是研究记录与证据；",
        "3. 大库缺失时用采集脚本重建，不影响已有记录。",
        "",
        "> 注意：备份包默认不含 `.env`，恢复后需要自己重新填 API Key。",
        "",
    ]
    return "\n".join(lines)


def build_backup_bytes(entries: list[BackupEntry], manifest: str) -> bytes:
    """在内存里生成备份包（界面上的"下载备份"用）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            archive.write(entry.path, entry.arcname)
        archive.writestr("MANIFEST.md", manifest)
    return buffer.getvalue()


def write_backup(target: Path, entries: list[BackupEntry], manifest: str) -> Path:
    """把备份写到磁盘（命令行脚本用）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_backup_bytes(entries, manifest))
    return target


def default_backup_name(prefix: str = "advisor-fit", *, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-backup-{stamp}.zip"
