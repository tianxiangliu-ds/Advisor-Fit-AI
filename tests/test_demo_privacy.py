"""公开 Demo 的访客数据隔离。"""

import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings
from advisor_fit.demo_privacy import (
    cleanup_stale_visitor_dirs,
    uploads_dir_for_visitor,
    visible_run_ids,
)
from advisor_fit.models.match import Draft, MatchReport
from advisor_fit.models.professor import FactValue, ProfessorProfile
from advisor_fit.models.student import StudentProfile
from advisor_fit.storage.repository import Repository


def test_demo_visitor_sees_seed_records_and_own_records_only(tmp_path):
    repo = Repository(tmp_path / "app.db")
    seed_run = repo.create_run()
    own_run = repo.create_run()
    other_run = repo.create_run()
    repo.save_student_profile(seed_run, StudentProfile(student_id="demo_student", facts=[]))

    visible = visible_run_ids(repo, {own_run}, demo_mode=True)

    assert visible == {seed_run, own_run}
    assert other_run not in visible


def test_local_mode_keeps_the_existing_unfiltered_history(tmp_path):
    repo = Repository(tmp_path / "app.db")
    repo.create_run()

    assert visible_run_ids(repo, set(), demo_mode=False) is None


def test_demo_uploads_use_a_visitor_specific_directory(tmp_path):
    base = tmp_path / "uploads"

    first = uploads_dir_for_visitor(base, demo_mode=True, visitor_id="visitor-a")
    second = uploads_dir_for_visitor(base, demo_mode=True, visitor_id="visitor-b")

    assert first == base / "visitor-a"
    assert second == base / "visitor-b"
    assert first != second


def test_local_uploads_keep_the_existing_directory(tmp_path):
    base = tmp_path / "uploads"

    assert uploads_dir_for_visitor(base, demo_mode=False, visitor_id="ignored") == base


def test_demo_cleanup_removes_only_old_uuid_visitor_directories(tmp_path):
    old = tmp_path / "11111111-1111-4111-8111-111111111111"
    recent = tmp_path / "22222222-2222-4222-8222-222222222222"
    unrelated = tmp_path / "keep-me"
    for directory in (old, recent, unrelated):
        directory.mkdir()
        (directory / "cv.pdf").write_bytes(b"private")
    os.utime(old, (0, 0))
    os.utime(recent, (50 * 86400, 50 * 86400))
    os.utime(unrelated, (0, 0))

    removed = cleanup_stale_visitor_dirs(
        tmp_path,
        older_than_days=30,
        now=60 * 86400,
    )

    assert removed == [old]
    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_demo_history_does_not_show_another_visitors_record(tmp_path, monkeypatch):
    data_dir = tmp_path / "demo-data"
    repo = Repository(data_dir / "app.db")
    seed_run = repo.create_run(name="公开虚构记录")
    repo.save_student_profile(seed_run, StudentProfile(student_id="demo_student", facts=[]))
    repo.save_professor_profile(
        seed_run,
        ProfessorProfile(professor_id="demo", name=FactValue(value="虚构导师")),
    )
    repo.save_match(seed_run, MatchReport())
    repo.save_draft(seed_run, Draft())
    repo.set_run_status(seed_run, "COMPLETED")
    other_run = repo.create_run(name="另一位访客的私密记录")
    repo.save_student_profile(other_run, StudentProfile(student_id="private_student", facts=[]))
    repo.save_professor_profile(
        other_run,
        ProfessorProfile(professor_id="private", name=FactValue(value="私密导师")),
    )
    repo.save_match(other_run, MatchReport())
    repo.save_draft(other_run, Draft())
    repo.set_run_status(other_run, "COMPLETED")

    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "app_mode", "demo")
    app_path = str(Path(__file__).parents[1] / "app.py")
    app = AppTest.from_file(app_path).run(timeout=20)
    next(button for button in app.button if "研究档案" in button.label).click().run(timeout=20)

    labels = {button.label for button in app.button}
    download_labels = {button.label for button in app.get("download_button")}
    assert "公开虚构记录" in labels
    assert "另一位访客的私密记录" not in labels
    assert "💾 下载数据备份（.zip）" not in download_labels

    compare = next(
        item for item in app.get("button_group") if "选择要比较的导师" in item.label
    )
    assert any("虚构导师" in option for option in compare.options)
    assert not any("私密导师" in option for option in compare.options)


def test_demo_startup_cleans_expired_visitor_upload_directory(tmp_path, monkeypatch):
    data_dir = tmp_path / "demo-data"
    uploads_dir = tmp_path / "uploads"
    Repository(data_dir / "app.db")
    old = uploads_dir / "33333333-3333-4333-8333-333333333333"
    old.mkdir(parents=True)
    (old / "cv.pdf").write_bytes(b"private")
    os.utime(old, (0, 0))

    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "uploads_dir", uploads_dir)
    monkeypatch.setattr(settings, "app_mode", "demo")
    app_path = str(Path(__file__).parents[1] / "app.py")
    AppTest.from_file(app_path).run(timeout=20)

    assert not old.exists()
