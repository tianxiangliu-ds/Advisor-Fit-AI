"""导师研究 Agent：多轮检索 + 标题兜底 + 作者消歧 + 履历调查 + 人工确认门控。

检索走「检索路由器」（`providers/router.py`）：按学科分流查多个免费学术库
（OpenAlex 打底，计算机/医学/物理等补查专业库），跨库合并去重后返回候选；
配了万方 Key 时中文库也会一起查。当按作者名查不到时，可改用「代表论文标题」检索。
检索到的候选论文经「作者消歧」判断是否属于目标导师本人（同名作者问题）；
若候选论文机构与填写学校不同，会做一次「履历调查」（用候选机构名二次检索），
把「曾任职单位」的证据链补齐。所有结果仍标「待用户确认」，绝不自动认定归属。
"""

from __future__ import annotations

import inspect
import time
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel

from advisor_fit.agents.tools import (
    build_registry,
    make_affiliation_tool,
    make_homepage_tool,
)
from advisor_fit.harness.budget import BudgetTracker
from advisor_fit.harness.loop import run_loop
from advisor_fit.harness.trace import RunTrace, digest_args
from advisor_fit.harness.workflow import Stage
from advisor_fit.llm.prompts import prompt_text
from advisor_fit.llm.provider import NullLLM
from advisor_fit.providers.academic import Work
from advisor_fit.providers.affiliation import institutions_conflict
from advisor_fit.providers.crossref import CrossrefProvider


def _mark(trace: RunTrace | None, stage: Stage) -> None:
    """记录工作流阶段；trace 为空时什么都不做。"""
    if trace is not None:
        trace.mark_stage(stage)


def work_to_paper(work: Work) -> dict:
    """把 Provider 的 Work 转成展示用的候选论文字典（含消歧标注字段）。"""
    platform = getattr(work, "source_platform", None)
    sources = list(getattr(work, "sources", None) or ([platform] if platform else []))
    return {
        "title": work.title,
        "year": work.year,
        "abstract": work.abstract or "（未提供摘要，请手动补充）",
        "source_url": work.source_url or "",
        "source_platform": platform or (sources[0] if sources else "未知来源"),
        "sources": sources,
        "citation_count": getattr(work, "citation_count", None),
        "disciplines": list(getattr(work, "disciplines", None) or []),
        "keywords": work.topics or [],
        "user_confirmed": False,
        "authors": work.authors,
        "institution": work.institution,
        "venue": work.venue or "",
        "belongs": True,
        "needs_review": False,
        "disambig_reason": "",
        "affiliation_note": "",
    }


class PaperVerdict(BaseModel):
    index: int
    belongs: bool = True
    needs_review: bool = False
    reason: str = ""


class DisambiguationOutput(BaseModel):
    verdicts: list[PaperVerdict] = []


_DISAMBIG_INSTRUCTIONS = prompt_text("disambiguation")


def _institutions_conflict(paper_institution: str, institution: str | None) -> bool:
    """规则消歧：两边机构都非空且互不包含时，视为疑似同名。

    具体规则（含"中英混排不判冲突"和高校别名词典）见 `providers/affiliation.py`。
    """
    return institutions_conflict(paper_institution, institution)


def _rule_disambiguate(papers: list[dict], institution: str | None) -> list[dict]:
    """无 LLM 时的确定性消歧：机构不符的标记为需人工审核（可能是导师曾任职单位）。"""
    for paper in papers:
        if _institutions_conflict(paper.get("institution", ""), institution):
            paper["belongs"] = True
            paper["needs_review"] = True
            paper["disambig_reason"] = (
                f"机构与填写学校不同（{paper.get('institution')}），可能为曾任职单位"
            )
    return papers


def _fingerprint_prefilter(
    papers: list[dict], professor_name: str, institution: str | None
) -> list[dict]:
    """合作者指纹预筛：机构不符且与「稳定合作者集合」无交集的，直接判为同名。

    稳定合作者 = 在候选池中出现 ≥2 次的非本人作者。只有当存在稳定团队信号时才触发，
    避免误伤独自署名或一次性合作的真实论文。
    """
    coauthor = Counter()
    for paper in papers:
        for author in paper.get("authors", []):
            author = str(author).strip()
            if author and author != (professor_name or "").strip():
                coauthor[author] += 1
    stable = {a for a, n in coauthor.items() if n >= 2}
    if not stable:
        return papers

    for paper in papers:
        if paper.get("belongs") is False:
            continue
        if not _institutions_conflict(paper.get("institution", ""), institution):
            continue
        authors = {str(a).strip() for a in paper.get("authors", [])}
        if authors & stable:
            continue
        paper["belongs"] = False
        paper["needs_review"] = False
        paper["disambig_reason"] = "机构不符且合作者无交集，疑似同名"
    return papers


