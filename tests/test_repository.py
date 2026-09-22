"""Task 2：SQLite 证据账本与运行生命周期测试。"""

import uuid

import pytest

from advisor_fit.models.common import SourceRecord
from advisor_fit.models.evidence import Claim, ClaimStatus, Evidence
from advisor_fit.models.student import StudentProfile
from advisor_fit.storage.repository import Repository


def source_record() -> SourceRecord:
    return SourceRecord(
        id="src_1",
        source_type="official_page",
        url="https://u.edu/faculty/wang",
        title="王伟 - 计算机学院",
    )


def evidence_record() -> Evidence:
    return Evidence(
        id="ev_1",
        source_type="openalex",
        source_url="https://api.openalex.org/works/W123",
        title="Retrieval-Augmented Generation",
        published_date="2026-03-01",
        evidence_text="...",
    )


def supported_claim() -> Claim:
    return Claim(
        id="c1",
        text="近三年持续研究检索增强生成",
        claim_type="trend",
        status=ClaimStatus.SUPPORTED,
        evidence_ids=["ev_1"],
    )


def test_delete_run_removes_sources_evidence_and_claims(tmp_path):
    repo = Repository(tmp_path / "test.db")
    run_id = repo.create_run()
    repo.save_source(run_id, source_record())
    repo.save_evidence(run_id, evidence_record())
    repo.save_claim(run_id, supported_claim())

    assert repo.count_rows_for_run(run_id) == 3
    repo.delete_run(run_id)

    assert repo.load_run(run_id) is None
    assert repo.count_rows_for_run(run_id) == 0


def test_create_run_returns_uuid_and_status(tmp_path):
    repo = Repository(tmp_path / "test.db")
    run_id = repo.create_run()
    uuid.UUID(run_id)  # 不抛异常即合法 UUID
    run = repo.load_run(run_id)
    assert run["status"] == "STARTED"


def test_load_artifacts_by_kind(tmp_path):
    repo = Repository(tmp_path / "test.db")
    run_id = repo.create_run()
    repo.save_source(run_id, source_record())
    repo.save_evidence(run_id, evidence_record())

    sources = repo.load_artifacts(run_id, "source")
    assert len(sources) == 1
    assert sources[0]["id"] == "src_1"


def test_save_artifact_replaces_same_id(tmp_path):
    repo = Repository(tmp_path / "test.db")
    run_id = repo.create_run()
    repo.save_source(run_id, source_record())
    updated = source_record()
    updated.title = "更新后的标题"
    repo.save_source(run_id, updated)

    sources = repo.load_artifacts(run_id, "source")
    assert len(sources) == 1
    assert sources[0]["title"] == "更新后的标题"


def test_delete_run_rejects_path_like_id(tmp_path):
    repo = Repository(tmp_path / "test.db")
    with pytest.raises(ValueError):
        repo.delete_run("../etc/passwd")


def test_delete_missing_run_is_noop(tmp_path):
    repo = Repository(tmp_path / "test.db")
    repo.delete_run(str(uuid.uuid4()))  # 合法 UUID 但不存在，不应抛错


def test_list_runs_returns_created_runs(tmp_path):
    repo = Repository(tmp_path / "test.db")
    run1 = repo.create_run()
    run2 = repo.create_run()
    ids = {run["id"] for run in repo.list_runs()}
    assert {run1, run2} <= ids


def test_list_runs_can_be_limited_to_the_current_visitor(tmp_path):
    """公开 Demo 传入可见 ID 后，不能列出其他访客的研究记录。"""
    repo = Repository(tmp_path / "test.db")
    demo_run = repo.create_run(name="虚构演示记录")
    own_run = repo.create_run(name="本次访客记录")
    repo.create_run(name="另一位访客记录")

    listed = repo.list_runs(allowed_ids={demo_run, own_run})

    assert {run["id"] for run in listed} == {demo_run, own_run}


def test_find_run_ids_by_exact_artifact_field(tmp_path):
    """演示记录按结构化标记识别，不能靠可能重名的记录标题。"""
    repo = Repository(tmp_path / "test.db")
    demo_run = repo.create_run(name="相同标题")
    visitor_run = repo.create_run(name="相同标题")
    repo.save_student_profile(
        demo_run, StudentProfile(student_id="demo_student", facts=[])
    )
    repo.save_student_profile(
        visitor_run, StudentProfile(student_id="visitor_student", facts=[])
    )

    assert repo.find_run_ids_by_artifact_field(
        "student_profile", "student_id", "demo_student"
    ) == {demo_run}


def test_run_name_can_be_set_and_listed(tmp_path):
    repo = Repository(tmp_path / "test.db")
    run_id = repo.create_run(name="陆伟 · 武汉大学")
    assert repo.load_run(run_id)["name"] == "陆伟 · 武汉大学"

    repo.set_run_name(run_id, "陆伟 · 中国地质大学（武汉）")
    run = repo.load_run(run_id)
    assert run["name"] == "陆伟 · 中国地质大学（武汉）"

    listed = {run["id"]: run["name"] for run in repo.list_runs()}
    assert listed[run_id] == "陆伟 · 中国地质大学（武汉）"
