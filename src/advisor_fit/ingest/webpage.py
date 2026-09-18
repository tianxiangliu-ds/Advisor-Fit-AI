"""官方网页安全抓取：SSRF 防护、robots.txt 检查、正文抽取与来源记录。

边界：
- 只允许 http/https；拒绝 loopback / private / link-local / multicast 等非公网地址；
- 最多 5 次重定向，每一跳重新做 URL 校验；
- 连接超时 5s、读取超时 15s、响应体上限 5MB；
- 先读 robots.txt，被禁止时不抓取目标页；
- 正文用 trafilatura 抽取，失败降级到 BeautifulSoup。
"""

from __future__ import annotations

import hashlib
import socket
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup
from pydantic import BaseModel

_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_MAX_BODY_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 5


class UnsafeUrlError(ValueError):
    """URL 指向非公网地址或使用了不允许的协议。"""


class RobotsDeniedError(Exception):
    """robots.txt 禁止抓取目标路径。"""


class FetchFailedError(Exception):
    """抓取失败（网络错误、超时、响应过大等）。"""


class FetchedPage(BaseModel):
    final_url: str
    status_code: int
    title: str | None = None
    text: str = ""
    html: str | None = None
    retrieved_at: str | None = None
    content_hash: str | None = None
    robots_allowed: bool = True


def _resolve_ips(host: str) -> set[str] | None:
    """解析主机名到 IP 集合；无法解析时返回 None。"""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(host, None):
            ips.add(info[4][0].split("%")[0])
    except socket.gaierror:
        return None
    return ips


def _all_non_public(ips: set[str]) -> bool:
    if not ips:
        return False
    for addr in ips:
        try:
            if ip_address(addr).is_global:
                return False
        except ValueError:
            continue
    return True


def validate_public_http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"unsupported scheme: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("missing hostname")

    # 字面 IP 直接判定
    try:
        if not ip_address(host).is_global:
            raise UnsafeUrlError(f"non-public host: {host}")
        return url
    except ValueError:
        pass  # 非 IP 字面量，按主机名处理

    # 主机名：尽力解析；解析到非公网 IP 则拒绝，无法解析则交由抓取阶段处理
    ips = _resolve_ips(host)
    if ips is not None and _all_non_public(ips):
        raise UnsafeUrlError(f"host resolves to non-public IP: {host}")
    return url


def robots_allows(robots_txt: str, path: str) -> bool:
    """极简 robots.txt 解析：应用所有 Disallow 规则（保守，宁可多拒绝）。"""
    for line in robots_txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() == "disallow":
            rule = value.strip()
            if rule and path.startswith(rule):
                return False
    return True


def _extract_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    return None


def _extract_text(html: str) -> str:
    extracted = trafilatura.extract(html)
    if extracted:
        return extracted.strip()
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _build_page(resp: httpx.Response) -> FetchedPage:
    if len(resp.content) > _MAX_BODY_BYTES:
        raise FetchFailedError("response body exceeds 5MB limit")
    html = resp.text
    return FetchedPage(
        final_url=str(resp.url),
        status_code=resp.status_code,
        title=_extract_title(html),
        text=_extract_text(html),
        html=html,
        retrieved_at=datetime.now(UTC).isoformat(),
        content_hash=hashlib.sha256(resp.content).hexdigest(),
        robots_allowed=True,
    )


async def _robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    path = parsed.path or "/"
    try:
        resp = await client.get(robots_url)
    except httpx.HTTPError:
        return True  # robots.txt 不可用 → 允许（fail-open）
    if resp.status_code >= 400:
        return True  # 无 robots.txt → 允许
    return robots_allows(resp.text, path)


async def fetch_official_page(url: str, user_agent: str) -> FetchedPage:
    validate_public_http_url(url)
    headers = {"User-Agent": user_agent}
    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(
        timeout=timeout, headers=headers, follow_redirects=False
    ) as client:
        if not await _robots_allows(client, url):
            raise RobotsDeniedError(f"robots.txt disallows fetching {url}")

        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            validate_public_http_url(current)
            try:
                resp = await client.get(current)
            except httpx.HTTPError as exc:
                raise FetchFailedError(f"fetch failed for {current}: {exc}") from exc
            if resp.status_code in _REDIRECT_STATUS:
                location = resp.headers.get("location")
                if not location:
                    return _build_page(resp)
                current = urljoin(current, location)
                continue
            return _build_page(resp)

    raise FetchFailedError("too many redirects")
