"""研究档案页：卡片筛选、单一比较入口与联系提醒。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from advisor_fit.config import settings
from advisor_fit.demo_data import ensure_demo_data

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def test_archive_uses_cards_filters_and_one_comparison_area(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    ensure_demo_data(data_dir)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")

    app = AppTest.from_file(APP_PATH).run(timeout=20)
    next(button for button in app.button if "研究档案" in button.label).click().run(
        timeout=20
    )

    assert not app.exception
    assert any(item.label == "搜索导师、学校或学院" for item in app.text_input)
    assert any(item.label == "学校" for item in app.selectbox)
    assert any(item.label == "学院" for item in app.selectbox)
    assert not any("横向比较已完成记录" in item.label for item in app.button)
    compare = next(
        item for item in app.get("button_group") if "选择要比较的导师" in item.label
    )
    assert len(compare.options) >= 2
    compare.set_value(compare.options[:2]).run(timeout=20)

    assert not app.exception
    assert app.dataframe
    rendered = "\n".join(
        str(item.value) for item in [*app.markdown, *app.info, *app.warning, *app.error]
    )
    assert "联系碰撞提醒" in rendered
