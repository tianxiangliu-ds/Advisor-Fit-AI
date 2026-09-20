"""The visual workflow must expose the real app, one screen at a time."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    return AppTest.from_file(str(Path(__file__).parent.parent / "app.py")).run(timeout=15)


def test_landing_and_sidebar_routes_to_student_screen(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    assert not app.exception
    assert any("找到契合的导师" in item.value for item in app.markdown)
    assert not app.file_uploader

    next(button for button in app.button if "学生事实" in button.label).click().run()

    assert not app.exception
    assert app.file_uploader
    assert any("先确认" in item.value for item in app.title)


def test_professor_and_paper_pages_are_distinct(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    next(button for button in app.button if "导师档案" in button.label).click().run()
    assert not app.exception
    assert "导师姓名（必填）" in [item.label for item in app.text_input]
    assert not any("补录论文数量" == item.label for item in app.number_input)

    next(button for button in app.button if "论文核验" in button.label).click().run()
    assert not app.exception
    assert any(item.label == "补录论文数量" for item in app.number_input)


def test_professor_fields_survive_navigation_to_papers_and_back(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    next(button for button in app.button if "导师档案" in button.label).click().run()
    next(item for item in app.text_input if item.label == "导师姓名（必填）").set_value("王老师")
    next(item for item in app.text_input if item.label == "学校/单位（必填）").set_value("武汉大学")
    next(item for item in app.checkbox if "核对并确认" in item.label).set_value(True)
    app.run()
    next(button for button in app.button if "论文核验" in button.label).click().run()
    next(button for button in app.button if "导师档案" in button.label).click().run()

    assert not app.exception
    name = next(item for item in app.text_input if item.label == "导师姓名（必填）")
    institution = next(item for item in app.text_input if item.label == "学校/单位（必填）")
    assert name.value == "王老师"
    assert institution.value == "武汉大学"
    assert next(item for item in app.checkbox if "核对并确认" in item.label).value


def test_manual_paper_values_survive_leaving_evidence_page(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    next(button for button in app.button if "论文核验" in button.label).click().run()
    next(item for item in app.text_input if item.label == "论文标题（必填）").set_value("原文题目")
    next(item for item in app.text_area if item.label == "论文摘要（必填）").set_value("摘要")
    next(item for item in app.checkbox if "确认这篇论文" in item.label).set_value(True)
    app.run()
    next(button for button in app.button if "导师档案" in button.label).click().run()
    next(button for button in app.button if "论文核验" in button.label).click().run()

    assert not app.exception
    title = next(item for item in app.text_input if item.label == "论文标题（必填）")
    assert title.value == "原文题目"
    assert next(item for item in app.text_area if item.label == "论文摘要（必填）").value == "摘要"
    assert next(item for item in app.checkbox if "确认这篇论文" in item.label).value


def test_candidate_inspector_and_confirmation_share_selected_paper(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.session_state["candidate_papers"] = [
        {
            "title": "文化遗产知识图谱研究", "year": 2024,
            "abstract": "讨论语义组织与文化资源关联。",
            "source_url": "https://example.edu/paper", "source_platform": "万方",
            "venue": "情报科学", "authors": ["王老师"], "institution": "武汉大学",
            "belongs": True, "needs_review": False, "user_confirmed": False,
            "affiliation_note": "作者单位一致", "disambig_reason": "",
        }
    ]
    app.session_state["active_page"] = "papers"
    app.run()
    assert not app.exception
    assert any(item.label == "选择论文查看证据" for item in app.selectbox)
    assert any("作者单位一致" in item.value for item in app.caption)

    next(item for item in app.checkbox if "文化遗产知识图谱研究" in item.label).set_value(True)
    app.run()
    assert app.session_state["candidate_papers"][0]["user_confirmed"] is True


def test_new_professor_clears_old_draft_and_search_widgets(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.session_state["active_page"] = "professor"
    app.session_state["prof_name"] = "旧导师"
    app.session_state["draft_body"] = "旧导师专属邮件"
    app.session_state["search_mode"] = "zh"
    app.session_state["cand_paper_0"] = True
    app.run()
    next(button for button in app.button if "开始新导师" in button.label).click().run()

    assert not app.exception
    assert app.session_state["prof_name"] == ""
    assert app.session_state.get("draft_body") is None
    assert app.session_state.get("search_mode") is None
    assert app.session_state.get("cand_paper_0") is None


def test_report_and_email_require_a_generated_result(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    next(button for button in app.button if "匹配简报" in button.label).click().run()
    assert not app.exception
    assert any("生成简报" in item.value for item in app.info)

    next(button for button in app.button if "联系邮件" in button.label).click().run()
    assert not app.exception
    assert any("才会有" in item.value for item in app.info)


def test_confirmed_manual_paper_generates_real_report_after_page_switch(tmp_path, monkeypatch):
    from advisor_fit.models.common import FactStatus
    from advisor_fit.models.student import StudentFact, StudentProfile

    app = _app(tmp_path, monkeypatch)
    app.session_state["student"] = StudentProfile(
        student_id="s1",
        facts=[StudentFact(id="f1", field="skill", value="Python", status=FactStatus.FACT)],
    )
    app.session_state["student_fact_editor"] = [
        {"id": "f1", "confirmed": True, "field": "skill", "value": "Python"}
    ]
    app.session_state["student_name"] = "张同学"
    app.session_state["prof_name"] = "王老师"
    app.session_state["prof_institution"] = "武汉大学"
    app.session_state["identity_confirmed"] = True
    app.session_state["active_page"] = "papers"
    app.run()
    assert not app.exception

    title = next(item for item in app.text_input if item.label == "论文标题（必填）")
    title.set_value("知识图谱研究")
    next(item for item in app.text_area if item.label == "论文摘要（必填）").set_value(
        "使用 Python 构建知识图谱并开展数字人文研究。"
    )
    next(item for item in app.text_input if item.label == "来源链接（必填）").set_value(
        "https://example.edu/paper"
    )
    confirmed = next(item for item in app.checkbox if item.label == "我已确认这篇论文属于该导师")
    confirmed.set_value(True)
    app.run()
    next(button for button in app.button if button.label == "生成报告与邮件草稿").click().run()

    assert not app.exception
    assert not app.error, [item.value for item in app.error]
    assert app.session_state["result"] is not None
    assert len(app.session_state["result"].professor.recent_publications) == 1
    assert app.session_state["active_page"] == "report"
    assert any("证据来源" in item.value for item in app.subheader)

    next(button for button in app.button if "联系邮件" in button.label).click().run()
    assert not app.exception
    assert any(item.label == "邮件正文（可直接编辑）" for item in app.text_area)

    old_run_id = app.session_state["run_id"]
    next(button for button in app.button if "研究档案" in button.label).click().run()
    next(button for button in app.button if "王老师 · 武汉大学" in button.label).click().run()
    assert not app.exception
    assert any("证据来源" in item.value for item in app.subheader)
    assert any("查看该记录的邮件草稿" in item.label for item in app.expander)

    next(item for item in app.checkbox if "清空本次会话" in item.label).set_value(True).run()
    next(button for button in app.button if button.label == "清空全部数据").click().run()
    assert not app.exception
    assert app.session_state["run_id"] != old_run_id
    assert app.session_state["repo"].load_run(old_run_id) is None


def test_unconfirmed_paper_cannot_generate_report(tmp_path, monkeypatch):
    from advisor_fit.models.common import FactStatus
    from advisor_fit.models.student import StudentFact, StudentProfile

    app = _app(tmp_path, monkeypatch)
    app.session_state["student"] = StudentProfile(
        student_id="s1",
        facts=[StudentFact(id="f1", field="skill", value="Python", status=FactStatus.FACT)],
    )
    app.session_state["student_fact_editor"] = [
        {"id": "f1", "confirmed": True, "field": "skill", "value": "Python"}
    ]
    app.session_state["prof_name"] = "王老师"
    app.session_state["prof_institution"] = "武汉大学"
    app.session_state["identity_confirmed"] = True
    app.session_state["active_page"] = "papers"
    app.run()
    next(item for item in app.text_input if item.label == "论文标题（必填）").set_value("论文")
    next(item for item in app.text_area if item.label == "论文摘要（必填）").set_value("摘要")
    next(item for item in app.text_input if item.label == "来源链接（必填）").set_value(
        "https://example.edu/paper"
    )
    app.run()
    next(button for button in app.button if button.label == "生成报告与邮件草稿").click().run()

    assert not app.exception
    assert app.session_state["result"] is None
    assert any("请至少勾选确认一篇论文" in item.value for item in app.error)


def test_field_report_shows_missing_fields_without_blocking(tmp_path, monkeypatch):
    """只填姓名+学校也能看到字段清单：缺的标"未知"，不卡住流程。"""
    app = _app(tmp_path, monkeypatch)
    next(button for button in app.button if "导师档案" in button.label).click().run()
    next(item for item in app.text_input if item.label == "导师姓名（必填）").set_value("陆伟")
    next(item for item in app.text_input if item.label == "学校/单位（必填）").set_value("武汉大学")
    app.run()
    next(button for button in app.button if "检查信息补齐情况" in button.label).click().run()

    assert not app.exception
    assert any("导师信息补齐情况" in item.value for item in app.markdown)
    assert any("请手动补充" in item.value for item in app.markdown)
    assert any("已获取 2/6 个字段" in item.value for item in app.caption)
