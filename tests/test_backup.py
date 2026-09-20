"""数据备份逻辑测试：默认排除敏感内容、zip 内容可读、恢复说明齐全。"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

from advisor_fit.storage.backup import (
    DEFAULT_BACKUP_FILES,
    collect_entries,
    default_backup_name,
    manifest_text,
    write_backup,
)


def _make_tree(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "app.db").write_bytes(b"app-db")
    (data / "page_cache.db").write_bytes(b"cache")
    (data / "advisors.db").write_bytes(b"big")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "11111111-1111-1111-1111-111111111111.pdf").write_bytes(b"cv")
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")
    return tmp_path


def test_default_backup_excludes_secrets_and_uploads(tmp_path):
    _make_tree(tmp_path)
    entries, missing, skipped = collect_entries(tmp_path)

    names = [entry.arcname for entry in entries]
    assert names == [f"data/{name}" for name in DEFAULT_BACKUP_FILES]
    assert missing == []
    assert any(".env" in item for item in skipped)
    assert any("uploads" in item for item in skipped)
    assert any("大库" in item for item in skipped)


def test_optional_flags_pull_in_the_rest(tmp_path):
    _make_tree(tmp_path)
    entries, _, skipped = collect_entries(
        tmp_path, include_big=True, include_uploads=True, include_env=True
    )

    names = {entry.arcname for entry in entries}
    assert "data/advisors.db" in names
    assert ".env" in names
    assert any(name.startswith("uploads/") for name in names)
    assert skipped == []


def test_missing_files_are_reported_not_fatal(tmp_path):
    entries, missing, _ = collect_entries(tmp_path)
    assert entries == []
    assert set(missing) == set(DEFAULT_BACKUP_FILES)


def test_zip_contains_database_and_manifest(tmp_path):
    _make_tree(tmp_path)
    entries, _, skipped = collect_entries(tmp_path)
    manifest = manifest_text(entries, skipped, version="0.2.0")
    target = write_backup(tmp_path / "backups" / "b.zip", entries, manifest)

    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert "data/app.db" in names
        assert "MANIFEST.md" in names
        text = archive.read("MANIFEST.md").decode("utf-8")

    assert "0.2.0" in text
    assert "怎么恢复" in text
    assert "不含 `.env`" in text


def test_manifest_lists_excluded_content(tmp_path):
    _make_tree(tmp_path)
    entries, _, skipped = collect_entries(tmp_path)
    text = manifest_text(
        entries, skipped, version="0.2.0", created_at=datetime(2026, 1, 2, 3, 4, 5)
    )
    assert "2026-01-02 03:04:05" in text
    assert "data/app.db" in text
    assert "没有包含的内容" in text


def test_backup_bytes_are_a_valid_zip(tmp_path):
    from advisor_fit.storage.backup import build_backup_bytes

    _make_tree(tmp_path)
    entries, _, skipped = collect_entries(tmp_path)
    payload = build_backup_bytes(entries, manifest_text(entries, skipped, version="0.2.0"))
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert "data/page_cache.db" in archive.namelist()


def test_default_backup_name_is_timestamped():
    name = default_backup_name("afa", when=datetime(2026, 1, 2, 3, 4, 5))
    assert name == "afa-backup-20260102-030405.zip"
