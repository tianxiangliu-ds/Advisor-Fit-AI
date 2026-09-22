"""统一的网页抓取零件：守规矩、限速、可重试、能识别内容是否变化。

为什么需要它：导师官网分散在各高校，样式和稳定性差别很大。
把"去外网取一个网页"收敛到这一个入口，才能统一做到：

- **守规矩**：抓之前先看 robots.txt（网站声明哪些内容允许被抓取），不允许就不抓；
  拿不到 robots.txt 时记为"未知"并继续，不因为一次失败就完全阻断用户。
- **限速**：同一个网站两次请求之间留出间隔，不反复冲击对方服务器。
- **可重试**：服务端临时出错（5xx）或网络抖动时自动退避重试；
  客户端错误（4xx）不重试，避免无意义的重复请求。
- **能识别变化**：支持 If-None-Match / If-Modified-Since 条件请求，
  内容没变就返回 NOT_MODIFIED，省流量也省时间。
- **给内容算指纹**（content_hash）：用来判断"这个页面和上次抓到的是不是同一份"。

只抓用户触发的单个页面，不做批量后台爬取（见 CLAUDE.md 的边界）。
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from advisor_fit.storage.cache import PROFILE_TTL_SECONDS, PageCache

USER_AGENT = "AdvisorFitAI/0.1 (personal research tool; contact: local user)"

# 抓取结果的状态码
OUTCOME_OK = "OK"
OUTCOME_NOT_MODIFIED = "NOT_MODIFIED"
OUTCOME_ROBOTS_DENIED = "ROBOTS_DENIED"
OUTCOME_FETCH_FAILED = "FETCH_FAILED"
OUTCOME_UNSAFE_URL = "UNSAFE_URL"

OUTCOME_MESSAGES: dict[str, str] = {
    OUTCOME_ROBOTS_DENIED: "该网站的 robots.txt 不允许抓取这个页面",
    OUTCOME_UNSAFE_URL: "只支持 http / https 链接",
    OUTCOME_FETCH_FAILED: "网页抓取失败",
    OUTCOME_NOT_MODIFIED: "网页内容与上次相同",
}


def content_hash(text: str) -> str:
    """给一页内容算一个短指纹（16 位十六进制），用于判断内容有没有变化。"""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class FetchResult:
    url: str
    outcome: str = OUTCOME_OK
    final_url: str = ""
    status_code: int | None = None
    text: str = ""
    content_hash: str = ""
    robots_allowed: bool | None = None
    from_cache: bool = False
    attempts: int = 0
    elapsed_ms: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_OK

    def friendly_error(self) -> str:
        """给界面用的一句中文说明。"""
        base = OUTCOME_MESSAGES.get(self.outcome, self.outcome)
        return f"{base}：{self.error}" if self.error else base


RobotsLoader = Callable[[str], bool | None]
AddressResolver = Callable[[str], list[str]]


class Fetcher:
    """抓取一个网页，并负责遵守 robots、限速、重试。"""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        user_agent: str = USER_AGENT,
        min_interval_seconds: float = 1.5,
        timeout: float = 20.0,
        max_attempts: int = 2,
        backoff_seconds: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        robots_loader: RobotsLoader | None = None,
        address_resolver: AddressResolver | None = None,
        cache: PageCache | None = None,
        cache_ttl_seconds: int = PROFILE_TTL_SECONDS,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval_seconds = min_interval_seconds
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.backoff_seconds = backoff_seconds
        self._client = client
        self._clock = clock
        self._sleep = sleep
        self._robots_loader = robots_loader or self._load_robots
        self._address_resolver = address_resolver or self._resolve_addresses
        self._robots_cache: dict[str, bool | None] = {}
        self._last_request_at: dict[str, float] = {}
        self._cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds

    # -- 内部 -----------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=False,
                headers={"User-Agent": self.user_agent},
            )
        return self._client

    @staticmethod
    def _resolve_addresses(host: str) -> list[str]:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({str(info[4][0]) for info in infos})

    def _url_safety_error(self, url: str) -> str:
        parts = urlparse(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc or not parts.hostname:
            return "链接必须以 http:// 或 https:// 开头"
        if parts.username is not None or parts.password is not None:
            return "链接不能包含账号或密码"
        try:
            literal = ipaddress.ip_address(parts.hostname)
            addresses = [str(literal)]
        except ValueError:
            try:
                addresses = self._address_resolver(parts.hostname)
            except (OSError, ValueError) as exc:
                return f"无法确认目标地址是否安全：{exc}"
        if not addresses:
            return "无法确认目标地址是否安全"
        try:
            if any(not ipaddress.ip_address(address).is_global for address in addresses):
                return "只允许访问公网地址，不能访问本机或局域网"
        except ValueError:
            return "目标域名解析出了无效地址"
        return ""

    def _load_robots(self, url: str) -> bool | None:
        """默认的 robots 读取：拿不到就返回"未知"，不因此彻底阻断。"""
        parts = urlparse(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            response = self._http().get(
                robots_url, timeout=self.timeout, follow_redirects=False
            )
        except httpx.HTTPError:
            return None
        if response.status_code == 404:
            return True  # 没有 robots.txt 就是不限制
        if response.status_code >= 400:
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch(self.user_agent, url)

    def _robots_allowed(self, url: str) -> bool | None:
        host = urlparse(url).netloc
        if host not in self._robots_cache:
            self._robots_cache[host] = self._robots_loader(url)
        return self._robots_cache[host]

    def _throttle(self, host: str) -> None:
        """同一个网站两次请求之间至少间隔 min_interval_seconds。"""
        last = self._last_request_at.get(host)
        now = self._clock()
        if last is not None:
            wait = self.min_interval_seconds - (now - last)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_request_at[host] = now

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((self._clock() - started) * 1000))

    # -- 对外 -----------------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        started = self._clock()
        safety_error = self._url_safety_error(url)
        if safety_error:
            return FetchResult(
                url=url,
                outcome=OUTCOME_UNSAFE_URL,
                error=safety_error,
                elapsed_ms=self._elapsed_ms(started),
            )
        allowed = self._robots_allowed(url)
        if allowed is False:
            return FetchResult(
                url=url,
                outcome=OUTCOME_ROBOTS_DENIED,
                robots_allowed=False,
                error=f"robots.txt 禁止抓取 {url}",
                elapsed_ms=self._elapsed_ms(started),
            )

        # 保质期内直接用缓存：不再访问对方网站，也不再花时间
        if self._cache is not None:
            cached = self._cache.get(url)
            if cached is not None:
                return FetchResult(
                    url=url,
                    outcome=OUTCOME_OK,
                    final_url=url,
                    status_code=200,
                    text=cached.text,
                    content_hash=cached.content_hash,
                    robots_allowed=cached.robots_allowed,
                    from_cache=True,
                    attempts=0,
                    elapsed_ms=self._elapsed_ms(started),
                )

        headers = {"User-Agent": self.user_agent}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        attempts = 0
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            try:
                current_url = url
                current_allowed = allowed
                for _redirect in range(6):
                    current_parts = urlparse(current_url)
                    self._throttle(current_parts.netloc)
                    response = self._http().get(
                        current_url,
                        headers=headers,
                        timeout=self.timeout,
                        follow_redirects=False,
                    )
                    if response.status_code not in {301, 302, 303, 307, 308}:
                        break
                    location = response.headers.get("Location")
                    if not location:
                        last_error = "跳转响应缺少 Location"
                        response = None
                        break
                    next_url = urljoin(current_url, location)
                    safety_error = self._url_safety_error(next_url)
                    if safety_error:
                        return FetchResult(
                            url=url,
                            outcome=OUTCOME_UNSAFE_URL,
                            final_url=next_url,
                            robots_allowed=current_allowed,
                            attempts=attempts,
                            error=safety_error,
                            elapsed_ms=self._elapsed_ms(started),
                        )
                    current_allowed = self._robots_allowed(next_url)
                    if current_allowed is False:
                        return FetchResult(
                            url=url,
                            outcome=OUTCOME_ROBOTS_DENIED,
                            final_url=next_url,
                            robots_allowed=False,
                            attempts=attempts,
                            error=f"robots.txt 禁止抓取 {next_url}",
                            elapsed_ms=self._elapsed_ms(started),
                        )
                    current_url = next_url
                else:
                    response = None
                    last_error = "网页跳转次数过多"
                if response is None:
                    if attempt < self.max_attempts:
                        self._sleep(self.backoff_seconds * attempt)
                        continue
                    break
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt < self.max_attempts:
                    self._sleep(self.backoff_seconds * attempt)
                    continue
                break

            if response.status_code == 304:
                return FetchResult(
                    url=url,
                    outcome=OUTCOME_NOT_MODIFIED,
                    final_url=str(response.url),
                    status_code=304,
                    robots_allowed=current_allowed,
                    from_cache=True,
                    attempts=attempts,
                    elapsed_ms=self._elapsed_ms(started),
                )

            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}"
                if response.status_code >= 500 and attempt < self.max_attempts:
                    self._sleep(self.backoff_seconds * attempt)
                    continue
                return FetchResult(
                    url=url,
                    outcome=OUTCOME_FETCH_FAILED,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    robots_allowed=current_allowed,
                    attempts=attempts,
                    error=last_error,
                    elapsed_ms=self._elapsed_ms(started),
                )

            text = response.text
            digest = content_hash(text)
            if self._cache is not None:
                self._cache.put(
                    url,
                    text=text,
                    content_hash=digest,
                    etag=response.headers.get("ETag"),
                    robots_allowed=current_allowed,
                    ttl_seconds=self.cache_ttl_seconds,
                )
            return FetchResult(
                url=url,
                outcome=OUTCOME_OK,
                final_url=str(response.url),
                status_code=response.status_code,
                text=text,
                content_hash=digest,
                robots_allowed=current_allowed,
                attempts=attempts,
                elapsed_ms=self._elapsed_ms(started),
            )

        return FetchResult(
            url=url,
            outcome=OUTCOME_FETCH_FAILED,
            robots_allowed=allowed,
            attempts=attempts,
            error=last_error or "未知网络错误",
            elapsed_ms=self._elapsed_ms(started),
        )
