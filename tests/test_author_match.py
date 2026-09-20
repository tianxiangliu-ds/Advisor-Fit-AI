"""人名比对测试：模糊匹配带回来的"同姓别人"必须被挡在门外。"""

from advisor_fit.providers.author_match import (
    author_matches,
    cjk_runs,
    name_tokens,
    normalize_person_name,
    split_author_entry,
)


def test_chinese_full_name_matches_ignoring_spaces():
    assert author_matches("陆伟", ["陆伟"]) is True
    assert author_matches("陆伟", ["陆 伟"]) is True
    assert author_matches("陆伟", ["陆鑫"]) is False
    assert author_matches("陆伟", ["陆长峰", "许英晨"]) is False


def test_chinese_name_does_not_match_a_longer_name():
    """「陆伟」不能命中「陆伟明」——子串匹配会放进完全不相干的论文。"""
    assert author_matches("陆伟", ["陆伟明"]) is False
    assert author_matches("陆伟", ["张陆伟"]) is False


def test_chinese_name_matches_inside_mixed_author_string():
    """OpenAlex 的原始署名常是「蒋春 Jiang Chun」这种中英混排。"""
    assert author_matches("蒋春", ["蒋春 Jiang Chun", "陈泉安 Chen Quanan"]) is True


def test_latin_name_matches_forward_and_reverse_order():
    assert author_matches("Wei Zhang", ["Wei Zhang"]) is True
    assert author_matches("Wei Zhang", ["Zhang, Wei"]) is True
    assert author_matches("Wei Zhang", ["Zhang Wei"]) is True


def test_latin_name_matches_initial_abbreviation():
    assert author_matches("Wei Zhang", ["W. Zhang"]) is True
    assert author_matches("Wei Zhang", ["Zhang W"]) is True


def test_latin_name_ignores_diacritics():
    """中文名的罗马化写法带变音符（Lü / Lu）非常常见，必须视为同一人。"""
    assert author_matches("Wei Lu", ["Wei Lü"]) is True
    assert author_matches("Wei Lü", ["Wei Lu"]) is True


def test_latin_name_does_not_match_a_different_person():
    assert author_matches("Wei Zhang", ["Wei Wang"]) is False
    assert author_matches("Wei Zhang", ["Li Zhang"]) is False
    assert author_matches("Wei Zhang", ["John Smith"]) is False


def test_concatenated_author_string_is_split_before_matching():
    """有的库把多个人拼成一条：不能把「LI Wei」和「ZHANG Bei」拼成 Wei Zhang。"""
    entry = "WANG Xiao, LI Wei, LIU Xiaoli, ZHANG Bei"
    assert author_matches("Wei Zhang", [entry]) is False
    assert author_matches("Wei Li", [entry]) is True


def test_missing_inputs_never_match():
    assert author_matches("", ["Wei Zhang"]) is False
    assert author_matches("Wei Zhang", []) is False
    assert author_matches("Wei Zhang", ["", None]) is False


def test_normalization_tokens_and_runs():
    assert normalize_person_name("Wei  Zhang.") == "weizhang"
    assert normalize_person_name("陆 伟") == "陆伟"
    assert name_tokens("Zhang, Wei") == ["zhang", "wei"]
    assert cjk_runs("蒋春 Jiang Chun") == ["蒋春"]
    assert cjk_runs("陆 伟") == ["陆伟"]
    assert cjk_runs("Wei Zhang") == []
    # 书目惯例「姓, 名」是一个人不拆；拼接惯例要拆
    assert split_author_entry("Zhang, Wei") == ["Zhang, Wei"]
    assert split_author_entry("WANG Xiao, LI Wei") == ["WANG Xiao", "LI Wei"]
    assert split_author_entry("A, B; C") == ["A", "B", "C"]
