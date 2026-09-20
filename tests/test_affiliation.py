"""机构名比对测试：中英混排不判冲突、高校别名词典、原有包含规则。"""

from advisor_fit.providers.affiliation import (
    comparable,
    has_cjk,
    institutions_conflict,
    normalize_tokens,
)


def test_same_university_in_two_scripts_is_not_a_conflict():
    """中文库写「武汉大学」，国际库写「Wuhan University」——不是冲突。"""
    assert institutions_conflict("Wuhan University", "武汉大学") is False
    assert institutions_conflict("Wuhan University, School of Medicine", "武汉大学") is False


def test_cross_script_pairs_are_reported_as_not_comparable():
    assert comparable("Wuhan University", "武汉大学") is False
    assert comparable("武汉大学", "武汉大学信息管理学院") is True


def test_alias_dictionary_covers_common_universities():
    assert institutions_conflict("China Pharmaceutical University", "中国药科大学") is False
    assert institutions_conflict("Tsinghua University", "清华大学") is False
    hust = "Huazhong University of Science and Technology"
    assert institutions_conflict(hust, "华中科技大学") is False


def test_conflicting_same_script_institutions_are_flagged():
    assert institutions_conflict("中国药科大学", "武汉大学") is True
    assert institutions_conflict("南京大学", "武汉大学") is True


def test_missing_values_are_never_a_conflict():
    assert institutions_conflict("", "武汉大学") is False
    assert institutions_conflict("Wuhan University", None) is False
    assert institutions_conflict("   ", "   ") is False


def test_containment_still_counts_as_same_source():
    assert institutions_conflict("武汉大学信息管理学院", "武汉大学") is False


def test_has_cjk_and_tokenizer():
    assert has_cjk("武汉") is True
    assert has_cjk("Wuhan") is False
    assert normalize_tokens("Wuhan University, School of Medicine") == [
        "wuhan",
        "university",
        "school",
        "of",
        "medicine",
    ]
