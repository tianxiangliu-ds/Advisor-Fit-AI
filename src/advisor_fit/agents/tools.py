"""导师研究 Agent 的工具箱：把散落的能力包成「带契约的正式工具」。

为什么单独成模块：

- 工具的**契约**（名字 / 说明 / 参数 JSON Schema / 权限 / 限流 / 缓存 / 超时 / 重试）
  集中声明在一处，主流程不再各写一份；
- 新增一个工具只改这里 + 注册实现，不动主循环；
- 契约可以被单测钉住——schema 写错、说明为空、声明了却没实现，都会在测试里暴露；
- 界面上「这次 Agent 手里有哪些工具」直接由这份清单生成，不会和实际能力脱节。

工具分两类权限：
- `network`：会发外部请求，受预算闸门统计（`BudgetTracker.record_external_call`）；
- `read`：只读本次运行已有的数据，不消耗外部额度。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from advisor_fit.analysis.identity import institutions_agree
from advisor_fit.harness.tools import RetryPolicy, ToolFn, ToolRegistry, ToolSpec
from advisor_fit.ingest.homepage import extract_title, fetch_homepage, html_to_text
from advisor_fit.ingest.profile_fallback import extract_page_fields, extract_structured_fields

# -- 契约声明 ------------------------------------------------------------------

TOOL_SPECS: dict[str, ToolSpec] = {
    "search_by_author": ToolSpec(
        name="search_by_author",
        description="按姓名+学校在多个学术库中检索候选论文",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "institution": {"type": ["string", "null"]},
                "source": {"type": "string", "enum": ["auto", "zh", "en"]},
            },
            "required": ["name"],
        },
        permission="network",
        rate_limit_per_run=4,
        cache_ttl_seconds=86_400,
        timeout_seconds=20.0,
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    ),
    "search_by_title": ToolSpec(
        name="search_by_title",
        description="按论文标题检索（多个学术库 + Crossref 兜底），作者名查不到时用它兜底",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "source": {"type": "string", "enum": ["auto", "zh", "en"]},
            },
            "required": ["title"],
        },
        permission="network",
        rate_limit_per_run=6,
        cache_ttl_seconds=86_400,
        timeout_seconds=20.0,
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    ),
    "fetch_professor_homepage": ToolSpec(
        name="fetch_professor_homepage",
        description=(
            "打开导师个人主页，抽取职称/院系/邮箱/公开写出的研究方向。"
            "论文查不到、或想确认这人到底研究什么时用它。"
        ),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        permission="network",
        rate_limit_per_run=2,
        cache_ttl_seconds=604_800,  # 个人主页一周内不会大变
        timeout_seconds=20.0,
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    ),
    "check_paper_affiliations": ToolSpec(
        name="check_paper_affiliations",
        description=(
            "检查当前候选论文的机构与目标学校是否一致，给出「一致/冲突/未知」的条数。"
            "冲突条数多说明很可能混进了同名作者，应当请求人工确认而不是继续扩搜。"
        ),
        parameters={"type": "object", "properties": {}},
        permission="read",  # 只读本次运行已有的候选集，不耗外部额度
        timeout_seconds=5.0,
    ),
}


def tool_names() -> list[str]:
    """本工具箱声明的全部工具名（界面与文档都用它，避免和实际能力脱节）。"""
    return list(TOOL_SPECS)


def build_registry(implementations: dict[str, ToolFn]) -> ToolRegistry:
    """按契约装配工具注册表。

    声明的工具必须有实现、实现的工具必须有声明——两边对不上就报错。
    宁可启动时炸，也不要让模型看到一个"存在但调不通"的工具。
    """
    declared = set(TOOL_SPECS)
    provided = set(implementations)
    missing = sorted(declared - provided)
    extra = sorted(provided - declared)
    if missing or extra:
        raise ValueError(
            f"工具契约与实现不匹配：缺实现 {missing or '无'}；"
            f"多出未声明 {extra or '无'}"
        )
    registry = ToolRegistry()
    for name, spec in TOOL_SPECS.items():
        registry.register(spec, implementations[name])
    return registry


# -- 工具实现 ------------------------------------------------------------------


def make_homepage_tool(*, fetcher: Callable[[str], str] | None = None) -> ToolFn:
    """造一个「读导师主页」工具。

    `fetcher` 可注入，便于测试时不真的联网；默认走 `fetch_homepage`。
    """
    fetch = fetcher or fetch_homepage

    def fetch_professor_homepage(url: str) -> dict[str, Any]:
        if not str(url).startswith(("http://", "https://")):
            return {"error": "url 必须是 http/https 开头"}
        try:
            html = fetch(url)
        except Exception as exc:  # noqa: BLE001 - 工具失败回传给模型，不中断循环
            return {"error": f"抓取失败：{type(exc).__name__}: {exc}"}

        text = html_to_text(html) or ""
        # 先看结构化信息（JSON-LD / meta），再用正文规则补；结构化更可信
        fields = extract_page_fields(text)
        for key, value in extract_structured_fields(html).items():
            if value:
                fields[key] = value
        return {
            "url": url,
            "title": extract_title(html),
            "email": fields.get("email", ""),
            "department": fields.get("department", ""),
            "title_text": fields.get("title", ""),
            "declared_interests": fields.get("declared_interests", ""),
            "text_chars": len(text),
        }

    return fetch_professor_homepage


def make_affiliation_tool(
    papers_provider: Callable[[], Sequence[dict[str, Any]]],
    *,
    institution: str,
) -> ToolFn:
    """造一个「核对候选论文机构」工具。

    `papers_provider` 在调用时才取当前候选集——工具是长驻的，候选集是每轮变的。
    """

    def check_paper_affiliations() -> dict[str, Any]:
        papers = list(papers_provider() or [])
        if not papers:
            return {"total": 0, "match": 0, "conflict": 0, "unknown": 0,
                    "verdict": "还没有候选论文可核对"}

        match = conflict = unknown = 0
        conflicting_titles: list[str] = []
        for paper in papers:
            paper_inst = str(paper.get("institution") or "").strip()
            if not paper_inst or not institution:
                unknown += 1
                continue
            agree = institutions_agree(paper_inst, institution)
            if agree is False:
                conflict += 1
                if len(conflicting_titles) < 5 and paper.get("title"):
                    conflicting_titles.append(str(paper["title"]))
            elif agree is True:
                match += 1
            else:
                unknown += 1

        if conflict and conflict >= max(1, len(papers) // 3):
            verdict = "冲突较多，很可能混进了同名作者，建议请求人工确认"
        elif conflict:
            verdict = "有少量机构冲突，建议人工核对这几篇"
        elif match:
            verdict = "机构基本一致"
        else:
            verdict = "机构信息缺失，无法判断，建议人工核对"

        return {
            "total": len(papers),
            "match": match,
            "conflict": conflict,
            "unknown": unknown,
            "conflicting_titles": conflicting_titles,
            "verdict": verdict,
        }

    return check_paper_affiliations
