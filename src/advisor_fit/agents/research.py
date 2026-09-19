"""导师研究 Agent：多轮检索 + 标题兜底 + 作者消歧 + 履历调查 + 人工确认门控。

检索主库为万方（中文库 OpenPeriodical/OpenConference 与英文库 OpenPeriodicalEng）；
当按作者名查不到时，可改用「代表论文标题」检索，万方查不到再降级 Crossref。
检索到的候选论文经「作者消歧」判断是否属于目标导师本人（同名作者问题）；
若候选论文机构与填写学校不同，会做一次「履历调查」（用候选机构名二次检索），
把「曾任职单位」的证据链补齐。所有结果仍标「待用户确认」，绝不自动认定归属。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from advisor_fit.harness.loop import run_loop
from advisor_fit.llm.provider import NullLLM
from advisor_fit.providers.academic import Work
from advisor_fit.providers.crossref import CrossrefProvider
from advisor_fit.providers.wanfang import SOURCE_LABELS


def work_to_paper(work: Work) -> dict:
    """把 Provider 的 Work 转成展示用的候选论文字典（含消歧标注字段）。"""
    return {
        "title": work.title,
        "year": work.year,
        "abstract": work.abstract or "（未提供摘要，请手动补充）",
        "source_url": work.source_url or "",
        "source_platform": work.source_platform or "万方",
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


_DISAMBIG_INSTRUCTIONS = (
    "判断每篇候选论文是否属于目标导师本人（而非同名作者）。"
    "依据：论文机构是否匹配、合作作者是否稳定、研究主题是否连续。"
    "belongs 取 true 表示属于该导师，false 表示同名他人。"
    "若论文机构与目标学校不同、但研究主题或合作作者与该导师连续，"
    "可能是导师曾任职单位或刚调动，此时 belongs 取 true 且 needs_review 取 true，"
    "reason 用中文说明「机构不同、疑似调动」；只有领域明显不同才 belongs=false。"
    "不确定时 belongs 取 true（保留给用户人工确认）。"
    "index 必须与 payload 中每篇论文的 index 一一对应，不要遗漏。"
)


def _institutions_conflict(paper_institution: str, institution: str | None) -> bool:
    """规则消歧：两边机构都非空且互不包含时，视为疑似同名。"""
    paper_institution = (paper_institution or "").strip()
    institution = (institution or "").strip()
    if not paper_institution or not institution:
        return False
    return institution not in paper_institution and paper_institution not in institution


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


def disambiguate_papers(
    llm, papers: list[dict], *, professor_name: str, institution: str | None
) -> list[dict]:
    """对候选论文做作者消歧：LLM 判定每篇是否属于该导师；无 LLM 时用机构规则兜底。"""
    if not papers:
        return papers

    payload = {
        "professor": {"name": professor_name, "institution": institution or ""},
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
        return _rule_disambiguate(papers, institution)

    if not isinstance(output, DisambiguationOutput):
        return _rule_disambiguate(papers, institution)

    verdicts = {verdict.index: verdict for verdict in output.verdicts}
    for index, paper in enumerate(papers):
        verdict = verdicts.get(index)
        if verdict is not None:
            paper["belongs"] = verdict.belongs
            paper["needs_review"] = verdict.needs_review
            paper["disambig_reason"] = verdict.reason
    return papers


_SEARCH_TOOLS = {"search_by_author", "search_by_title"}


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
    source: str | None = None,
    max_steps: int = 6,
    resume_steps: list[dict] | None = None,
    granted_confirmations: list[str] | None = None,
) -> ResearchResult:
    """跑一轮导师研究：检索 →（必要时）确认门控 → 作者消歧 → 履历调查。

    search_institution 用于指定与「当前单位」不同的检索机构（如导师调动前的单位）。
    seed_titles 用于作者名查不到时按「代表论文标题」兜底检索。
    """
    crossref = CrossrefProvider()

    def search_by_author(name: str, institution: str | None = None, source: str = "zh") -> dict:
        if source == "en" and english_name:
            name = english_name
        works = provider.search_publications(name, institution=institution, source=source)
        return {"count": len(works), "papers": [work_to_paper(work) for work in works]}

    def search_by_title(title: str, source: str = "zh") -> dict:
        works = provider.search_by_title(title, source=source)
        if not works:
            works = crossref.search_by_title(title)
        return {"count": len(works), "papers": [work_to_paper(work) for work in works]}

    if isinstance(llm, NullLLM):
        return _research_without_llm(
            provider, crossref, name, institution, source, search_institution, seed_titles
        )

    label = SOURCE_LABELS.get(source, "万方")
    scope = f"只用 {label}" if source else "可用万方中文库（source='zh'）或英文库（source='en'）"
    task = (
        f"检索导师「{name}」的论文。学校/单位：{institution or '未提供'}。"
        f"备选检索机构：{search_institution or '无'}。"
        f"导师英文名（可能为空）：{english_name or '无'}。"
        f"检索范围：{scope}。"
        "已先按「姓名+学校」和「代表论文标题」查过若干轮（见 history）。"
        "作者名查不到时用 search_by_title 按论文标题兜底；"
        "结果太少时去掉学校重试（institution 传空字符串），"
        "或（在提供英文名时）切到 source='en' 用英文名再查。"
        "若候选论文机构与学校明显不符、疑似同名作者，请求人工确认后再继续。"
    )
    tools = [
        ("search_by_author", "按姓名+学校检索万方候选论文", search_by_author),
        ("search_by_title", "按论文标题检索（万方查不到降级 Crossref）", search_by_title),
    ]

    # 预热：确定性先查一轮，交给 LLM 判断是否扩搜/换库。
    seed_steps: list[dict] = []
    if resume_steps is None:
        seed_source = source or "zh"
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

    steps = run_loop(
        llm,
        tools,
        task,
        max_steps=max_steps,
        resume_steps=resume_steps if resume_steps is not None else seed_steps,
        granted_confirmations=granted_confirmations,
    )

    needs_confirmation = ""
    for step in steps:
        if "needs_confirmation" in step:
            needs_confirmation = step["needs_confirmation"]
            break

    papers = _merge_search_results(steps)
    if needs_confirmation:
        return ResearchResult(papers=papers, needs_confirmation=needs_confirmation, log=steps)

    disambiguate_papers(llm, papers, professor_name=name, institution=institution)
    papers = _investigate_affiliations(
        provider, name, papers, institution, source or "zh", english_name
    )
    return ResearchResult(papers=papers, log=steps)


def _investigate_affiliations(
    provider,
    name: str,
    papers: list[dict],
    primary_institution: str | None,
    source: str,
    english_name: str | None,
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
                english_name or name, institution=inst, source=source
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
) -> ResearchResult:
    """无 LLM 时：确定性检索（机构 → 备选机构 → 标题兜底）+ 机构规则消歧。"""
    papers: list[dict] = []
    for inst in (search_institution, institution):
        if not inst:
            continue
        works = provider.search_publications(name, institution=inst, source=source or "zh")
        papers.extend(work_to_paper(work) for work in works)
    if len(papers) < 3:
        for title in (seed_titles or [])[:5]:
            works = provider.search_by_title(title, source=source or "zh")
            if not works:
                works = crossref.search_by_title(title)
            papers.extend(work_to_paper(work) for work in works)
    papers = _dedupe_papers(papers)
    _rule_disambiguate(papers, institution)
    return ResearchResult(papers=papers)
