"""主页解析测试（不触真实网络/LLM）。"""

import httpx

from advisor_fit.ingest.homepage import (
    HomepageProfile,
    extract_title,
    fetch_homepage,
    html_to_text,
    parse_homepage,
)
from advisor_fit.llm.provider import NullLLM

_HTML = """
<html>
  <head><title>陆伟 - 武汉大学信息管理学院</title>
  <style>.x{color:red}</style>
  </head>
  <body>
    <h1>陆伟</h1>
    <p>武汉大学信息管理学院 教授</p>
    <p>研究方向：信息检索、知识图谱、数字人文</p>
    <p>邮箱：luwei@whu.edu.cn</p>
    <script>var a = 1;</script>
  </body>
</html>
"""


class FakeLLM:
    def __init__(self, profile):
        self.profile = profile

    def generate(self, *, schema, instructions, payload):
        return self.profile


def test_html_to_text_drops_script_and_style():
    text = html_to_text(_HTML)
    assert "陆伟" in text
    assert "信息检索" in text
    assert "var a = 1" not in text
    assert ".x{color:red}" not in text


def test_extract_title():
    assert extract_title(_HTML) == "陆伟 - 武汉大学信息管理学院"


def test_parse_homepage_uses_llm_profile():
    profile = HomepageProfile(
        name="陆伟",
        institution="武汉大学",
        department="信息管理学院",
        title="教授",
        email="luwei@whu.edu.cn",
        declared_interests=["信息检索", "知识图谱"],
    )
    result = parse_homepage(html_to_text(_HTML), FakeLLM(profile))
    assert result.name == "陆伟"
    assert result.institution == "武汉大学"
    assert result.declared_interests == ["信息检索", "知识图谱"]


def test_parse_homepage_falls_back_to_rule_without_llm():
    result = parse_homepage(html_to_text(_HTML), NullLLM(), title="陆伟 - 武汉大学信息管理学院")
    assert result.email == "luwei@whu.edu.cn"
    assert result.name == "陆伟"


def test_fetch_homepage_returns_html():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://example.com/faculty/luwei"
        return httpx.Response(200, text="<html><title>陆伟</title><body>正文</body></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    html = fetch_homepage("https://example.com/faculty/luwei", client=client)
    assert "陆伟" in html


def _fetcher_for(handler, *, robots=True):
    from advisor_fit.ingest.fetch import Fetcher

    return Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        robots_loader=lambda url: robots,
    )


def test_extract_homepage_profile_goes_through_the_fetch_part():
    """应用层入口必须走统一抓取零件，而不是自己直接请求。"""
    from advisor_fit.ingest.homepage import extract_homepage_profile

    fetcher = _fetcher_for(lambda request: httpx.Response(200, text=_HTML))
    profile = extract_homepage_profile(
        "https://example.com/faculty/luwei", NullLLM(), fetcher=fetcher
    )

    assert profile.email == "luwei@whu.edu.cn"
    assert profile.name == "陆伟"


def test_extract_homepage_profile_explains_robots_refusal_in_chinese():
    from advisor_fit.ingest.homepage import extract_homepage_profile

    fetcher = _fetcher_for(lambda request: httpx.Response(200, text=_HTML), robots=False)
    try:
        extract_homepage_profile("https://example.com/faculty/luwei", NullLLM(), fetcher=fetcher)
    except RuntimeError as exc:
        assert "robots.txt" in str(exc)
    else:  # pragma: no cover - 明确失败信息比静默通过更重要
        raise AssertionError("robots 拒绝时应当抛出可读错误")
