"""一键验证：把这一阶段新增的能力逐项跑一遍，并打印中文结果。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\verify_features.py

特点：
- 不联网（用假的网站响应），不调用 LLM，任何机器上都能跑出同样结果；
- 每项都给出「预期 / 实际 / 结论」，最后汇总通过率；
- 全部通过时进程退出码为 0，有失败时为 1。

它检查的就是「导师主页解析」这条链路的 5 件事：
    1. 取网页时守规矩（robots / 限速 / 重试）
    2. 网页缓存（保质期内不再访问对方网站）
    3. 字段级补齐（缺字段不卡流程）
    4. 身份核对（同名歧义时请你确认）
    5. 导师名册（只取学校/学院/姓名）
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from advisor_fit.analysis.identity import assess_identity  # noqa: E402
from advisor_fit.config import settings  # noqa: E402
from advisor_fit.ingest.fetch import Fetcher  # noqa: E402
from advisor_fit.ingest.profile_fallback import (  # noqa: E402
    REQUIRED_FIELDS,
    build_field_report,
    extract_page_fields,
    extract_structured_fields,
)
from advisor_fit.ingest.supervisor_roster import RosterEntry  # noqa: E402
from advisor_fit.models.provenance import FieldStatus  # noqa: E402
from advisor_fit.models.resolution import ResolutionStatus  # noqa: E402
from advisor_fit.storage.cache import PageCache  # noqa: E402
from advisor_fit.storage.roster_repo import RosterRepository  # noqa: E402

WORK = ROOT / ".tmp" / "verify"
RESULTS: list[tuple[str, str, str, bool]] = []


def check(section: str, title: str, expected: str, actual: str, ok: bool) -> None:
    RESULTS.append((f"[{section}] {title}", expected, actual, ok))


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


_JSON_LD = """
<html><head><script type="application/ld+json">
{"@type":"Person","name":"陆伟","jobTitle":"教授","email":"mailto:luwei@whu.edu.cn",
 "affiliation":{"name":"武汉大学","department":{"name":"信息管理学院"}},
 "knowsAbout":["信息检索","知识图谱"]}
