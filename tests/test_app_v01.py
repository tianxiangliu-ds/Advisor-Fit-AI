"""v0.1 Streamlit 主流程的最小页面契约。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings


def test_app_defaults_to_manual_professor_and_paper_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    app_path = Path(__file__).parent.parent / "app.py"

    app = AppTest.from_file(str(app_path)).run(timeout=10)

    assert not app.exception
    assert any("找到契合的导师" in item.value for item in app.markdown)
    next(button for button in app.button if "导师档案" in button.label).click().run()
    assert not app.exception
    labels = [item.label for item in app.text_input]
    buttons = [item.label for item in app.button]
    assert "导师姓名（必填）" in labels
    assert "学校/单位（必填）" in labels
    assert "抓取官方页并召回作者候选" not in buttons


def test_fact_editor_renders_when_student_present(tmp_path, monkeypatch):
    from advisor_fit.models.common import FactStatus
    from advisor_fit.models.student import StudentFact, StudentProfile

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    app_path = Path(__file__).parent.parent / "app.py"

    app = AppTest.from_file(str(app_path)).run(timeout=10)
    student = StudentProfile(
        student_id="s1",
        facts=[StudentFact(id="f1", field="skill", value="Python", status=FactStatus.FACT)],
    )
    app.session_state["student"] = student
    app.session_state["student_fact_editor"] = [
        {"confirmed": False, "field": "skill", "value": "Python"}
    ]
    app.session_state["active_page"] = "resume"
    app.run()

    assert not app.exception
    assert any("先确认" in item.value for item in app.title)
