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


# -- 卡片式链接（整张人物卡片是一个 <a>）--------------------------------------


@pytest.mark.parametrize(
    ("text", "name", "title", "email"),
    [
        (
            "龚韵武汉大学空间科学与技术系副主任，教授yun.gong@whu.edu.cn",
            "龚韵", "教授", "yun.gong@whu.edu.cn",
        ),
        (
            "陈燕鸣武汉大学动力与机械学院副教授chenyanming@whu.edu.cn",
            "陈燕鸣", "副教授", "chenyanming@whu.edu.cn",
        ),
        ("程磊教授Lei.Cheng@whu.edu.cn", "程磊", "教授", "Lei.Cheng@whu.edu.cn"),
        ("付松教授fusion@whu.edu.cn", "付松", "教授", "fusion@whu.edu.cn"),
    ],
)
def test_parse_card_link_extracts_name_title_email(text, name, title, email):
    from advisor_fit.ingest.name_verify import parse_card_link

    parsed = parse_card_link(text)

    assert parsed is not None
    assert parsed["name"] == name
    assert parsed["title"] == title
    assert parsed["email"] == email


def test_parse_card_link_handles_department_note_before_title():
    from advisor_fit.ingest.name_verify import parse_card_link

    parsed = parse_card_link(
        "丁浩武汉大学地球与空间科学技术学院，副院长，教授dhaosgg@sgg.whu.edu.cn"
    )

    assert parsed is not None
    assert parsed["name"] == "丁浩"
    assert parsed["email"] == "dhaosgg@sgg.whu.edu.cn"


def test_parse_card_link_keeps_foreign_names():
    from advisor_fit.ingest.name_verify import parse_card_link

    parsed = parse_card_link("GhamgeenIzatRashed(阿部)副教授ghamgeen@whu.edu.cn")

    assert parsed is not None
    assert parsed["name"] == "GhamgeenIzatRashed"
    assert parsed["email"] == "ghamgeen@whu.edu.cn"


def test_parse_card_link_ignores_navigation_cards():
    from advisor_fit.ingest.name_verify import parse_card_link

    assert parse_card_link("学生就业指导与服务中心") is None


# -- 规范化（保人，不删人）----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("董陇军副教授", "董陇军"),
        ("刘志祥教授", "刘志祥"),
        ("工研院胡耀武", "胡耀武"),
        ("薛 渊", "薛渊"),
        ("陆伟", "陆伟"),
        # 采集时"姓名"会和表头"职称"粘在一起（见 2026-09 数据清洗事故）
        ("何鹏职称", "何鹏"),
        ("谭璇职称", "谭璇"),
    ],
)
def test_normalize_person_name_keeps_the_person(raw, normalized):
    from advisor_fit.ingest.name_verify import normalize_person_name

    assert normalize_person_name(raw) == normalized


@pytest.mark.parametrize(
    "raw",
    ["董陇军副教授", "刘志祥教授", "工研院胡耀武", "何鹏职称", "杨虹职称"],
)
def test_names_with_title_or_prefix_are_not_auto_deleted(raw):
    """带职称/单位前缀的不是垃圾，规范化后要保住人，不能删。"""
    from advisor_fit.ingest.name_verify import is_safe_to_auto_delete

    assert is_safe_to_auto_delete(raw) is False


@pytest.mark.parametrize(
    "raw",
    ["郭新闻", "李文化", "方向忠", "闫永达", "玄玉波", "Kok-MengLee", "AndersLindquist",
     "龚韵", "陈燕鸣",
     # 「张学工」是清华大学真实教授。曾经的"学工"词根会在清洗时把他删掉——
     # 那次事故说明：词根必须经得起"它会不会出现在真人姓名里"这一问。
     "张学工", "春雷"],
)
def test_real_names_and_rare_surnames_are_not_auto_deleted(raw):
    """含「新闻/文化/方向」字样或罕见姓氏、外籍姓名的，都是真人，绝不能自动删。"""
    from advisor_fit.ingest.name_verify import is_safe_to_auto_delete

    assert is_safe_to_auto_delete(raw) is False


@pytest.mark.parametrize(
    "raw",
    ["全部导师", "电信学院大部分导师", "计算机学院大部分老师", "某院放疗科",
     "CYC", "ZYT", "邓 * ling"],
)
def test_column_names_and_junk_are_safe_to_auto_delete(raw):
    from advisor_fit.ingest.name_verify import is_safe_to_auto_delete

    assert is_safe_to_auto_delete(raw) is True


@pytest.mark.parametrize(
    "raw",
    ["师生服务", "常用下载", "师资队伍", "现任领导", "学院简介", "宣传片", "高端培训", "全职教师"],
)
def test_ui_words_are_safe_to_auto_delete(raw):
    from advisor_fit.ingest.name_verify import is_safe_to_auto_delete

    assert is_safe_to_auto_delete(raw) is True


def test_ui_root_never_matches_a_real_name():
    """词根必须经得起这一问："它会不会出现在真人姓名里？"

    反面教材是"学工"：它能拦「学工资料」，但「张学工」是清华大学的真实教授，
    加进安全词表就会在采集与清洗时把人删掉。拦「学工资料」交给更安全的"资料"。
    """
    from advisor_fit.ingest.name_verify import SAFE_UI_ROOTS, has_ui_word

    assert "学工" not in SAFE_UI_ROOTS, "「学工」会误伤「张学工」这类真名"
    assert "资料" in SAFE_UI_ROOTS, "「学工资料」要靠更安全的「资料」拦住"

    assert has_ui_word("学工资料") or "资料" in "学工资料"


def test_nav_words_that_look_like_names_are_still_caught():
    """去掉"学工"之后，真正的界面词不能因此漏网。"""
    from advisor_fit.ingest.name_verify import is_safe_to_auto_delete

    for word in ("学工资料", "教工家园", "院友风采", "校园风光", "两院院士"):
        assert is_safe_to_auto_delete(word) is True, f"{word} 应当被拦下"
