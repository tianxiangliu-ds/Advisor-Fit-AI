"""网页缓存测试：保质期内不再重复抓取，过期后重新抓。"""

from __future__ import annotations

import httpx

from advisor_fit.ingest.fetch import Fetcher
from advisor_fit.storage.cache import PageCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _fetcher(tmp_path, handler, *, clock, cache=True, **kwargs):
    page_cache = PageCache(tmp_path / "cache.sqlite", clock=clock) if cache else None
    return (
        Fetcher(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            robots_loader=lambda url: True,
            cache=page_cache,
            **kwargs,
        ),
        page_cache,
    )


# -- 缓存本身 -----------------------------------------------------------------


def test_put_and_get_round_trip(tmp_path):
    clock = FakeClock()
    cache = PageCache(tmp_path / "c.sqlite", clock=clock)

    cache.put("https://a.example.com/x", text="正文", content_hash="abc", robots_allowed=True)
    got = cache.get("https://a.example.com/x")

    assert got is not None
    assert got.text == "正文"
    assert got.content_hash == "abc"
    assert got.robots_allowed is True
    assert cache.count() == 1


def test_expired_entry_is_not_returned(tmp_path):
    clock = FakeClock()
    cache = PageCache(tmp_path / "c.sqlite", clock=clock, default_ttl_seconds=100)
    cache.put("https://a.example.com/x", text="正文")

    assert cache.get("https://a.example.com/x") is not None

    clock.advance(101)

    assert cache.get("https://a.example.com/x") is None


def test_peek_ignores_expiry(tmp_path):
    clock = FakeClock()
    cache = PageCache(tmp_path / "c.sqlite", clock=clock, default_ttl_seconds=1)
    cache.put("https://a.example.com/x", text="旧正文")
    clock.advance(1000)

    assert cache.get("https://a.example.com/x") is None
    assert cache.peek("https://a.example.com/x").text == "旧正文"


def test_clear_removes_everything(tmp_path):
    cache = PageCache(tmp_path / "c.sqlite")
    cache.put("https://a.example.com/x", text="正文")

    cache.clear()

    assert cache.count() == 0


# -- 接进抓取零件 -------------------------------------------------------------


def test_second_fetch_comes_from_cache_without_network(tmp_path):
    calls = []
    clock = FakeClock()

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="<html>陆伟</html>")

    fetcher, _ = _fetcher(tmp_path, handler, clock=clock)
    first = fetcher.fetch("https://example.com/luwei")
    second = fetcher.fetch("https://example.com/luwei")

    assert first.from_cache is False
    assert second.from_cache is True
    assert second.text == "<html>陆伟</html>"
    assert len(calls) == 1, "第二次不应再访问对方网站"


def test_without_cache_every_fetch_hits_network(tmp_path):
    calls = []
    clock = FakeClock()

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="正文")

    fetcher, _ = _fetcher(tmp_path, handler, clock=clock, cache=False)
    fetcher.fetch("https://example.com/a")
    fetcher.fetch("https://example.com/a")

    assert len(calls) == 2


def test_cache_expiry_triggers_a_fresh_fetch(tmp_path):
    calls = []
    clock = FakeClock()

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=f"第 {len(calls)} 版")

    fetcher, _ = _fetcher(tmp_path, handler, clock=clock, cache_ttl_seconds=60)
    fetcher.fetch("https://example.com/a")
    clock.advance(61)
    fresh = fetcher.fetch("https://example.com/a")

    assert len(calls) == 2
    assert fresh.from_cache is False
    assert fresh.text == "第 2 版"


def test_cached_fetch_records_robots_state_and_hash(tmp_path):
    clock = FakeClock()

    def handler(request):
        return httpx.Response(200, text="正文")

    fetcher, cache = _fetcher(tmp_path, handler, clock=clock)
    fetcher.fetch("https://example.com/a")
    stored = cache.peek("https://example.com/a")

    assert stored.robots_allowed is True
    assert stored.content_hash != ""
