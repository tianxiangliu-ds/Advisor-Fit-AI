"""Task 4：官方网页安全抓取与来源记录测试。"""

from __future__ import annotations

import httpx
import pytest

from advisor_fit.ingest.official_profile import OfficialPageFacts, parse_official_profile
from advisor_fit.ingest.webpage import (
    FetchedPage,
    FetchFailedError,
    RobotsDeniedError,
    UnsafeUrlError,
    fetch_official_page,
    validate_public_http_url,
)

USER_AGENT = "AdvisorFitBot/0.1 (contact: you@example.com)"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
    ],
)
def test_private_or_non_http_urls_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_http_url(url)


@pytest.mark.asyncio
async def test_robots_denial_prevents_page_fetch(respx_mock):
    respx_mock.get("https://u.edu/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /faculty/"
    )
    target = respx_mock.get("https://u.edu/faculty/wang").respond(200, text="<html></html>")

    with pytest.raises(RobotsDeniedError):
        await fetch_official_page("https://u.edu/faculty/wang", USER_AGENT)

    assert target.call_count == 0


@pytest.mark.asyncio
async def test_fetch_page_when_robots_allows(respx_mock):
    respx_mock.get("https://u.edu/robots.txt").respond(200, text="User-agent: *\nDisallow:\n")
    respx_mock.get("https://u.edu/faculty/wang").respond(
        200,
        text="<html><head><title>王伟 - 计算机学院</title></head>"
        "<body><h1>王伟</h1><p>教授</p></body></html>",
    )

    page = await fetch_official_page("https://u.edu/faculty/wang", USER_AGENT)

    assert page.status_code == 200
    assert page.robots_allowed is True
    assert page.title == "王伟 - 计算机学院"
    assert page.content_hash


@pytest.mark.asyncio
async def test_fetch_timeout_raises_fetch_failed(respx_mock):
    respx_mock.get("https://u.edu/robots.txt").respond(200, text="User-agent: *\nDisallow:\n")
    respx_mock.get("https://u.edu/faculty/wang").mock(side_effect=httpx.ReadTimeout("boom"))

    with pytest.raises(FetchFailedError):
        await fetch_official_page("https://u.edu/faculty/wang", USER_AGENT)


@pytest.fixture
def professor_page_html(fixtures_dir) -> str:
    return (fixtures_dir / "professor_page.html").read_text(encoding="utf-8")


def test_parse_official_profile_extracts_identity_and_links(professor_page_html):
    page = FetchedPage(
        final_url="https://u.edu/faculty/wang",
        status_code=200,
        html=professor_page_html,
        title="王伟 - 计算机学院 - 示例大学",
    )

    facts = parse_official_profile(page)

    assert facts.name == "王伟"
    assert facts.email == "wangwei@university.example.edu"
    assert facts.external_ids.orcid == "0000-0002-1234-5678"
    assert facts.institution == "示例大学"
    assert "信息检索" in facts.declared_interests
    assert any("硕士" in s for s in facts.recruiting_statements)


def test_anchor_requires_name():
    facts = OfficialPageFacts(name=None)
    with pytest.raises(ValueError):
        facts.to_anchor()
