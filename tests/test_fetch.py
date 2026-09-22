"""统一抓取零件测试：守规矩（robots）、限速、重试、条件请求。不触真实网络。"""

from __future__ import annotations

import httpx

from advisor_fit.ingest.fetch import Fetcher, content_hash


class FakeClock:
    """假时钟 + 假睡眠：把"等了多久"记下来，测试里不真的等。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _build(handler, *, robots=None, **kwargs):
    clock = FakeClock()
    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock.monotonic,
        sleep=clock.sleep,
        robots_loader=lambda url: robots,
        **kwargs,
    )
    return fetcher, clock


def test_robots_denied_blocks_the_request_entirely():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="不应该被请求")

    fetcher, _ = _build(handler, robots=False)
    result = fetcher.fetch("https://example.com/faculty/luwei")

    assert result.outcome == "ROBOTS_DENIED"
    assert result.robots_allowed is False
    assert calls == []


def test_successful_fetch_returns_text_and_content_hash():
    def handler(request):
        return httpx.Response(200, text="<html><title>陆伟</title>正文</html>")

    fetcher, _ = _build(handler, robots=True)
    result = fetcher.fetch("https://example.com/faculty/luwei")

    assert result.outcome == "OK"
    assert result.status_code == 200
    assert "陆伟" in result.text
    assert result.content_hash == content_hash(result.text)
    assert result.robots_allowed is True
    assert result.attempts == 1


def test_unknown_robots_still_allows_fetching_but_records_unknown():
    """robots.txt 拉不到时记为"未知"，不因此完全阻断用户。"""

    def handler(request):
        return httpx.Response(200, text="正文")

    fetcher, _ = _build(handler, robots=None)
    result = fetcher.fetch("https://example.com/a")

    assert result.outcome == "OK"
    assert result.robots_allowed is None


def test_same_host_is_rate_limited_between_requests():
    def handler(request):
        return httpx.Response(200, text="正文")

    fetcher, clock = _build(handler, robots=True, min_interval_seconds=1.5)
    fetcher.fetch("https://example.com/a")
    fetcher.fetch("https://example.com/b")

    assert clock.slept == [1.5]


def test_different_hosts_do_not_wait_for_each_other():
    def handler(request):
        return httpx.Response(200, text="正文")

    fetcher, clock = _build(handler, robots=True, min_interval_seconds=1.5)
    fetcher.fetch("https://a.example.com/x")
    fetcher.fetch("https://b.example.com/y")

    assert clock.slept == []


def test_retries_after_server_error_then_succeeds():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="正文")

    fetcher, clock = _build(handler, robots=True, max_attempts=3, backoff_seconds=0.5)
    result = fetcher.fetch("https://example.com/a")

    assert result.outcome == "OK"
    assert result.attempts == 2
    # 第一次 0.5 秒是重试退避；第二次 1.0 秒是限速补足
    # （距上次请求不足 1.5 秒）——重试同样受限速约束，不会猛敲对方服务器
    assert clock.slept == [0.5, 1.0]


def test_client_error_is_not_retried():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(404, text="not found")

    fetcher, _ = _build(handler, robots=True, max_attempts=3)
    result = fetcher.fetch("https://example.com/missing")

    assert result.outcome == "FETCH_FAILED"
    assert result.status_code == 404
    assert len(seen) == 1


def test_not_modified_is_reported_as_cache_hit():
    def handler(request):
        assert request.headers.get("If-None-Match") == 'W/"abc"'
        return httpx.Response(304)

    fetcher, _ = _build(handler, robots=True)
    result = fetcher.fetch("https://example.com/a", etag='W/"abc"')

    assert result.outcome == "NOT_MODIFIED"
    assert result.from_cache is True


def test_network_error_is_reported_instead_of_raised():
    def handler(request):
        raise httpx.ConnectError("连接被拒绝")

    fetcher, _ = _build(handler, robots=True, max_attempts=2, backoff_seconds=0.2)
    result = fetcher.fetch("https://example.com/a")

    assert result.outcome == "FETCH_FAILED"
    assert "连接被拒绝" in result.error
    assert result.attempts == 2


def test_non_http_scheme_is_rejected():
    fetcher, _ = _build(lambda request: httpx.Response(200), robots=True)
    result = fetcher.fetch("ftp://example.com/file")

    assert result.outcome == "UNSAFE_URL"


def test_private_network_target_is_rejected_before_any_request():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="不应该被请求")

    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        robots_loader=lambda _url: True,
        address_resolver=lambda _host: ["127.0.0.1"],
    )
    result = fetcher.fetch("http://internal.example/admin")

    assert result.outcome == "UNSAFE_URL"
    assert "公网" in result.error
    assert calls == []


def test_ipv6_loopback_is_rejected():
    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
        robots_loader=lambda _url: True,
    )

    assert fetcher.fetch("http://[::1]/admin").outcome == "UNSAFE_URL"


def test_domain_is_rejected_if_any_resolved_address_is_private():
    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
        robots_loader=lambda _url: True,
        address_resolver=lambda _host: ["93.184.216.34", "10.0.0.8"],
    )

    assert fetcher.fetch("https://mixed.example/profile").outcome == "UNSAFE_URL"


def test_url_with_embedded_credentials_is_rejected():
    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
        robots_loader=lambda _url: True,
        address_resolver=lambda _host: ["93.184.216.34"],
    )

    result = fetcher.fetch("https://user:password@example.com/profile")

    assert result.outcome == "UNSAFE_URL"


def test_redirect_to_private_network_is_rejected():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
        )

    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        robots_loader=lambda _url: True,
        address_resolver=lambda _host: ["93.184.216.34"],
    )
    result = fetcher.fetch("https://example.com/profile")

    assert result.outcome == "UNSAFE_URL"
    assert calls == ["https://example.com/profile"]


def test_public_redirect_is_checked_and_followed():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"Location": "https://faculty.example.edu/p"})
        return httpx.Response(200, text="导师主页")

    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        robots_loader=lambda _url: True,
        address_resolver=lambda _host: ["93.184.216.34"],
    )
    result = fetcher.fetch("https://example.com/profile")

    assert result.outcome == "OK"
    assert result.final_url == "https://faculty.example.edu/p"
    assert calls == ["https://example.com/profile", "https://faculty.example.edu/p"]


def test_robots_is_looked_up_once_per_host():
    lookups = []

    def handler(request):
        return httpx.Response(200, text="正文")

    clock = FakeClock()
    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock.monotonic,
        sleep=clock.sleep,
        robots_loader=lambda url: lookups.append(url) or True,
    )
    fetcher.fetch("https://example.com/a")
    fetcher.fetch("https://example.com/b")

    assert lookups == ["https://example.com/a"]


def test_content_hash_is_stable_and_short():
    first = content_hash("同样的内容")
    second = content_hash("同样的内容")

    assert first == second
    assert first != content_hash("不一样的内容")
    assert len(first) == 16