def disambiguate_papers(
    llm,
    papers: list[dict],
    *,
    professor_name: str,
    institution: str | None,
    known_directions: list[str] | None = None,
) -> list[dict]:
    """对候选论文做作者消歧：LLM 判定每篇是否属于该导师；无 LLM 时用机构规则兜底。"""
    if not papers:
        return papers

    payload = {
        "professor": {
            "name": professor_name,
            "institution": institution or "",
            "known_directions": known_directions or [],
        },
        "papers": [
            {
                "index": index,
                "title": paper.get("title"),
                "authors": paper.get("authors", []),
                "institution": paper.get("institution", ""),
                "year": paper.get("year"),
                "abstract": (paper.get("abstract") or "")[:300],
            }
            for index, paper in enumerate(papers)
        ],
    }
    try:
        output = llm.generate(
            schema=DisambiguationOutput,
            instructions=_DISAMBIG_INSTRUCTIONS,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - LLM 不可用时降级为规则
        return _fingerprint_prefilter(
            _rule_disambiguate(papers, institution), professor_name, institution
        )

    if not isinstance(output, DisambiguationOutput):
        return _fingerprint_prefilter(
            _rule_disambiguate(papers, institution), professor_name, institution
        )

    verdicts = {verdict.index: verdict for verdict in output.verdicts}
    for index, paper in enumerate(papers):
        verdict = verdicts.get(index)
        if verdict is not None:
            paper["belongs"] = verdict.belongs
            paper["needs_review"] = verdict.needs_review
            paper["disambig_reason"] = verdict.reason
    return _fingerprint_prefilter(papers, professor_name, institution)


_SEARCH_TOOLS = {"search_by_author", "search_by_title"}


def _supported_kwargs(provider, **kwargs) -> dict:
    """只把 Provider 真正支持的参数传过去。

    检索路由器支持 `english_name`（国际库优先用罗马化姓名），但更简单的自定义 Provider
    可能只有基础签名；这里按签名过滤，避免因为多传一个可选参数就整轮检索失败。
    """
    try:
        params = inspect.signature(provider.search_publications).parameters
    except (TypeError, ValueError):  # pragma: no cover - 内建/装饰过的 Provider
        params = {}
    if not params:
        return {key: value for key, value in kwargs.items() if key in ("institution", "source")}
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in params}


def _merge_search_results(steps: list[dict]) -> list[dict]:
    """合并多轮检索结果，按标题去重，后搜到的在前。"""
    merged: dict[str, dict] = {}
    for step in steps:
        if step.get("tool") not in _SEARCH_TOOLS:
            continue
        result = step.get("result")
        if not isinstance(result, dict):
            continue
        for paper in result.get("papers", []):
            title = paper.get("title", "")
            if title and title not in merged:
                merged[title] = paper
    return list(merged.values())


