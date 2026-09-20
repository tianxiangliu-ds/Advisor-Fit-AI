"""中文姓名判据测试（**产品侧**）。

分工说明：
- 这里只测产品自己的判据 `advisor_fit.ingest.cv.looks_like_chinese_name`——
  它服务于"用户输入的这份中文文本里哪个是姓名"。
- **网页导航词的完整拦截保证在爬取侧**（`../advisor-fit-crawl/tests/`）。
  因为要拦住「师资队伍」「武大主页」这类词，得靠爬虫自己的导航词表
  `NAME_STOPWORDS`；产品不该为了爬虫的需要去背这张表。
"""

from __future__ import annotations

import pytest

from advisor_fit.ingest.cv import looks_like_chinese_name


@pytest.mark.parametrize(
    "text",
    ["陆伟", "马费成", "冉从敬", "赵蓉英", "陈传夫", "欧阳修", "司马光", "黄如花"],
)
def test_accepts_real_names(text):
    assert looks_like_chinese_name(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "学院简介", "人才培养", "学院概况", "现任领导", "规章制度", "科学研究",
        "信息公开", "机构设置", "下页", "尾页", "详细", "字母检索", "系部检索",
        "博士后", "专职教师", "下载专区", "联系我们", "旧版",
    ],
)
def test_rejects_navigation_words(text):
    """这些是产品判据自己就能拦下的界面词。"""
    assert looks_like_chinese_name(text) is False


def test_rejects_latin_and_over_four_chars():
    assert looks_like_chinese_name("Smith") is False
    assert looks_like_chinese_name("欧阳修文博") is False
    assert looks_like_chinese_name("") is False


def test_rejects_middle_dot_names():
    """带间隔号的少数民族姓名暂按不识别处理，避免误判。"""
    assert looks_like_chinese_name("阿·依古丽") is False
