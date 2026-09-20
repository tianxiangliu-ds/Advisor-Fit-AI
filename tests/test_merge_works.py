"""跨库合并去重与学科判定测试（不触真实网络）。"""

from advisor_fit.providers.academic import Work
from advisor_fit.providers.disciplines import (
    discipline_from_arxiv_category,
    discipline_from_openalex_field,
    discipline_label,
    dominant_discipline,
    guess_discipline,
    is_known_discipline,
)
from advisor_fit.providers.merge import merge_works, normalize_doi, normalize_title


def make_work(**kwargs) -> Work:
    payload = {
        "id": "w1",
        "title": "A Paper",
        "source_platform": "OpenAlex",
    }
    payload.update(kwargs)
    return Work(**payload)


# ---------- 归一化 ----------


def test_normalize_doi_strips_prefixes_and_case():
    assert normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert normalize_doi("doi:10.1000/xyz") == "10.1000/xyz"
    assert normalize_doi(None) == ""


def test_normalize_title_ignores_punctuation_case_and_html():
    left = normalize_title("Attention is All You Need!")
    right = normalize_title("<jats:title>attention is all you need</jats:title>")
    assert left == right == "attentionisallyouneed"


# ---------- 合并 ----------


def test_same_doi_merges_and_records_every_source():
    merged = merge_works(
        [
            make_work(
                id="a", title="Graph Neural Networks", doi="10.1/X", source_platform="OpenAlex"
            ),
            make_work(
                id="b",
                title="Graph neural networks",
                doi="https://doi.org/10.1/x",
                source_platform="Crossref",
            ),
        ]
    )
    assert len(merged) == 1
    assert merged[0].sources == ["OpenAlex", "Crossref"]


def test_same_title_with_compatible_year_merges_without_doi():
    merged = merge_works(
        [
            make_work(title="Deep Learning Survey", year=2021, source_platform="OpenAlex"),
            make_work(title="deep learning survey.", year=2022, source_platform="DBLP"),
        ]
    )
    assert len(merged) == 1
    assert merged[0].sources == ["OpenAlex", "DBLP"]


def test_conflicting_years_are_kept_apart():
    """年份差得多就不是同一篇：宁可多一条，也不合并错。"""
    merged = merge_works(
        [
            make_work(title="Deep Learning Survey", year=2018, source_platform="OpenAlex"),
            make_work(title="Deep Learning Survey", year=2024, source_platform="DBLP"),
        ]
    )
    assert len(merged) == 2


def test_merge_takes_the_richer_field_from_each_source():
    merged = merge_works(
        [
            make_work(
                title="RAG",
                doi="10.1/x",
                abstract="短摘要",
                citation_count=3,
                source_platform="OpenAlex",
            ),
            make_work(
                title="RAG",
                doi="10.1/x",
                abstract="更长的摘要，包含方法与结论。",
                citation_count=42,
                venue="NeurIPS",
                authors=["张三", "李四"],
                institution="Wuhan University",
                source_platform="Crossref",
            ),
        ]
    )[0]
    assert merged.abstract == "更长的摘要，包含方法与结论。"
    assert merged.citation_count == 42
    assert merged.venue == "NeurIPS"
    assert merged.authors == ["张三", "李四"]
    assert merged.institution == "Wuhan University"


def test_single_source_work_still_gets_sources_filled():
    merged = merge_works([make_work(title="Only One", source_platform="Europe PMC")])
    assert merged[0].sources == ["Europe PMC"]


def test_works_without_title_are_dropped():
    merged = merge_works([make_work(title="   "), make_work(title="Kept")])
    assert [work.title for work in merged] == ["Kept"]


# ---------- 学科判定 ----------


def test_guess_discipline_from_directions_and_department():
    assert guess_discipline("临床医学、肿瘤免疫") == "medicine"
    assert guess_discipline("计算机视觉与模式识别") == "cs"
    assert guess_discipline("金融工程与风险管理") == "economics"
    assert guess_discipline("中国古代文学") == "humanities"


def test_guess_discipline_does_not_invent_an_answer():
    assert guess_discipline("") is None
    assert guess_discipline("量子") == "physics"
    assert guess_discipline("很普通的一句话") is None


def test_openalex_field_and_arxiv_category_mapping():
    assert discipline_from_openalex_field("Medicine") == "medicine"
    assert discipline_from_openalex_field("Computer Science") == "cs"
    assert discipline_from_openalex_field("Unknown Field") is None
    assert discipline_from_arxiv_category("cs.CL") == "cs"
    assert discipline_from_arxiv_category("q-bio.NC") == "medicine"


def test_dominant_discipline_picks_the_majority_and_ignores_general():
    assert dominant_discipline(["medicine", "medicine", "cs"]) == "medicine"
    assert dominant_discipline(["general", "general"]) is None
    assert dominant_discipline([]) is None


def test_discipline_label_falls_back_to_general():
    assert discipline_label("medicine") == "医学 / 生命科学"
    assert discipline_label(None) == "通用（未指定学科）"
    assert is_known_discipline("general") is False
    assert is_known_discipline("cs") is True