def _dedupe_papers(papers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for paper in papers:
        title = paper.get("title", "")
        if title and title not in seen:
            seen.add(title)
            result.append(paper)
    return result


@dataclass
class ResearchResult:
    papers: list[dict] = field(default_factory=list)
    needs_confirmation: str = ""
    log: list[dict] = field(default_factory=list)
    trace: RunTrace | None = None


def _make_step(tool: str, args: dict, result: dict) -> dict:
    return {"tool": tool, "args": args, "result": result}


def research_professor(
    llm,
    provider,
    *,
    name: str,
    institution: str | None = None,
    english_name: str | None = None,
    search_institution: str | None = None,
    seed_titles: list[str] | None = None,
    known_directions: list[str] | None = None,
    source: str | None = None,
    discipline: str | None = None,
    max_steps: int = 6,
    resume_steps: list[dict] | None = None,
    granted_confirmations: list[str] | None = None,
    budget: BudgetTracker | None = None,
    trace: RunTrace | None = None,
) -> ResearchResult:
    """跑一轮导师研究：检索 →（必要时）确认门控 → 作者消歧 → 履历调查。

    search_institution 用于指定与「当前单位」不同的检索机构（如导师调动前的单位）。
    seed_titles 用于作者名查不到时按「代表论文标题」兜底检索。
    discipline 用于指定学科（决定补查哪些专业库）；留空则由路由器自己判断。
    """
    crossref = CrossrefProvider()
    self_counting = bool(getattr(provider, "counts_external_calls", False))
    if budget is not None and hasattr(provider, "bind_budget"):
        provider.bind_budget(budget)

    def _count_external() -> None:
        # 检索路由器会按"每个来源分别"记账，这里不能重复计数
        if budget is not None and not self_counting:
            budget.record_external_call("external")

    # 边查边累积的候选集：`check_paper_affiliations` 工具靠它读"当前"候选，
    # 而不是等整轮跑完再汇总。
    collected_papers: list[dict] = []

    def _collect(papers: list[dict]) -> None:
        collected_papers[:] = _dedupe_papers([*collected_papers, *papers])

    def search_by_author(name: str, institution: str | None = None, source: str = "auto") -> dict:
        if source == "en" and english_name:
            name = english_name
        _count_external()
        works = provider.search_publications(
            name,
            **_supported_kwargs(
                provider,
                institution=institution,
                source=source,
                discipline=discipline,
                english_name=english_name or None,
            ),
        )
        papers = [work_to_paper(work) for work in works]
        _collect(papers)
        return {"count": len(works), "papers": papers}

    def search_by_title(title: str, source: str = "auto") -> dict:
        _count_external()
        works = provider.search_by_title(
            title, **_supported_kwargs(provider, source=source, discipline=discipline)
        )
        if not works:
            works = crossref.search_by_title(title)
        papers = [work_to_paper(work) for work in works]
        _collect(papers)
        return {"count": len(works), "papers": papers}

    if isinstance(llm, NullLLM):
        return _research_without_llm(
            provider,
            crossref,
            name,
            institution,
            source,
            search_institution,
            seed_titles,
            discipline=discipline,
            budget=budget,
            trace=trace,
        )

    scope_fn = getattr(provider, "scope_label", None)
    scope = scope_fn(source) if callable(scope_fn) else "可用当前的学术检索来源"
    task = (
        f"检索导师「{name}」的论文。学校/单位：{institution or '未提供'}。"
        f"备选检索机构：{search_institution or '无'}。"
        f"导师英文名（可能为空）：{english_name or '无'}。"
        f"本次可用的检索来源：{scope}。"
        "已先按「姓名+学校」和「代表论文标题」查过若干轮（见 history）。"
        "作者名查不到时用 search_by_title 按论文标题兜底；"
        "结果太少时去掉学校重试（institution 传空字符串），"
        "或（在提供英文名时）切到 source='en' 用英文名再查。"
        "想确认这人到底研究什么、或论文一直查不到时，"
        "用 fetch_professor_homepage 打开他的个人主页看看。"
        "对候选取舍拿不准时用 check_paper_affiliations 核对机构一致性。"
        "若候选论文机构与学校明显不符、疑似同名作者，请求人工确认后再继续。"
    )

    # 工具契约集中在 agents/tools.py 声明，这里只提供实现。
    # 候选集是每轮变的，所以用 papers_provider 让工具在调用时才取。
    registry = build_registry(
        {
            "search_by_author": search_by_author,
            "search_by_title": search_by_title,
            "fetch_professor_homepage": make_homepage_tool(),
            "check_paper_affiliations": make_affiliation_tool(
                lambda: collected_papers, institution=search_institution or institution or ""
            ),
        }
    )

    # 预热：确定性先查一轮，交给 LLM 判断是否扩搜/换库。
    if trace is not None:
        trace.mark_stage(Stage.SEEDING)
    seed_steps: list[dict] = []
    if resume_steps is None:
        seed_source = source or "auto"
        primary_inst = search_institution or institution
        if primary_inst:
            result = search_by_author(name, primary_inst, seed_source)
            seed_steps.append(
                _make_step(
                    "search_by_author",
                    {"name": name, "institution": primary_inst, "source": seed_source},
                    result,
                )
            )
        if search_institution and institution and search_institution != institution:
            result = search_by_author(name, institution, seed_source)
            seed_steps.append(
                _make_step(
                    "search_by_author",
                    {"name": name, "institution": institution, "source": seed_source},
                    result,
                )
            )
        author_hits = sum(
            step["result"].get("count", 0)
            for step in seed_steps
            if step["tool"] == "search_by_author"
        )
        if author_hits < 3:
            for title in (seed_titles or [])[:5]:
                result = search_by_title(title, seed_source)
                seed_steps.append(
                    _make_step("search_by_title", {"title": title}, result)
                )

    if trace is not None:
        trace.mark_stage(Stage.SEARCHING)
    steps = run_loop(
        llm,
        task=task,
        max_steps=max_steps,
        resume_steps=resume_steps if resume_steps is not None else seed_steps,
        granted_confirmations=granted_confirmations,
        registry=registry,
        budget=budget,
        trace=trace,
    )

    needs_confirmation = ""
    for step in steps:
        if "needs_confirmation" in step:
            needs_confirmation = step["needs_confirmation"]
            break

    papers = _merge_search_results(steps)
    if needs_confirmation:
        if trace is not None:
            trace.mark_stage(Stage.AWAITING_CONFIRMATION)
        return ResearchResult(
            papers=papers,
            needs_confirmation=needs_confirmation,
            log=steps,
            trace=trace,
        )

    disambiguate_papers(
        llm, papers, professor_name=name, institution=institution,
        known_directions=known_directions,
    )
    _mark(trace, Stage.INVESTIGATING)
    papers = _investigate_affiliations(
        provider, name, papers, institution, source or "auto", english_name,
        discipline=discipline,
    )
    _mark(trace, Stage.DONE)
    return ResearchResult(papers=papers, log=steps, trace=trace)


def _investigate_affiliations(
    provider,
    name: str,
    papers: list[dict],
    primary_institution: str | None,
    source: str,
    english_name: str | None,
    *,
    discipline: str | None = None,
) -> list[dict]:
    """履历调查：对「机构与填写学校不同」的候选，用候选机构名二次检索，补「曾任职单位」证据。"""
    alt_counts: dict[str, int] = {}
    for paper in papers:
        if not paper.get("needs_review"):
            continue
        inst = (paper.get("institution") or "").strip()
        if inst and inst != (primary_institution or "").strip():
            alt_counts[inst] = alt_counts.get(inst, 0) + 1

    extra: list[dict] = []
    for inst in sorted(alt_counts, key=alt_counts.get, reverse=True)[:2]:
        try:
            works = provider.search_publications(
                english_name or name,
                **_supported_kwargs(
                    provider,
                    institution=inst,
                    source=source,
                    discipline=discipline,
                    english_name=english_name or None,
                ),
            )
        except Exception:  # noqa: BLE001 - 履历调查失败不阻断主流程
            continue
        for work in works:
            paper = work_to_paper(work)
            paper["affiliation_note"] = f"曾任职单位：{inst}"
            paper["needs_review"] = False
            extra.append(paper)

    return _dedupe_papers([*papers, *extra])


def _research_without_llm(
    provider,
    crossref: CrossrefProvider,
    name: str,
    institution: str | None,
    source: str | None,
    search_institution: str | None,
    seed_titles: list[str] | None,
    *,
    discipline: str | None = None,
    budget: BudgetTracker | None = None,
    trace: RunTrace | None = None,
) -> ResearchResult:
    """无 LLM 时：确定性检索（机构 → 备选机构 → 标题兜底）+ 机构规则消歧。"""
    self_counting = bool(getattr(provider, "counts_external_calls", False))

    def _count() -> None:
        if budget is not None and not self_counting:
            budget.record_external_call("external")

    if trace is not None:
        # 规则路径没有模型决策，不能假装有；但**它真的调用了哪些检索要如实记**。
        # 否则没配 Key 的演示站上，访客点完「一键研究」看到的轨迹区几乎是空的。
        trace.mode = "rule"
        trace.mark_stage(Stage.SEEDING)
        trace.mark_stage(Stage.SEARCHING)
        trace.mark_stage(Stage.DISAMBIGUATING)

    def _record(tool: str, args: dict, count: int, started: float) -> None:
        if trace is None:
            return
        trace.add(
            kind="tool_call",
            name=tool,
            args_digest=digest_args(args),
            status="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
            summary=f"count={count}",
        )

    papers: list[dict] = []
    for inst in (search_institution, institution):
        if not inst:
            continue
        _count()
        started = time.monotonic()
        works = provider.search_publications(
            name,
            **_supported_kwargs(
                provider, institution=inst, source=source or "auto", discipline=discipline
            ),
        )
        _record("search_by_author", {"name": name, "institution": inst}, len(works), started)
        papers.extend(work_to_paper(work) for work in works)
    if len(papers) < 3:
        for title in (seed_titles or [])[:5]:
            _count()
            started = time.monotonic()
            works = provider.search_by_title(
                title,
                **_supported_kwargs(provider, source=source or "auto", discipline=discipline),
            )
            if not works:
                works = crossref.search_by_title(title)
            _record("search_by_title", {"title": title}, len(works), started)
            papers.extend(work_to_paper(work) for work in works)
    papers = _dedupe_papers(papers)
    _rule_disambiguate(papers, institution)
    if trace is not None:
        trace.mark_stage(Stage.DONE)
        trace.add(
            kind="done", name="rule_pipeline",
            summary=f"papers={len(papers)}（规则路径，未使用大模型）",
        )
        if budget is not None:
            trace.budget = budget.snapshot()
    return ResearchResult(papers=papers, trace=trace)