</script></head><body>正文</body></html>
"""

_PAGE_TEXT = """武汉大学信息管理学院
陆伟，男，教授，博士生导师
研究方向：信息检索、知识图谱、数字人文
邮箱：luwei@whu.edu.cn
"""


# -- 1. 取网页时守规矩 ---------------------------------------------------------


def verify_fetch() -> None:
    section = "1 抓取守规矩"

    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="正文")

    fetcher = Fetcher(client=_client(handler), robots_loader=lambda url: False)
    denied = fetcher.fetch("https://example.com/a")
    check(
        section,
        "robots.txt 不允许时不抓",
        "结果=ROBOTS_DENIED 且 0 次请求",
        f"结果={denied.outcome} 请求数={len(calls)}",
        denied.outcome == "ROBOTS_DENIED" and calls == [],
    )

    clock = FakeClock()
    waits: list[float] = []

    def clock_advance(seconds: float) -> None:
        waits.append(seconds)
        clock.advance(seconds)

    fetcher = Fetcher(
        client=_client(lambda r: httpx.Response(200, text="正文")),
        robots_loader=lambda url: True,
        min_interval_seconds=1.5,
        clock=clock,
        sleep=clock_advance,
    )
    fetcher.fetch("https://same.example.com/x")
    fetcher.fetch("https://same.example.com/y")
    check(
        section,
        "同一个网站限速",
        "两次请求之间等待 1.5 秒",
        f"等待记录={waits}",
        waits == [1.5],
    )

    attempts: list[int] = []

    def flaky(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="正文")

    fetcher = Fetcher(
        client=_client(flaky),
        robots_loader=lambda url: True,
        max_attempts=3,
        clock=lambda: 0.0,
        sleep=lambda s: None,
    )
    retried = fetcher.fetch("https://example.com/retry")
    check(
        section,
        "服务端临时出错自动重试",
        "第 2 次成功，结果=OK",
        f"结果={retried.outcome} 尝试次数={retried.attempts}",
        retried.outcome == "OK" and retried.attempts == 2,
    )

    unsafe = Fetcher(client=_client(lambda r: httpx.Response(200)), robots_loader=lambda u: True)
    check(
        section,
        "拒绝非 http/https 链接",
        "结果=UNSAFE_URL",
        f"结果={unsafe.fetch('ftp://example.com/x').outcome}",
        unsafe.fetch("ftp://example.com/x").outcome == "UNSAFE_URL",
    )


# -- 2. 网页缓存 ---------------------------------------------------------------


def verify_cache() -> None:
    section = "2 网页缓存"
    cache_dir = WORK / "cache"
    shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    clock = FakeClock()
    network_calls: list[str] = []

    def handler(request):
        network_calls.append(str(request.url))
        return httpx.Response(200, text="<html>陆伟的主页</html>")

    cache = PageCache(cache_dir / "cache.sqlite", clock=clock)
    fetcher = Fetcher(
        client=_client(handler), robots_loader=lambda url: True, cache=cache,
        clock=clock, sleep=lambda s: clock.advance(s),
    )

    first = fetcher.fetch("https://example.com/luwei")
    second = fetcher.fetch("https://example.com/luwei")
    check(
        section,
        "保质期内第二次不再访问对方网站",
        "网络请求 1 次；第二次来自缓存",
        f"网络请求 {len(network_calls)} 次；第二次 from_cache={second.from_cache}",
        len(network_calls) == 1 and second.from_cache is True and first.from_cache is False,
    )

    clock.advance(91 * 24 * 3600)
    third = fetcher.fetch("https://example.com/luwei")
    check(
        section,
        "过期后重新抓取",
        "网络请求变成 2 次",
        f"网络请求 {len(network_calls)} 次",
        len(network_calls) == 2 and third.from_cache is False,
    )

    stored = cache.peek("https://example.com/luwei")
    has_hash = bool(stored and stored.content_hash)
    robots_state = stored.robots_allowed if stored else None
    check(
        section,
        "缓存记录内容指纹与 robots 状态",
        "content_hash 非空、robots_allowed=True",
        f"hash={has_hash} robots={robots_state}",
        has_hash and robots_state is True,
    )


# -- 3. 字段级补齐 -------------------------------------------------------------


def verify_field_fallback() -> None:
    section = "3 字段补齐"

    report = build_field_report(manual={"name": "陆伟", "institution": "武汉大学"})
    missing_required = report.missing_required(REQUIRED_FIELDS)
    check(
        section,
        "只填姓名+学校：不卡流程",
        "必填项齐全，其余标未知",
        f"必填缺失={missing_required} 未知字段={len(report.unknown_fields())} 个",
        missing_required == [] and report.get("email").status == FieldStatus.UNKNOWN,
    )

    structured = extract_structured_fields(_JSON_LD)
    check(
        section,
        "读官网结构化信息（JSON-LD）",
        "能取到 姓名/职称/邮箱/机构/院系/方向",
        f"取到 {len(structured)} 个字段",
        len(structured) >= 6 and structured.get("department") == "信息管理学院",
    )

    rules = extract_page_fields(_PAGE_TEXT)
    check(
        section,
        "无 LLM 也能从正文提取",
        "能取到 邮箱/院系/职称/研究方向",
        f"取到 {sorted(rules)}",
        {"email", "department", "title", "declared_interests"} <= set(rules),
    )

    priority = build_field_report(
        manual={"name": "陆伟"},
        homepage_html=_JSON_LD,
        page_text=_PAGE_TEXT,
    )
    check(
        section,
        "手动填写的优先级最高",
        "姓名的来源=你手动填写",
        f"姓名来源={priority.get('name').source}",
        priority.get("name").source == "你手动填写",
    )

    suspicious = build_field_report(manual={"name": "陆伟", "email": "luwei@gmail.com"})
    check(
        section,
        "可疑邮箱不当作已确认",
        "状态=INFERRED 且带核对提示",
        f"状态={suspicious.get('email').status.value} 提示={bool(suspicious.get('email').note)}",
        suspicious.get("email").status == FieldStatus.INFERRED
        and bool(suspicious.get("email").note),
    )

    summary = build_field_report(manual={"name": "陆伟", "institution": "武汉大学"}).summary()
    check(
        section,
        "界面能显示一句话小结",
        "含「已获取 x/6 个字段」与「还缺」",
        summary,
        "已获取" in summary and "还缺" in summary,
    )


# -- 4. 身份核对 ---------------------------------------------------------------


class FakeProfile:
    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "")
        self.institution = kwargs.get("institution", "")
        self.department = kwargs.get("department", "")
        self.title = kwargs.get("title", "")
        self.email = kwargs.get("email", "")
        self.declared_interests = kwargs.get("declared_interests", [])


def verify_identity() -> None:
    section = "4 身份核对"

    good = assess_identity(
        name="陆伟",
        institution="武汉大学",
        homepage_profile=FakeProfile(institution="武汉大学信息管理学院", email="luwei@whu.edu.cn"),
    )
    check(
        section,
        "邮箱像学校 + 机构一致",
        "结论=已核对一致",
        f"结论={good.status.value} 支持 {good.score} 项",
        good.status == ResolutionStatus.CONFIRMED,
    )

    conflict = assess_identity(
        name="王伟",
        institution="武汉大学",
        homepage_profile=FakeProfile(institution="中国药科大学", email="wang@cpu.edu.cn"),
    )
    check(
        section,
        "机构对不上（可能同名他人）",
        "结论=需要确认，并列出冲突",
        f"结论={conflict.status.value} 冲突 {len(conflict.conflicts)} 条",
        conflict.status == ResolutionStatus.REVIEW_REQUIRED and bool(conflict.conflicts),
    )

    rejected = assess_identity(
        name="王伟",
        institution="武汉大学",
        homepage_profile=FakeProfile(institution="中国药科大学", email="wang@gmail.com"),
    )
    check(
        section,
        "只有冲突、没有支持",
        "结论=明显对不上",
        f"结论={rejected.status.value}",
        rejected.status == ResolutionStatus.REJECTED,
    )

    empty = assess_identity(name="陆伟")
    check(
        section,
        "没有任何线索",
        "结论=需要确认，并说明原因",
        f"结论={empty.status.value} 说明={empty.reasons[:1]}",
        empty.status == ResolutionStatus.REVIEW_REQUIRED
        and any("线索" in item for item in empty.reasons),
    )


# -- 5. 导师名册 ---------------------------------------------------------------


def verify_roster() -> None:
    section = "5 导师名册"

    check(
        section,
        "名册只保留三个字段",
        "模型里没有 rate/description",
        f"字段={sorted(RosterEntry.model_fields)}",
        set(RosterEntry.model_fields) == {"university", "department", "supervisor"},
    )

    db_path = settings.data_dir / "supervisor_roster.db"
    if not db_path.exists():
        check(section, "本地名册已导入", "数据库存在", f"未找到 {db_path}", False)
        return

    repo = RosterRepository(db_path)
    total = repo.count()
    check(
        section,
        "本地名册已导入",
        "条数 > 1000",
        f"{total} 条 / {len(repo.universities())} 所学校",
        total > 1000,
    )

    names = repo.lookup("武汉大学", "信息管理学院")
    check(
        section,
        "能按学校+学院查到导师姓名",
        "武汉大学 信息管理学院 有名单",
        f"{len(names)} 位：{'、'.join(names[:5])}…" if names else "没查到",
        len(names) >= 5,
    )

    departments = repo.departments("武汉大学")
    check(
        section,
        "能列出该校有哪些学院",
        "学院数 > 10",
        f"{len(departments)} 个学院",
        len(departments) > 10,
    )


# -- 汇总 ---------------------------------------------------------------------


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)

    verify_fetch()
    verify_cache()
    verify_field_fallback()
    verify_identity()
    verify_roster()

    width = 46
    print("=" * 100)
    print("Advisor-Fit AI · 本阶段新增能力自动验证")
    print("=" * 100)
    current = ""
    for title, expected, actual, ok in RESULTS:
        section = title.split("]")[0] + "]"
        if section != current:
            current = section
            print()
        print(f"{title[:width]:<{width}} {'通过' if ok else '失败'}")
        print(f"{'':<{width}} 预期：{expected}")
        print(f"{'':<{width}} 实际：{actual}")

    passed = sum(1 for *_, ok in RESULTS if ok)
    total = len(RESULTS)
    tail = "　—— 全部符合预期" if passed == total else "　—— 有未通过项"
    print()
    print("=" * 100)
    print(f"结果：{passed}/{total} 项通过{tail}")
    print("=" * 100)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
