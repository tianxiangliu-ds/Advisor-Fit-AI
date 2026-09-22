"""论文研究入口应先简单，只有需要纠错时才展示高级字段。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def _papers_page(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    app = AppTest.from_file(APP_PATH).run(timeout=15)
    app.session_state["active_page"] = "papers"
    return app.run(timeout=15)


def _input_labels(app: AppTest) -> list[str]:
    return [item.label for item in [*app.text_input, *app.text_area]]


def test_advanced_search_fields_are_hidden_before_first_research(tmp_path, monkeypatch):
    """若默认又显示三个纠错字段，这个测试会失败。"""
    app = _papers_page(tmp_path, monkeypatch)

    assert not app.exception
    labels = _input_labels(app)
    assert not any("导师英文名" in label for label in labels)
    assert not any("检索用机构" in label for label in labels)
    assert not any("代表论文标题" in label for label in labels)
    assert any(button.label == "🔎 开始一键研究" for button in app.button)


def test_research_requires_a_completed_professor_profile(tmp_path, monkeypatch):
    """没有导师姓名和学校时，不能静默点击一键研究。"""
    app = _papers_page(tmp_path, monkeypatch)

    next(button for button in app.button if button.label == "🔎 开始一键研究").click().run()

    assert any("请先完善导师档案" in item.value for item in app.error)


def test_user_can_reveal_correction_fields_and_sees_generic_example(tmp_path, monkeypatch):
    """若纠错入口消失或示例仍写特定低通用性学校，这个测试会失败。"""
    app = _papers_page(tmp_path, monkeypatch)
    next(button for button in app.button if "补充纠错信息" in button.label).click().run()

    assert not app.exception
    labels = _input_labels(app)
    assert any("导师英文名" in label for label in labels)
    assert any("检索用机构" in label and "清华大学" in label for label in labels)
    assert any("代表论文标题" in label for label in labels)


def test_user_can_hide_correction_fields_again(tmp_path, monkeypatch):
    """补充线索不是一次性展开后永久占据页面。"""
    app = _papers_page(tmp_path, monkeypatch)
    next(button for button in app.button if "补充纠错信息" in button.label).click().run()

    next(button for button in app.button if button.label == "收起补充线索").click().run()

    assert not app.exception
    assert not any("导师英文名" in label for label in _input_labels(app))


def test_completed_research_uses_a_distinct_rerun_button(tmp_path, monkeypatch):
    """若研究前后仍使用同一按钮文案，这个测试会失败。"""
    app = _papers_page(tmp_path, monkeypatch)
    app.session_state["_research_sources"] = "OpenAlex 3 篇"
    app.session_state["candidate_papers"] = [
        {
            "title": "可核验论文",
            "year": 2025,
            "abstract": "摘要",
            "source_url": "https://example.edu/paper",
            "source_platform": "OpenAlex",
            "authors": ["王老师"],
            "institution": "清华大学",
            "belongs": True,
            "needs_review": False,
            "user_confirmed": False,
        }
    ]
    app.run(timeout=15)

    assert not app.exception
    assert any(button.label == "✓ 研究已完成 · 重新研究" for button in app.button)
    assert not any(button.label == "🔎 开始一键研究" for button in app.button)


def test_completed_research_shows_the_nonempty_clues_it_used(tmp_path, monkeypatch):
    """用户要能知道额外提供的线索是否真的进入了这次检索。"""
    app = _papers_page(tmp_path, monkeypatch)
    app.session_state["_research_sources"] = "OpenAlex 3 篇"
    app.session_state["_research_clues"] = ["英文名：Yongchao Xu", "官网论文题名：2 篇"]
    app.run(timeout=15)

    captions = "\n".join(str(item.value) for item in app.caption)
    assert "本次使用的补充线索" in captions
    assert "英文名：Yongchao Xu" in captions
