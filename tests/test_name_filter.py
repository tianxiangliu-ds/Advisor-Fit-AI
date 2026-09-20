"""中文姓名判据测试：用于从网页链接里筛导师姓名，滤掉导航词。"""

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
        "学院简介", "师资队伍", "人才培养", "学院概况", "现任领导", "党群工作",
        "规章制度", "科学研究", "信息公开", "国际交流", "机构设置", "荣休教师",
        "下页", "尾页", "详细", "字母检索", "系部检索", "博士后", "专职教师",
        "下载专区", "联系我们", "人才培养", "武大主页", "旧版",
    ],
)
def test_rejects_navigation_words(text):
    """这些词的首字有些本身就是姓氏（党/国/武），必须靠爬虫侧的导航词表拦掉。"""
    from scripts.crawl_university import NAME_STOPWORDS

    assert text in NAME_STOPWORDS or looks_like_chinese_name(text) is False


def test_rejects_latin_and_over_four_chars():
    assert looks_like_chinese_name("Smith") is False
    assert looks_like_chinese_name("欧阳修文博") is False
    assert looks_like_chinese_name("") is False


def test_rejects_middle_dot_names():
    """带间隔号的少数民族姓名暂按不识别处理，避免误判。"""
    assert looks_like_chinese_name("阿·依古丽") is False
