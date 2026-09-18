"""Task 3：CV 解析、PII 脱敏与学生确认测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter

from advisor_fit.ingest.cv import (
    ParsedDocument,
    build_student_profile,
    extract_pdf_text,
    redact_pii,
)


@pytest.fixture
def fake_empty_pdf(tmp_path) -> Path:
    path = tmp_path / "empty.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(str(path))
    return path


@pytest.fixture
def parsed_fixture(cv_text: str) -> ParsedDocument:
    return ParsedDocument(text=cv_text, page_count=1, warnings=[])


def test_redact_pii_removes_direct_identifiers():
    result = redact_pii("张三 zhang@example.com 13800138000 北京市海淀区")
    assert "张三" not in result.text
    assert "zhang@example.com" not in result.text
    assert "13800138000" not in result.text
    assert set(result.redactions) >= {"name", "email", "phone", "address"}


def test_redact_pii_with_explicit_name():
    result = redact_pii("我叫李雷，研究方向是检索增强生成", name="李雷")
    assert "李雷" not in result.text
    assert "name" in result.redactions


def test_redact_pii_does_not_over_redact_common_words():
    result = redact_pii("研究方向是信息检索")
    assert "方向" in result.text


def test_empty_pdf_returns_blocking_warning(fake_empty_pdf):
    parsed = extract_pdf_text(fake_empty_pdf)
    assert parsed.text == ""
    assert "NO_EXTRACTABLE_TEXT" in parsed.warnings


def test_parsed_facts_are_not_draftable_until_user_confirms(parsed_fixture):
    profile = build_student_profile(parsed_fixture)
    assert profile.facts, "应至少抽取出若干候选事实"
    assert profile.draftable_facts() == []


def test_profile_extracts_skills_and_degree(parsed_fixture):
    profile = build_student_profile(parsed_fixture)
    values = {f.value for f in profile.facts}
    assert "Python" in values
    assert "本科" in values
    assert "某大学" in values
