"""在线 Demo 的部署配置测试：演示数据、入口顺序、依赖清单一致性。

这些测试守的是三件容易悄悄坏掉的事：

1. **公开 Demo 不能出现真实数据** —— 演示数据必须是虚构的、带"演示数据"标记；
2. **入口文件里环境变量必须先于应用导入** —— 顺序反了会去读真实数据目录；
3. **requirements.txt 不能和 pyproject.toml 漂移** —— 托管平台只读前者。
"""

from __future__ import annotations

from pathlib import Path

from advisor_fit.demo_data import (
    DEMO_ADVISORS,
    DEMO_PROFESSORS,
    DEMO_UNIVERSITIES,
    ensure_demo_data,
    is_seeded,
    seed_advisors,
    seed_runs,
)
from advisor_fit.storage.advisor_repo import AdvisorRepository
from advisor_fit.storage.repository import Repository

ROOT = Path(__file__).resolve().parents[1]


# ---------- 演示数据 ----------


def test_ensure_demo_data_creates_library_and_completed_runs(tmp_path):
    summary = ensure_demo_data(tmp_path / "demo")

    assert summary["created"] is True
    assert summary["advisors"] == len(DEMO_ADVISORS)
    assert summary["runs"] == len(DEMO_PROFESSORS)

    advisors = AdvisorRepository(tmp_path / "demo" / "advisors.db")
    assert advisors.count() == len(DEMO_ADVISORS)

    runs = Repository(tmp_path / "demo" / "app.db").list_runs()
    assert len(runs) == len(DEMO_PROFESSORS)
    assert all(run["status"] == "COMPLETED" for run in runs)
    assert all(run["name"] for run in runs)


def test_ensure_demo_data_is_idempotent(tmp_path):
    first = ensure_demo_data(tmp_path / "demo")
    second = ensure_demo_data(tmp_path / "demo")

    assert first["created"] is True
    assert second["created"] is False
    # 重复调用不会把数据写两遍
    assert AdvisorRepository(tmp_path / "demo" / "advisors.db").count() == len(DEMO_ADVISORS)


def test_seed_helpers_can_run_separately(tmp_path):
    assert seed_advisors(tmp_path) == len(DEMO_ADVISORS)
    assert len(seed_runs(tmp_path)) == len(DEMO_PROFESSORS)
    assert is_seeded(tmp_path) is True


def test_demo_data_is_fictional_and_labelled(tmp_path):
    """演示数据必须是虚构的，并且自己写明"这是演示数据"。"""
    seed_advisors(tmp_path)
    repo = AdvisorRepository(tmp_path / "advisors.db")
    people = repo.all_advisors()

    for person in people:
        assert person.university in DEMO_UNIVERSITIES, f"{person.name} 的学校不在演示学校名单里"
        assert "演示数据" in person.profile_text, f"{person.name} 缺少演示数据标记"
        assert person.homepage_url.startswith("https://example.edu.cn"), (
            "演示主页必须用 example 域名"
        )

    for professor in DEMO_PROFESSORS:
        assert professor.institution in DEMO_UNIVERSITIES
        for title, _year, abstract, url, _keywords in professor.papers:
            assert "示例" in title or "示例" in abstract
            assert url.startswith("https://example.org/"), "演示论文链接必须是 example 域名"


def test_demo_paper_links_are_valid_http_urls(tmp_path):
    from advisor_fit.ingest.manual_professor import ManualPaperInput

    for professor in DEMO_PROFESSORS:
        for title, year, abstract, url, keywords in professor.papers:
            paper = ManualPaperInput(
                title=title,
                year=year,
                abstract=abstract,
                source_url=url,
                keywords=list(keywords),
                user_confirmed=True,
            )
            assert paper.source_url.startswith("http")


# ---------- 入口文件 ----------


def test_entry_sets_data_dir_before_importing_the_app():
    """环境变量必须在导入 advisor_fit 之前设置，否则配置会读到真实数据目录。"""
    source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    lines = source.splitlines()

    def first_index(predicate) -> int | None:
        for index, line in enumerate(lines):
            if predicate(line):
                return index
        return None

    env_line = first_index(lambda line: 'os.environ.setdefault("DATA_DIR"' in line)
    import_line = first_index(lambda line: line.startswith("from advisor_fit"))
    assert env_line is not None, "入口文件没有设置 DATA_DIR"
    assert import_line is not None, "入口文件没有导入 advisor_fit"
    assert env_line < import_line, "DATA_DIR 必须在导入 advisor_fit 之前设置"
    assert 'os.environ.setdefault("UPLOADS_DIR"' in source
    assert 'os.environ.setdefault("APP_MODE", "demo")' in source


def test_entry_seeds_demo_data_and_runs_the_real_app():
    source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    assert "ensure_demo_data" in source
    assert 'runpy.run_path(str(ROOT / "app.py")' in source


# ---------- 部署产物 ----------


def test_requirements_txt_matches_pyproject():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from sync_requirements import pyproject_dependencies, requirements_dependencies

    assert requirements_dependencies() == pyproject_dependencies()


def test_deploy_artifacts_exist():
    assert (ROOT / "requirements.txt").is_file()
    assert (ROOT / "Dockerfile").is_file()
    assert (ROOT / ".dockerignore").is_file()

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements.txt" in dockerfile
    assert "streamlit_app.py" in dockerfile

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for must_ignore in (".venv/", "data/", "uploads/", ".env"):
        assert must_ignore in dockerignore, f".dockerignore 必须排除 {must_ignore}"
