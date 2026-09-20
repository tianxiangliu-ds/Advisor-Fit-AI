"""字段级补齐测试：拿不到就标"未知"，绝不因为缺字段而卡住流程。"""

from __future__ import annotations

from advisor_fit.ingest.profile_fallback import (
    REQUIRED_FIELDS,
    SOURCE_AI,
    SOURCE_MANUAL,
    SOURCE_RULES,
    SOURCE_STRUCTURED,
    build_field_report,
    extract_page_fields,
    extract_structured_fields,
    looks_like_school_email,
)
from advisor_fit.models.provenance import FieldStatus

_JSON_LD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Person",
  "name": "陆伟",
  "jobTitle": "教授",
  "email": "mailto:luwei@whu.edu.cn",
  "affiliation": {"@type": "Organization", "name": "武汉大学",
                  "department": {"name": "信息管理学院"}},
  "knowsAbout": ["信息检索", "知识图谱"]
}
</script>
</head><body>正文</body></html>
"""

_PAGE_TEXT = """武汉大学信息管理学院
陆伟，男，教授，博士生导师
研究方向：信息检索、知识图谱、数字人文
邮箱：luwei@whu.edu.cn
"""


class FakeProfile:
    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "")
        self.institution = kwargs.get("institution", "")
        self.department = kwargs.get("department", "")
        self.title = kwargs.get("title", "")
        self.email = kwargs.get("email", "")
        self.declared_interests = kwargs.get("declared_interests", [])


# -- 来源一：官网结构化信息 ---------------------------------------------------


def test_reads_person_from_json_ld():
    fields = extract_structured_fields(_JSON_LD_HTML)

    assert fields["name"] == "陆伟"
    assert fields["title"] == "教授"
    assert fields["email"] == "luwei@whu.edu.cn"
    assert fields["institution"] == "武汉大学"
    assert fields["department"] == "信息管理学院"
    assert fields["declared_interests"] == "信息检索、知识图谱"


def test_reads_meta_author_when_no_json_ld():
    html = '<html><head><meta name="author" content="张伟"></head><body>正文</body></html>'

    assert extract_structured_fields(html)["name"] == "张伟"


def test_plain_html_yields_nothing_structured():
    assert extract_structured_fields("<html><body>随便写点东西</body></html>") == {}


def test_broken_json_ld_is_ignored_not_raised():
    html = '<script type="application/ld+json">{ not valid json </script>'

    assert extract_structured_fields(html) == {}


# -- 来源二：官网正文（无 LLM）------------------------------------------------


def test_rule_extraction_works_without_any_llm():
    fields = extract_page_fields(_PAGE_TEXT)

    assert fields["email"] == "luwei@whu.edu.cn"
    assert fields["department"] == "信息管理学院"
    assert fields["title"] == "教授"
    assert "信息检索" in fields["declared_interests"]


def test_rule_extraction_prefers_longer_title_word():
    assert extract_page_fields("张三 副教授")["title"] == "副教授"


# -- 邮箱可信度 ---------------------------------------------------------------


def test_school_email_is_recognised():
    assert looks_like_school_email("luwei@whu.edu.cn") is True
    assert looks_like_school_email("a@mit.edu") is True
    assert looks_like_school_email("someone@gmail.com") is False
    assert looks_like_school_email("不合法") is False


# -- 合并与优先级 -------------------------------------------------------------


def test_manual_value_wins_over_website():
    report = build_field_report(
        manual={"name": "陆伟"},
        page_text=_PAGE_TEXT,
        source_url="https://example.com/luwei",
    )

    assert report.get("name").source == SOURCE_MANUAL
    assert report.get("email").source == SOURCE_RULES


def test_structured_information_wins_over_plain_text():
    report = build_field_report(homepage_html=_JSON_LD_HTML, page_text=_PAGE_TEXT)

    assert report.get("institution").source == SOURCE_STRUCTURED
    assert report.get("name").source == SOURCE_STRUCTURED


def test_ai_extraction_wins_over_rule_extraction():
    report = build_field_report(
        page_text=_PAGE_TEXT,
        llm_profile=FakeProfile(name="陆伟", institution="武汉大学", title="教授"),
    )

    assert report.get("title").source == SOURCE_AI
    # AI 没给的字段仍然由规则补齐
    assert report.get("email").source == SOURCE_RULES


# -- 关键：缺字段不阻塞 -------------------------------------------------------


def test_missing_fields_are_unknown_but_do_not_block():
    report = build_field_report(manual={"name": "陆伟", "institution": "武汉大学"})

    assert report.get("department").status == FieldStatus.UNKNOWN
    assert report.get("email").status == FieldStatus.UNKNOWN
    assert report.missing_required(REQUIRED_FIELDS) == []
    assert set(report.unknown_fields()) == {"department", "title", "email", "declared_interests"}


def test_only_name_and_institution_are_required():
    report = build_field_report(manual={"name": "陆伟"})

    assert report.missing_required(REQUIRED_FIELDS) == ["institution"]


def test_summary_is_readable_chinese():
    report = build_field_report(manual={"name": "陆伟", "institution": "武汉大学"})

    summary = report.summary()
    assert "已获取 2/6 个字段" in summary
    assert "还缺" in summary


# -- 不把可疑邮箱当已确认 -----------------------------------------------------


def test_non_school_email_is_downgraded_to_inferred():
    report = build_field_report(manual={"name": "陆伟", "email": "luwei@gmail.com"})

    entry = report.get("email")
    assert entry.status == FieldStatus.INFERRED
    assert "核对" in entry.note
    assert report.notes


def test_school_email_stays_confirmed():
    report = build_field_report(manual={"name": "陆伟", "email": "luwei@whu.edu.cn"})

    assert report.get("email").status == FieldStatus.CONFIRMED
    assert report.notes == []


# -- 可序列化（要落库、要进报告）---------------------------------------------


def test_report_is_serialisable():
    report = build_field_report(manual={"name": "陆伟", "institution": "武汉大学"})
    payload = report.model_dump(mode="json")

    assert payload["fields"]["name"]["value"] == "陆伟"
    assert payload["fields"]["name"]["status"] == "CONFIRMED"
    assert payload["fields"]["email"]["status"] == "UNKNOWN"
