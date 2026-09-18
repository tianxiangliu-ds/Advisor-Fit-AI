"""导师双选 AI 助手（MVP）—— Streamlit 五步向导。

1. 输入 CV + 导师官方 URL
2. 确认学生事实
3. 确认导师身份（作者消歧）
4. 证据化导师画像与匹配报告
5. 邮件草稿、导出、删除

无“发送”按钮；所有结论可回溯到证据。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import streamlit as st

from advisor_fit.analysis.matching import build_match_report
from advisor_fit.analysis.professor_profile import assemble_professor_profile
from advisor_fit.config import settings
from advisor_fit.export.report import export_json, export_markdown
from advisor_fit.ingest.cv import build_student_profile, extract_pdf_text
from advisor_fit.ingest.official_profile import parse_official_profile
from advisor_fit.ingest.webpage import FetchedPage, fetch_official_page
from advisor_fit.llm.claims import generate_and_validate_claims
from advisor_fit.llm.drafting import generate_draft
from advisor_fit.llm.provider import OpenAIStructuredLLM
from advisor_fit.models.common import SourceRecord
from advisor_fit.models.evidence import Evidence
from advisor_fit.pipeline import PipelineResult
from advisor_fit.providers.academic import AuthorQuery
from advisor_fit.providers.openalex import OpenAlexProvider
from advisor_fit.resolution.author import resolve_author
from advisor_fit.storage.repository import Repository
from advisor_fit.validation.claims import validate_claims
from advisor_fit.validation.draft import validate_draft


class _NullLLM:
    def generate(self, **_kwargs):
        raise RuntimeError("未配置 LLM")


def _provider() -> OpenAlexProvider:
    return OpenAlexProvider(
        cache_dir=settings.data_dir / "cache", mailto=settings.openalex_mailto
    )


def _llm():
    return OpenAIStructuredLLM() if settings.llm_api_key else _NullLLM()


def _run(coro):
    return asyncio.run(coro)


def _reset_run() -> None:
    if st.session_state.get("run_id") and st.session_state.get("repo") is not None:
        try:
            st.session_state.repo.delete_run(st.session_state.run_id)
        except ValueError:
            pass
    for key in (
        "student",
        "facts_confirmed",
        "anchor",
        "resolution",
        "page",
        "result",
        "selected_author_id",
    ):
        st.session_state[key] = None if key != "facts_confirmed" else {}
    st.session_state.repo = Repository(settings.data_dir / "app.db")
    st.session_state.run_id = st.session_state.repo.create_run()


def _build_result(student, confirmed_map, anchor, author_id, page) -> None:
    provider = _provider()
    llm = _llm()

    confirmed_ids = {fid for fid, ok in confirmed_map.items() if ok}
    student = student.model_copy(
        update={
            "facts": [
                f.model_copy(update={"user_confirmed": f.id in confirmed_ids})
                for f in student.facts
            ]
        }
    )

    since_year = datetime.now(UTC).year - 5
    works = _run(provider.list_recent_works(author_id, since_year))

    evidences: list[Evidence] = [
        Evidence(
            id="ev_official_profile",
            source_type="official_page",
            source_url=page.final_url,
            title=page.title,
            retrieved_at=page.retrieved_at,
            evidence_text=(page.text or "")[:500],
            source_tier=1,
        )
    ]
    for work in works:
        evidences.append(
            Evidence(
                id=f"ev_{work.id}",
                source_type="openalex",
                canonical_id=work.doi,
                title=work.title,
                published_date=str(work.year) if work.year else None,
                evidence_text=work.title,
                source_tier=2,
            )
        )

    professor = assemble_professor_profile(anchor, works, evidences)
    match_report = build_match_report(student, professor)

    evidence_map = {ev.id: ev for ev in evidences}
    claims = generate_and_validate_claims(llm, {"evidences": evidence_map})
    claim_validation = validate_claims(claims, evidence_map)
    draft = generate_draft(llm, student, professor, match_report)
    draft_validation = validate_draft(draft, student, evidence_map)

    source = SourceRecord(
        id=f"src_{st.session_state.run_id[:8]}_official",
        source_type="official_page",
        url=page.final_url,
        title=page.title,
        retrieved_at=page.retrieved_at,
    )
    st.session_state.result = PipelineResult(
        run_id=st.session_state.run_id,
        student=student,
        professor=professor,
        match_report=match_report,
        resolution=st.session_state.resolution,
        claims=claims,
        claim_validation=claim_validation,
        draft=draft,
        draft_validation=draft_validation,
        evidences=evidences,
        sources=[source],
        warnings=[],
    )


def _render_report(result) -> None:
    prof = result.professor
    st.subheader("导师画像")
    for field in prof.iter_asserted_fields():
        st.write(f"**{field.key}**：{field.value}")
    if prof.declared_interests:
        st.write("官网声明方向：" + "、".join(t.topic for t in prof.declared_interests))
    if prof.observed_recent_topics:
        st.write(
            "近年论文观察主题："
            + "、".join(f"{t.topic}（{t.trend}）" for t in prof.observed_recent_topics)
        )
    st.write(f"招生状态：{prof.recruiting.status.value}")

    st.subheader("匹配分析")
    report = result.match_report
    st.write(f"研究匹配：**{report.research_fit.value}**")
    st.write(f"建议：**{report.recommendation.value}**")
    st.write(f"机会信号：{report.opportunity_signal}")
    if report.strengths:
        st.write("强项：" + "、".join(d.label for d in report.strengths))
    if report.gaps:
        st.write("缺口：" + "、".join(report.gaps))
    if report.questions_to_ask:
        st.write("待询问：" + "、".join(report.questions_to_ask))

    st.subheader("证据来源")
    for ev in result.evidences:
        st.write(f"- [{ev.source_type}] {ev.title or ev.id}")


def _render_draft(result) -> None:
    draft = result.draft
    st.write(f"主题：{draft.subject or '（无）'}")
    for sentence in draft.sentences:
        st.write(f"- {sentence.text}")
    if draft.warnings:
        st.warning("；".join(draft.warnings))
    if not result.claim_validation.ok:
        st.warning("部分声明被移除：" + str(result.claim_validation.errors))


# --------------------------------------------------------------------------
st.set_page_config(page_title="导师双选 AI 助手", layout="wide")
st.title("导师双选 AI 助手（MVP）")
st.caption("证据驱动的导师理解与匹配 · 仅本地 · 不发送邮件 · 所有结论可回溯来源")

for _key in (
    "student",
    "facts_confirmed",
    "anchor",
    "resolution",
    "page",
    "result",
    "selected_author_id",
):
    if _key not in st.session_state:
        st.session_state[_key] = {} if _key == "facts_confirmed" else None

if "repo" not in st.session_state:
    st.session_state.repo = Repository(settings.data_dir / "app.db")
    st.session_state.run_id = st.session_state.repo.create_run()

# ---- Step 1：输入 -----------------------------------------------------------
st.header("① 输入")
uploaded = st.file_uploader("上传 CV（PDF，本地解析，不上传）", type=["pdf"])
name_hint = st.text_input("你的姓名（可选，用于脱敏）")
url = st.text_input("导师官方主页 URL", placeholder="https://university.edu/faculty/xxx")

if uploaded is not None and st.button("解析 CV", type="primary"):
    tmp_path = settings.uploads_dir / f"{st.session_state.run_id}.pdf"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(uploaded.getvalue())
    parsed = extract_pdf_text(tmp_path)
    st.session_state.student = build_student_profile(parsed)
    st.session_state.student = st.session_state.student.model_copy(
        update={"name": name_hint or None}
    )
    if parsed.warnings:
        st.warning("；".join(parsed.warnings))

# ---- Step 2：确认学生事实 ---------------------------------------------------
student = st.session_state.student
confirmed_count = 0
if student is not None:
    st.header("② 确认学生事实")
    st.caption("只有勾选的事实会进入匹配与邮件；未确认前不用于任何生成。")
    if not student.facts:
        st.info("未能从 CV 中抽取到结构化事实，可回到上一步检查文件。")
    for fact in student.facts:
        st.session_state.facts_confirmed[fact.id] = st.checkbox(
            f"{fact.field}：{fact.value}", key=f"fact_{fact.id}"
        )
    confirmed_count = sum(st.session_state.facts_confirmed.values())
    if confirmed_count == 0:
        st.warning("请至少确认一项事实。")

# ---- Step 3：确认导师身份 ---------------------------------------------------
if student is not None and confirmed_count > 0 and url:
    st.header("③ 确认导师身份")
    if st.button("抓取官方页并召回作者候选"):
        try:
            page = _run(fetch_official_page(url, settings.advisor_fit_user_agent))
            st.session_state.page = page
            facts = parse_official_profile(page)
            if not facts.name:
                st.error("未从页面识别到姓名，请检查 URL 或改用下方粘贴文本。")
            else:
                st.session_state.anchor = facts.to_anchor()
                candidates = _run(
                    _provider().search_authors(
                        AuthorQuery(name=facts.name, institution=facts.institution)
                    )
                )
                st.session_state.resolution = resolve_author(st.session_state.anchor, candidates)
        except Exception as exc:  # noqa: BLE001
            st.error(f"抓取失败：{exc}")

    page = st.session_state.page
    if page is not None and st.session_state.anchor is None:
        pasted = st.text_area("页面抓取失败或无法解析时，可粘贴页面 HTML/正文")
        if pasted:
            st.session_state.page = FetchedPage(final_url=url, status_code=200, html=pasted)
            facts = parse_official_profile(st.session_state.page)
            if facts.name:
                st.session_state.anchor = facts.to_anchor()
                candidates = _run(
                    _provider().search_authors(
                        AuthorQuery(name=facts.name, institution=facts.institution)
                    )
                )
                st.session_state.resolution = resolve_author(st.session_state.anchor, candidates)

    resolution = st.session_state.resolution
    if resolution is not None:
        if resolution.status == "CONFIRMED":
            reasons = "、".join(resolution.reasons)
            st.success(f"已确认作者：{resolution.selected_author_id}（{reasons}）")
            st.session_state.selected_author_id = resolution.selected_author_id
        elif resolution.status == "REJECTED":
            st.error("出现明确身份冲突：" + "、".join(resolution.conflicts))
        else:
            st.warning("存在重名或证据不足，请人工选择候选作者：")
            options = {
                f"{c.name}（{', '.join(c.affiliations) or '未知机构'}）": c.id
                for c in resolution.ranked_candidates
            }
            if options:
                chosen = st.radio("候选作者", list(options.keys()), key="candidate")
                st.session_state.selected_author_id = options[chosen]

    if st.session_state.selected_author_id and st.button("生成报告与邮件草稿", type="primary"):
        with st.spinner("生成中…"):
            _build_result(
                st.session_state.student,
                st.session_state.facts_confirmed,
                st.session_state.anchor,
                st.session_state.selected_author_id,
                st.session_state.page,
            )

# ---- Step 4/5 展示 ----------------------------------------------------------
result = st.session_state.result
if result is not None:
    st.header("④ 报告")
    _render_report(result)

    st.header("⑤ 邮件草稿、导出与删除")
    _render_draft(result)

    st.download_button("下载 Markdown", export_markdown(result), file_name="report.md")
    st.download_button("下载 JSON", export_json(result), file_name="report.json")

    if st.button("删除本次数据", type="secondary"):
        _reset_run()
        st.rerun()
