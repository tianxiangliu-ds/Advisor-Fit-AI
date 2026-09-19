"""导师研究 Agent：多轮检索 + 作者消歧 + 人工确认门控。

检索只查万方（中文库 OpenPeriodical/OpenConference 与英文库 OpenPeriodicalEng），
由 Harness 循环让 LLM 自主决定查询策略（是否带学校、是否切英文名）并在结果不理想时重试。
检索到的候选论文再经「作者消歧」判断是否属于目标导师本人（同名作者问题）。
所有结果仍标「待用户确认」，绝不自动认定归属。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from advisor_fit.harness.loop import run_loop
from advisor_fit.llm.provider import NullLLM
from advisor_fit.providers.academic import Work
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
    """对候选论文做作者消歧：LLM 判定每篇是否属于该导师；无 LLM 时用机构规则兜底。

    返回原地标注了 belongs / disambig_reason 的 papers 列表。
    """
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


def _merge_search_results(steps: list[dict]) -> list[dict]:
    """合并多轮检索结果，按标题去重，后搜到的在前。"""
    merged: dict[str, dict] = {}
    for step in steps:
        if step.get("tool") != "search_publications":
            continue
        result = step.get("result")
        if not isinstance(result, dict):
            continue
        for paper in result.get("papers", []):
            title = paper.get("title", "")
            if title and title not in merged:
                merged[title] = paper
    return list(merged.values())


@dataclass
class ResearchResult:
    papers: list[dict] = field(default_factory=list)
    needs_confirmation: str = ""
    log: list[dict] = field(default_factory=list)


def research_professor(
    llm,
    provider,
    *,
    name: str,
    institution: str | None = None,
    english_name: str | None = None,
    source: str | None = None,
    max_steps: int = 4,
    resume_steps: list[dict] | None = None,
    granted_confirmations: list[str] | None = None,
) -> ResearchResult:
    """跑一轮导师研究：检索 →（必要时）确认门控 → 作者消歧。

    source 为 None 时让 LLM 自主决定中/英文库；传入 "zh"/"en" 则强制该库。
    """
    if isinstance(llm, NullLLM):
        return _research_without_llm(provider, name, institution, source)

    def search_publications(
        name: str, institution: str | None = None, source: str = "zh"
    ) -> dict:
        if source == "en" and english_name:
            name = english_name
        works = provider.search_publications(name, institution=institution, source=source)
        return {"count": len(works), "papers": [work_to_paper(work) for work in works]}

    label = SOURCE_LABELS.get(source, "万方")
    scope = f"只用 {label}" if source else "可用万方中文库（source='zh'）或英文库（source='en'）"
    task = (
        f"检索导师「{name}」的论文。学校/单位：{institution or '未提供'}。"
        f"导师英文名（可能为空）：{english_name or '无'}。"
        f"检索范围：{scope}。"
        "已先用「姓名+学校」查过一轮（见 history）。"
        "结果太少或无结果时，去掉学校（institution 传空字符串）重试，"
        "或（在提供了英文名时）切到 source='en' 用英文名再查。"
        "若候选论文机构与学校明显不符、疑似同名作者，请求人工确认后再继续。"
    )
    tools = [("search_publications", "按姓名+学校检索万方候选论文", search_publications)]

    # 预热：只要提供了学校，就先确定性带学校检索一轮，交给 LLM 判断是否需要扩搜/换库。
    seed_steps: list[dict] = []
    if not resume_steps and institution:
        seed_source = source or "zh"
        seed_result = search_publications(name, institution, seed_source)
        seed_steps = [
            {
                "tool": "search_publications",
                "args": {"name": name, "institution": institution, "source": seed_source},
                "result": seed_result,
            }
        ]

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
        return ResearchResult(
            papers=papers, needs_confirmation=needs_confirmation, log=steps
        )

    disambiguate_papers(llm, papers, professor_name=name, institution=institution)
    return ResearchResult(papers=papers, log=steps)


def _research_without_llm(
    provider, name: str, institution: str | None, source: str | None
) -> ResearchResult:
    """无 LLM 时：确定性检索 + 机构规则消歧。"""
    works = provider.search_publications(
        name, institution=institution, source=source or "zh"
    )
    papers = [work_to_paper(work) for work in works]
    _rule_disambiguate(papers, institution)
    return ResearchResult(papers=papers)
