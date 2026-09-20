"""上传简历清理规则测试（不联网、只碰 tmp_path）。"""

from __future__ import annotations

import time
import uuid

from advisor_fit.storage.uploads import (
    cleanup_orphans,
    human_size,
    is_run_pdf,
    scan_uploads,
)

RUN_A = "11111111-1111-1111-1111-111111111111"
RUN_B = "22222222-2222-2222-2222-222222222222"


def _write(directory, name: str, *, age_days: float = 0.0, size: int = 10):
    path = directory / name
    path.write_bytes(b"x" * size)
    if age_days:
        stamp = time.time() - age_days * 86400
        import os

        os.utime(path, (stamp, stamp))
    return path


def test_only_uuid_pdf_files_are_managed(tmp_path):
    assert is_run_pdf(tmp_path / f"{RUN_A}.pdf") is True
    assert is_run_pdf(tmp_path / "简历.pdf") is False
    assert is_run_pdf(tmp_path / "notes.txt") is False
    assert is_run_pdf(tmp_path / "1111.pdf") is False


def test_scan_counts_referenced_and_orphans(tmp_path):
    _write(tmp_path, f"{RUN_A}.pdf", size=100)
    _write(tmp_path, "33333333-3333-3333-3333-333333333333.pdf", size=50)
    _write(tmp_path, "我自己放的资料.pdf", size=999)  # 不是 UUID 命名，不受管理

    report = scan_uploads(tmp_path, [RUN_A])

    assert report.total_files == 2
    assert report.total_bytes == 150
    assert report.referenced_files == 1
    assert report.orphan_count == 1
    assert report.orphan_bytes == 50
    assert "已无对应记录" in report.describe()


def test_scan_handles_missing_directory(tmp_path):
    report = scan_uploads(tmp_path / "not-there", [RUN_A])
    assert report.total_files == 0
    assert report.orphan_count == 0
    assert "还没有保存任何简历" in report.describe()


def test_cleanup_removes_only_orphans(tmp_path):
    kept = _write(tmp_path, f"{RUN_A}.pdf")
    orphan = _write(tmp_path, f"{RUN_B}.pdf")
    mine = _write(tmp_path, "我的笔记.pdf")

    removed = cleanup_orphans(tmp_path, [RUN_A])

    assert removed == [orphan]
    assert kept.exists()
    assert mine.exists()
    assert not orphan.exists()


def test_cleanup_respects_retention_window(tmp_path):
    fresh_orphan = _write(tmp_path, f"{RUN_B}.pdf", age_days=1)
    old_orphan = _write(tmp_path, "44444444-4444-4444-4444-444444444444.pdf", age_days=40)

    removed = cleanup_orphans(tmp_path, [], older_than_days=30)

    assert removed == [old_orphan]
    assert fresh_orphan.exists()
    assert not old_orphan.exists()


def test_cleanup_never_touches_files_of_live_runs(tmp_path):
    live = _write(tmp_path, f"{RUN_A}.pdf", age_days=365)
    assert cleanup_orphans(tmp_path, [RUN_A], older_than_days=30) == []
    assert live.exists()


def test_cleanup_is_safe_when_directory_is_missing(tmp_path):
    assert cleanup_orphans(tmp_path / "nope", [], older_than_days=30) == []


def test_human_size_reads_naturally():
    assert human_size(0) == "0B"
    assert human_size(512) == "512B"
    assert human_size(2048) == "2.0KB"
    assert human_size(5 * 1024 * 1024) == "5.0MB"


def test_uuid_helpers_accept_real_run_ids(tmp_path):
    real = _write(tmp_path, f"{uuid.uuid4()}.pdf")
    assert scan_uploads(tmp_path, [real.stem]).referenced_files == 1
