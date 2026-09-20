"""姓名复核测试：界面词绝不能被当成人名。"""

from __future__ import annotations

import pytest

from advisor_fit.ingest.name_verify import (
    filter_candidates,
    has_ui_word,
    looks_like_person_name,
    verify_with_llm,
)
from advisor_fit.llm.provider import NullLLM

# 这些是项目负责人在真实爬取结果里发现的误判，必须全部拦掉
_REAL_WORLD_FALSE_POSITIVES = [
    "师生服务", "常用下载", "国内师资", "国外师资",
    "宣传片", "高端培训", "全职教师",
    "学院简介", "师资队伍", "人才培养", "研究方向", "现任领导",
    "下载专区", "联系我们", "招生信息", "学术动态",
    "通知公告", "党建工作", "东南大学", "学院概况", "荣休教师",
]


@pytest.mark.parametrize("word", _REAL_WORLD_FALSE_POSITIVES)
def test_rejects_real_world_false_positives(word):
    assert looks_like_person_name(word) is False


@pytest.mark.parametrize(
    "name",
    [
        "陆伟", "马费成", "安璐", "赵蓉英", "陈传夫", "黄如花", "欧阳修", "周裕",
        # 姓氏不在常见姓氏表里，但确实是真人名 —— 不能被过滤掉
        "闫永达", "玄玉波", "初剑峰", "随阳轶", "呼咏", "台桂香", "豆志河",
        # 社区数据里带空格的写法
        "薛 渊",
    ],
)
def test_accepts_real_names(name):
    assert looks_like_person_name(name) is True


def test_normalises_whitespace_in_names():
    from advisor_fit.ingest.name_verify import normalize_name

    assert normalize_name("薛 渊") == "薛渊"
    assert normalize_name(" 陆伟 ") == "陆伟"


def test_common_surname_is_only_a_hint():
    from advisor_fit.ingest.name_verify import has_common_surname

    assert has_common_surname("陆伟") is True
    assert has_common_surname("闫永达") is False, "罕见姓氏被识别为不在表内"
    assert looks_like_person_name("闫永达") is True, "但不影响它被当作真人名保留"


def test_reports_which_ui_root_matched():
    assert has_ui_word("师生服务") == "服务"
    assert has_ui_word("高端培训") == "培训"
    assert has_ui_word("陆伟") == ""


def test_rejects_non_chinese_and_wrong_length():
    assert looks_like_person_name("Smith") is False
    assert looks_like_person_name("欧阳修文博") is False
    assert looks_like_person_name("") is False


def test_filter_candidates_splits_and_dedupes():
    kept, dropped = filter_candidates(
        ["陆伟", "师生服务", "陆伟", "高端培训", "安璐", "常用下载"]
    )

    assert kept == ["陆伟", "安璐"]
    assert set(dropped) == {"师生服务", "高端培训", "常用下载"}


def test_llm_verify_is_skipped_when_model_unavailable():
    """没有 API Key 时，复核必须原样放行，不能把数据清空。"""
    names = ["陆伟", "安璐"]

    assert verify_with_llm(NullLLM(), names) == names


class _FakeVerifyLLM:
    def __init__(self, keep):
        self.keep = keep

    def generate(self, *, schema, instructions, payload):
        return schema(keep=self.keep)


def test_llm_verify_keeps_only_confirmed_names():
    llm = _FakeVerifyLLM(["陆伟"])

    assert verify_with_llm(llm, ["陆伟", "安璐"]) == ["陆伟"]


def test_llm_verify_ignores_unknown_names_from_the_model():
    llm = _FakeVerifyLLM(["陆伟", "模型编的名字"])

    assert verify_with_llm(llm, ["陆伟", "安璐"]) == ["陆伟"]
