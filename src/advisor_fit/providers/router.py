"""检索路由器：把「查一位导师的论文」变成「按学科分流查多个学术库，再合并去重」。

它对外长得和单个 Provider 一样（`search_publications` / `search_by_title`），
所以导师研究 Agent 不需要知道背后有几个库。

一次检索的流程：

```
① 定学科：用户指定 → 关键词规则 → 查完主干库后用结果里的学科标签反推
② 选源  ：按学科 + 语言挑出该查的库，按优先级排序（见 sources.py 的登记表）
③ 逐个查：同源限速、计入预算闸门、单个源失败/被反爬只跳过不中断
④ 合并  ：DOI 优先、其次标题+年份，字段互补并记下"被几个库收录"
```

三条工程底线：
- **任何单源故障都不影响整体**：状态记成 `error`/`blocked` 并告诉用户原因；
- **超预算就停**：由 BudgetTracker 判定，剩下的源记成 `budget`，不抛异常；
- **不新增幻觉**：学科判断只用于"决定去哪个库查"，不会写进报告当结论。
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from advisor_fit.harness.budget import BudgetTracker
from advisor_fit.providers.academic import Work
from advisor_fit.providers.arxiv import ArxivProvider
from advisor_fit.providers.crossref import CrossrefProvider
from advisor_fit.providers.dblp import DblpProvider, DblpUnavailable
from advisor_fit.providers.disciplines import (
    discipline_label,
    dominant_discipline,
    guess_discipline,
    is_known_discipline,
)
from advisor_fit.providers.europepmc import EuropePmcProvider
from advisor_fit.providers.merge import merge_works
from advisor_fit.providers.openalex import OpenAlexProvider, OpenAlexUnavailable
from advisor_fit.providers.scopus import ScopusProvider, ScopusUnavailable
from advisor_fit.providers.sources import (
    SourceSpec,
    classify_sources,
    load_discipline_keywords,
    load_sources,
)
from advisor_fit.providers.springer import SpringerProvider, SpringerUnavailable
from advisor_fit.providers.wanfang import WanfangProvider

# 源状态：ok 有结果 / empty 查到了但没结果 / error 出错 / blocked 被对方拦下
#         no_key、disabled、language 是"没查"的三种原因 / budget 是超预算主动停
_SKIP_REASONS = {
    "no_key": "未配置访问 Key，本次跳过",
    "disabled": "在检索源配置里被关闭",
    "language": "与所选检索语言不符",
}


@dataclass
class SourceOutcome:
    key: str
    label: str
    status: str
    count: int = 0
    reason: str = ""
    cost: float = 0.0

    @property
    def searched(self) -> bool:
        return self.status in ("ok", "empty")


@dataclass
class SearchOutcome:
    works: list[Work] = field(default_factory=list)
    discipline: str = "general"
    outcomes: list[SourceOutcome] = field(default_factory=list)

    def describe(self) -> str:
        """给用户看的一句话口径：查了谁、谁没查、为什么。"""
        parts: list[str] = []
        used = [item for item in self.outcomes if item.searched and item.count]
        if used:
            parts.append(
                "、".join(
                    f"{item.label} {item.count} 篇"
                    + (f"（{item.reason}）" if item.reason else "")
                    for item in used
                )
            )
        else:
            parts.append("本次没有检索到论文")
        quiet = [item for item in self.outcomes if item.status in ("blocked", "error")]
        if quiet:
            parts.append("未返回：" + "、".join(f"{item.label}（{item.reason}）" for item in quiet))
        return "；".join(parts)


class SearchRouter:
    """多源检索路由器。对外提供与单个 Provider 相同的方法签名。"""

    # 让 Agent 知道"外部请求次数已经由我自己记了"，避免重复计数
    counts_external_calls = True

    def __init__(
        self,
        *,
        specs: tuple[SourceSpec, ...] | None = None,
        key_values: dict[str, str] | None = None,
        budget: BudgetTracker | None = None,
        contact_email: str = "",
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        clients: dict | None = None,
        default_limit: int = 20,
        since_year: int | None = None,
    ) -> None:
        self._specs = specs if specs is not None else load_sources()
        self._key_values = dict(key_values or {})
        self._keywords = load_discipline_keywords()
        self.budget = budget
        self.contact_email = contact_email
        self._sleep = sleeper
        self._clock = clock
        self._clients = clients or {}
        self._providers: dict[str, object] = {}
        self._accepts_english: dict[int, bool] = {}
        self._last_call_at: dict[str, float] = {}
        self._queried: set[str] = set()
        self.default_limit = default_limit
        self.since_year = since_year
        self.outcomes: list[SourceOutcome] = []
        self.discipline = "general"
        self.cost = 0.0
        self.merged: list[Work] = []

    # ---------- 预算与限速 ----------

    def bind_budget(self, budget: BudgetTracker | None) -> None:
        self.budget = budget

    def _wait_for_slot(self, spec: SourceSpec) -> None:
        last = self._last_call_at.get(spec.key)
        if last is not None:
            wait = spec.min_interval_seconds - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call_at[spec.key] = self._clock()

    # ---------- Provider 装配 ----------

    def _supports_english_name(self, provider) -> bool:
        """Provider 是否接受 english_name（缓存，避免每次检索都做一次签名检查）。"""
        key = id(provider)
        cached = self._accepts_english.get(key)
        if cached is not None:
            return cached
        try:
            params = inspect.signature(provider.search_publications).parameters
            supported = "english_name" in params or any(
                param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
            )
        except (TypeError, ValueError):  # pragma: no cover - 内建/装饰过的 Provider
            supported = False
        self._accepts_english[key] = supported
        return supported

    def _provider_for(self, key: str):
        if key in self._providers:
            return self._providers[key]
        client = self._clients.get(key)
        provider = None
        if key == "openalex":
            provider = OpenAlexProvider(client=client, mailto=self.contact_email)
        elif key == "dblp":
            provider = DblpProvider(client=client)
        elif key == "europepmc":
            provider = EuropePmcProvider(client=client)
        elif key == "arxiv":
            provider = ArxivProvider(client=client)
        elif key == "crossref":
            provider = CrossrefProvider(client=client)
        elif key == "wanfang":
            provider = WanfangProvider(self._key_values.get("wanfang_app_key", ""), client=client)
        elif key == "scopus":
            provider = ScopusProvider(self._key_values.get("scopus_api_key", ""), client=client)
        elif key == "springer_meta":
            provider = SpringerProvider(
                self._key_values.get("springer_meta_api_key", ""), client=client
            )
        elif key == "springer_open_access":
            provider = SpringerProvider(
                open_access_api_key=self._key_values.get("springer_open_access_api_key", ""),
                client=client,
            )
        # 未接入适配器的源（登记了但代码还没写）：返回 None，路由器会如实说明并跳过
        self._providers[key] = provider
        return provider

    # ---------- 单源调用 ----------

    def _call_source(
        self,
        spec: SourceSpec,
        *,
        name: str,
        institution: str | None,
        limit: int,
        by_title: bool,
        english_name: str | None = None,
    ) -> tuple[SourceOutcome, list[Work]]:
        def failed(status: str, reason: str) -> tuple[SourceOutcome, list[Work]]:
            return SourceOutcome(key=spec.key, label=spec.label, status=status, reason=reason), []

        provider = self._provider_for(spec.key)
        if provider is None:
            return failed("error", "这个来源还没有接入适配器")

        if self.budget is not None:
            exceeded = self.budget.exceeded()
            if exceeded:
                return failed("budget", f"已达资源上限 {exceeded}")

        method = getattr(provider, "search_by_title" if by_title else "search_publications", None)
        if method is None:
            return failed("error", "该来源不支持这种检索")

        # 姓名怎么传：
        # - 支持 english_name 的库（OpenAlex）：把中文名和英文名都给它，由它自己去匹配作者档案；
        # - 其它国际库（Europe PMC / arXiv / DBLP）：它们只认罗马化姓名，有英文名就用英文名；
        # - 中文库：始终用中文名。
        query_name = name
        extra_kwargs: dict = {}
        if not by_title:
            if self._supports_english_name(provider):
                extra_kwargs["english_name"] = english_name or None
            elif english_name and spec.language != "zh":
                query_name = english_name

        self._wait_for_slot(spec)
        if self.budget is not None:
            self.budget.record_external_call(spec.key)
        self.cost += spec.cost_per_call

        last_error: Exception | None = None
        for attempt in range(2):  # 网络抖动重试一次：学术库偶尔会读超时
            try:
                works = (
                    method(name, limit=limit)
                    if by_title
                    else method(query_name, institution=institution, limit=limit, **extra_kwargs)
                )
            except (
                DblpUnavailable,
                OpenAlexUnavailable,
                ScopusUnavailable,
                SpringerUnavailable,
            ) as exc:
                return failed("blocked", str(exc))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt == 0:
                    self._sleep(1.0)
                    continue
                return failed("error", f"{type(exc).__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001 - 单源失败绝不影响主流程
                return failed("error", f"{type(exc).__name__}: {exc}")
            else:
                last_error = None
                break
        if last_error is not None:  # pragma: no cover - 上面的分支已经返回
            return failed("error", f"{type(last_error).__name__}: {last_error}")

        cleaned = [work for work in works if work and (work.title or "").strip()]
        for work in cleaned:
            if not work.source_platform:
                work.source_platform = spec.label
        # 有结果时也把"这条线索的依据"带上（例如 OpenAlex 选中了哪个作者档案），便于用户核对
        detail = "" if by_title else getattr(provider, "last_author_note", "")
        return (
            SourceOutcome(
                key=spec.key,
                label=spec.label,
                status="ok" if cleaned else "empty",
                count=len(cleaned),
                reason=detail,
                cost=spec.cost_per_call,
            ),
            cleaned,
        )

    # ---------- 学科判定 ----------

    def resolve_discipline(self, explicit: str | None = None, *texts: str | None) -> str:
        """用户指定 > 已从检索结果反推的学科 > 关键词规则 > 通用。"""
        if is_known_discipline(explicit):
            return explicit or "general"
        if self.discipline != "general":
            return self.discipline
        return guess_discipline(*texts, keywords=self._keywords) or "general"

    def _learn_discipline_from(self, works: list[Work]) -> str | None:
        """从检索结果反推学科；至少要 3 篇论文支持，避免被个别噪声论文带偏。"""
        return dominant_discipline(
            [key for work in works for key in work.disciplines], min_support=3
        )

    # ---------- 对外检索入口 ----------

    def search_publications(
        self,
        name: str,
        *,
        institution: str | None = None,
        english_name: str | None = None,
        source: str = "auto",
        discipline: str | None = None,
        known_directions: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Work]:
        """按姓名（+机构提示）检索；返回合并去重后的候选论文。"""
        limit = limit or self.default_limit
        language = source if source in ("zh", "en") else "auto"
        resolved = self.resolve_discipline(
            discipline, name, institution, " ".join(known_directions or [])
        )
        if is_known_discipline(resolved):
            self.discipline = resolved

        selected, notes = self._select(resolved, language)
        self._record_notes(notes)
        found = self._find(
            selected,
            name=name,
            institution=institution,
            english_name=english_name,
            limit=limit,
        )

        # 学科未知时：先用主干库的结果反推学科，再把该学科的专业库补上（最多多花 1–2 次请求）
        if resolved == "general" and language != "zh":
            learned = self._learn_discipline_from(found)
            if learned:
                self.discipline = learned
                extra, extra_notes = self._select(learned, language)
                self._record_notes(extra_notes)
                extra = [spec for spec in extra if spec.key not in self._queried]
                found.extend(
                    self._find(
                        extra,
                        name=name,
                        institution=institution,
                        english_name=english_name,
                        limit=limit,
                    )
                )

        self.merged = merge_works(found)
        return self.merged

    def search_by_title(
        self,
        title: str,
        *,
        source: str = "auto",
        discipline: str | None = None,
        limit: int = 5,
    ) -> list[Work]:
        """按论文标题兜底检索：结果同样跨库合并。"""
        language = source if source in ("zh", "en") else "auto"
        resolved = self.resolve_discipline(discipline, title)
        if is_known_discipline(resolved):
            self.discipline = resolved
        selected, notes = classify_sources(
            resolved, specs=self._specs, key_values=self._key_values, language=language
        )
        selected = [spec for spec in selected if spec.supports_title_search]
        self._record_notes(notes)
        works = self._find(selected, name=title, institution=None, limit=limit, by_title=True)
        self.merged = merge_works(works)
        return self.merged

    def _select(
        self, discipline: str, language: str
    ) -> tuple[list[SourceSpec], list[tuple[SourceSpec, str]]]:
        """挑出可用于「按作者检索」的源（标题检索能力见 supports_title_search）。"""
        selected, notes = classify_sources(
            discipline, specs=self._specs, key_values=self._key_values, language=language
        )
        return [spec for spec in selected if spec.supports_author_search], notes

    def _find(
        self,
        specs: list[SourceSpec],
        *,
        name: str,
        institution: str | None,
        limit: int,
        english_name: str | None = None,
        by_title: bool = False,
    ) -> list[Work]:
        works: list[Work] = []
        for spec in specs:
            self._queried.add(spec.key)
            outcome, found = self._call_source(
                spec,
                name=name,
                institution=institution,
                limit=limit,
                by_title=by_title,
                english_name=english_name,
            )
            self.outcomes.append(outcome)
            works.extend(found)
            if outcome.status == "budget":
                break
        return works

    # ---------- 汇总 ----------

    def _record_notes(self, notes: list[tuple[SourceSpec, str]]) -> None:
        for spec, reason in notes:
            if any(item.key == spec.key and item.status == reason for item in self.outcomes):
                continue
            self.outcomes.append(
                SourceOutcome(
                    key=spec.key,
                    label=spec.label,
                    status=reason,
                    reason=_SKIP_REASONS.get(reason, reason),
                )
            )

    def outcome(self) -> SearchOutcome:
        return SearchOutcome(
            works=self.merged, discipline=self.discipline, outcomes=list(self.outcomes)
        )

    def discipline_text(self) -> str:
        return discipline_label(self.discipline)

    def describe(self) -> str:
        return self.outcome().describe()

    def scope_label(self, source: str | None, discipline: str | None = None) -> str:
        """给 Agent 的任务描述用：本次能用哪些库。"""
        language = source if source in ("zh", "en") else "auto"
        resolved = self.resolve_discipline(discipline)
        specs, _ = classify_sources(
            resolved, specs=self._specs, key_values=self._key_values, language=language
        )
        if not specs:
            return "当前没有可用的检索来源（请检查 Key 或检索语言设置）"
        return "、".join(spec.label for spec in specs)
