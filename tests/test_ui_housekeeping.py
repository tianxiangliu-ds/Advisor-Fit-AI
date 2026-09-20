"""版本号显示、简历清理入口、数据备份入口的端到端 UI 测试。"""

from __future__ import annotations

import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit import __version__
from advisor_fit.config import settings
from advisor_fit.storage.uploads import DEFAULT_RETENTION_DAYS

ORPHAN = "33333333-3333-3333-3333-333333333333.pdf"


def _app(tmp_path, monkeypatch) -> AppTest:
    # 不要预售假数据库文件：应用启动时会自己建好真的 app.db（备份按钮要用到它）
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    return AppTest.from_file(str(Path(__file__).parent.parent / "app.py")).run(timeout=20)


def _go(app: AppTest, label: str) -> AppTest:
    next(button for button in app.button if label in button.label).click().run()
    return app


def test_sidebar_shows_the_program_version(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    assert not app.exception
    assert any(f"v{__version__}" in item.value for item in app.markdown)


def test_resume_page_explains_the_upload_cleanup_rule(tmp_path, monkeypatch):
    app = _go(_app(tmp_path, monkeypatch), "学生事实")

    assert not app.exception
    assert any("本机简历文件" in item.label for item in app.expander)


def test_orphan_upload_is_reported_and_can_be_cleaned(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    uploads = settings.uploads_dir
    uploads.mkdir(parents=True, exist_ok=True)
    orphan = uploads / ORPHAN
    orphan.write_bytes(b"cv-bytes")
    # 让它的修改时间是"刚刚"，确保没被启动时的保留期清理带走
    import os

    stamp = time.time()
    os.utime(orphan, (stamp, stamp))

    app = _go(app, "学生事实")

    assert not app.exception
    captions = " ".join(item.value for item in app.caption)
    assert "已无对应记录" in captions

    next(button for button in app.button if "立即清理" in button.label).click().run()

    assert not app.exception
    assert not orphan.exists()


def test_live_run_upload_is_never_cleaned(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    uploads = settings.uploads_dir
    uploads.mkdir(parents=True, exist_ok=True)
    live = uploads / f"{app.session_state['run_id']}.pdf"
    live.write_bytes(b"cv-bytes")

    _go(app, "学生事实")

    assert live.exists()


def test_retention_window_is_documented_in_the_page(tmp_path, monkeypatch):
    app = _go(_app(tmp_path, monkeypatch), "学生事实")
    captions = " ".join(item.value for item in app.caption)
    assert f"{DEFAULT_RETENTION_DAYS} 天" in captions


def test_history_page_offers_a_data_backup_download(tmp_path, monkeypatch):
    app = _go(_app(tmp_path, monkeypatch), "研究档案")

    assert not app.exception
    downloads = app.get("download_button")
    assert downloads
    assert "备份" in downloads[0].label
    captions = " ".join(item.value for item in app.caption)
    assert "不含" in captions and "API Key" in captions
