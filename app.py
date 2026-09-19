"""导师双选 AI 助手 v0.1：人工证据输入版 Streamlit 应用。

流程：CV 本地解析与人工确认 → 手动导师资料 → 手动录入已核实论文
→ 可解释匹配 → 事实锁定邮件 → 导出与完整删除。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from advisor_fit.agents.research import research_professor
from advisor_fit.config import settings
from advisor_fit.export.report import export_docx, export_json, export_markdown
from advisor_fit.ingest.cv import (
    apply_fact_edits,
    build_student_profile,
    extract_pdf_markdown,
    extract_pdf_text,
)
from advisor_fit.ingest.cv_llm import build_student_profile_llm
from advisor_fit.ingest.homepage import extract_homepage_profile
from advisor_fit.ingest.manual_professor import (
    ManualPaperInput,
    ManualProfessorInput,
    validate_paper_values,
)
from advisor_fit.llm.provider import NullLLM, build_llm
from advisor_fit.manual_pipeline import run_manual_pipeline
from advisor_fit.providers.wanfang import WanfangProvider
from advisor_fit.storage.repository import Repository


def _llm():
    return build_llm()


def _build_student_profile(upload_path, parsed):
    """LLM 结构化抽取优先；未配置或失败时降级为规则抽取。"""
    llm = _llm()
    if isinstance(llm, NullLLM):
        return build_student_profile(parsed)
    text = extract_pdf_markdown(upload_path) or parsed.text
    try:
        return build_student_profile_llm(text, llm)
    except Exception:  # noqa: BLE001 - LLM 失败必须降级到规则路径
        return build_student_profile(parsed)


def _new_run() -> None:
    st.session_state.repo = Repository(settings.data_dir / "app.db")
    st.session_state.run_id = st.session_state.repo.create_run()
    st.session_state.setdefault("session_run_ids", []).append(st.session_state.run_id)


def _clear_professor_state() -> None:
    for key in (
        "prof_name",
        "prof_institution",
        "prof_department",
        "prof_title",
        "prof_email",
        "prof_interests",
        "prof_homepage",
        "prof_english_name",
        "candidate_papers",
        "_research_confirm",
        "_research_steps",
        "_research_granted",
        "result",
    ):
        st.session_state.pop(key, None)


def _reset_professor() -> None:
    """开始新导师：清空导师区并开一条新记录，保留学生简历与历史。"""
    _clear_professor_state()
    _new_run()


def _reset_all() -> None:
    """清空本次会话全部数据：删除本次会话创建的所有记录与上传的 CV。"""
    repo = st.session_state.get("repo")
    for run_id in list(st.session_state.get("session_run_ids", [])):
        if repo is not None:
            try:
                repo.delete_run(run_id)
            except ValueError:
                pass
    cv_path = st.session_state.get("cv_path")
    if cv_path:
        try:
            Path(cv_path).unlink(missing_ok=True)
        except OSError:
            pass
    for key in (
        "student",
        "student_fact_editor",
        "parsed_text",
        "result",
        "student_name",
        "cv_path",
        "session_run_ids",
        "prof_name",
        "prof_institution",
        "prof_department",
        "prof_title",
        "prof_email",
        "prof_interests",
        "prof_homepage",
        "prof_english_name",
        "candidate_papers",
        "_research_confirm",
        "_research_steps",
        "_research_granted",
        "viewed_run",
        "show_compare",
    ):
        st.session_state.pop(key, None)
    _new_run()


FACT_FIELDS = ["skill", "degree", "institution", "interest", "project", "publication"]


def _fact_rows(student) -> list[dict]:
    return [
        {"id": fact.id, "confirmed": True, "field": fact.field, "value": str(fact.value or "")}
        for fact in student.facts
    ]


def _add_fact() -> None:
    st.session_state.student_fact_editor.append(
        {"id": f"user_{uuid.uuid4().hex[:8]}", "confirmed": False, "field": "skill", "value": ""}
    )


def _remove_fact(row_id: str) -> None:
    st.session_state.student_fact_editor = [
        row for row in st.session_state.student_fact_editor if row.get("id") != row_id
    ]


def _set_all_facts(confirmed: bool) -> None:
    for row in st.session_state.student_fact_editor:
        row["confirmed"] = confirmed
    for key in list(st.session_state.keys()):
        if key.startswith("fact_conf_"):
            st.session_state[key] = confirmed


def _split_terms(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace("；", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _run_research(name: str, institution: str, mode: str, english_name: str) -> None:
    """用导师研究 Agent 检索并消歧，结果与确认门控写入 session_state。"""
    provider = WanfangProvider(settings.wanfang_app_key)
    source = None if mode == "auto" else mode
    result = research_professor(
        _llm(),
        provider,
        name=name,
        institution=institution or None,
        english_name=english_name or None,
        source=source,
        resume_steps=st.session_state.get("_research_steps"),
        granted_confirmations=st.session_state.get("_research_granted", []),
    )
    if result.needs_confirmation:
        st.session_state["_research_confirm"] = result.needs_confirmation
        st.session_state["_research_steps"] = result.log
        st.session_state.candidate_papers = result.papers
    else:
        st.session_state.pop("_research_confirm", None)
        st.session_state["_research_steps"] = None
        st.session_state["_research_granted"] = []
        st.session_state.candidate_papers = result.papers


def _set_all_candidates(confirmed: bool, *, matching_only: bool = False) -> None:
    """候选论文全选/全不选（直接写 widget 状态与候选数据）。"""
    papers = st.session_state.get("candidate_papers", [])
    for index, paper in enumerate(papers):
        if matching_only and not paper.get("belongs", True):
            continue
        paper["user_confirmed"] = confirmed
        st.session_state[f"cand_paper_{index}"] = confirmed


def _candidate_institutions() -> list[str]:
    """候选论文中出现的机构，按出现次数排序（用于机构确认按钮）。"""
    from collections import Counter

    counter: Counter = Counter()
    for paper in st.session_state.get("candidate_papers", []):
        inst = str(paper.get("institution") or "").strip()
        if inst:
            counter[inst] += 1
    return [inst for inst, _ in counter.most_common()]


_FIT_BADGES = {
    "STRONG": "🟢 强",
    "PARTIAL": "🟡 部分",
    "WEAK": "🔴 弱",
    "UNKNOWN": "⚪ 未知",
}

_REC_BADGES = {
    "WORTH_CONTACTING": "✅ 值得联系",
    "LEARN_MORE": "🔍 建议深入了解",
    "LOW_PRIORITY": "⏸ 优先级较低",
    "INSUFFICIENT_EVIDENCE": "⚪ 证据不足",
}


def _badge(mapping: dict, value: str) -> str:
    return mapping.get(value, value)


def _render_report(result) -> None:
    professor = result.professor
    st.subheader("导师画像")
    for field in professor.iter_asserted_fields():
        st.write(f"**{field.key}**：{field.value}")
    if professor.homepage:
        st.markdown(f"官方主页：[{professor.homepage}]({professor.homepage})")
    if professor.declared_interests:
        st.write("用户录入的公开方向：" + "、".join(t.topic for t in professor.declared_interests))
    if professor.observed_recent_topics:
        st.write(
            "论文观察主题："
            + "、".join(
                f"{topic.topic}（{topic.trend}）"
                for topic in professor.observed_recent_topics
            )
        )

    direction = getattr(result, "direction_summary", None)
    if direction is not None and direction.summary:
        st.subheader("研究方向归纳（LLM）")
        st.write(direction.summary)
        if direction.topics:
            st.write("主题：" + "、".join(direction.topics))

    st.subheader("已核实论文")
    for publication in professor.recent_publications:
        label = f"{publication.title}（{publication.year or '年份未知'}）"
        if publication.source_url:
            st.markdown(f"- [{label}]({publication.source_url})")
        else:
            st.write(f"- {label}")
        if publication.keywords:
            st.caption("关键词：" + "、".join(publication.keywords))

    st.subheader("匹配分析")
    report = result.match_report
    st.write(f"研究匹配：**{_badge(_FIT_BADGES, report.research_fit.value)}**")
    st.write(f"建议：**{_badge(_REC_BADGES, report.recommendation.value)}**")
    st.write(f"证据充分度：{report.evidence_sufficiency}")
    st.write(f"招生机会信号：{report.opportunity_signal}")
    if report.strengths:
        st.write("强项：" + "、".join(item.label for item in report.strengths))
    if report.gaps:
        st.write("待补足方向：" + "、".join(report.gaps))
    if report.questions_to_ask:
        st.write("建议询问：" + "、".join(report.questions_to_ask))

    deep = getattr(result, "deep_analysis", None)
    if deep is not None:
        sections = [
            ("研究交集", deep.research_intersection),
            ("方法能力匹配", deep.method_match),
            ("背景缺口", deep.background_gaps),
            ("最值得读的论文", deep.recommended_papers),
            ("联系前应补的知识", deep.knowledge_to_supplement),
        ]
        rendered = any(points for _, points in sections)
        if rendered:
            st.subheader("深度匹配分析（LLM）")
            for label, points in sections:
                if points:
                    st.write(f"**{label}**")
                    for point in points:
                        st.write(f"- {point.text}")

    st.subheader("证据来源")
    for evidence in result.evidences:
        label = evidence.title or evidence.id
        if evidence.source_url:
            st.markdown(f"- [{evidence.source_type}] [{label}]({evidence.source_url})")
        else:
            st.write(f"- [{evidence.source_type}] {label}")


def _render_draft(result) -> None:
    draft = result.draft
    st.write(f"主题：{draft.subject or '（无）'}")
    body = "\n".join(sentence.text for sentence in draft.sentences)
    st.code(body, language=None)
    if draft.warnings:
        st.info("；".join(draft.warnings))
    if not result.draft_validation.ok:
        st.warning("邮件中仍有未通过校验的句子，请不要直接发送。")


def _load_run_view(repo, run_id):
    """从数据库重建可展示的历史结果（仅报告所需字段）。"""
    from types import SimpleNamespace

    from advisor_fit.models.evidence import Evidence
    from advisor_fit.models.match import Draft, MatchReport
    from advisor_fit.models.professor import ProfessorProfile
    from advisor_fit.validation.draft import DraftValidationResult

    professor_data = repo.load_latest(run_id, "professor_profile")
    match_data = repo.load_latest(run_id, "match")
    draft_data = repo.load_latest(run_id, "draft")
    if not (professor_data and match_data and draft_data):
        return None
    return SimpleNamespace(
        run_id=run_id,
        professor=ProfessorProfile(**professor_data),
        match_report=MatchReport(**match_data),
        draft=Draft(**draft_data),
        draft_validation=DraftValidationResult(),
        evidences=[Evidence(**data) for data in repo.load_artifacts(run_id, "evidence")],
    )


def _compare_runs(repo) -> list[dict]:
    """把历史已完成的 run 整理成对比行。"""
    from advisor_fit.models.match import MatchReport
    from advisor_fit.models.professor import ProfessorProfile

    rows: list[dict] = []
    for run in repo.list_runs():
        if run["status"] != "COMPLETED":
            continue
        professor_data = repo.load_latest(run["id"], "professor_profile")
        match_data = repo.load_latest(run["id"], "match")
        if not (professor_data and match_data):
            continue
        professor = ProfessorProfile(**professor_data)
        match = MatchReport(**match_data)
        rows.append(
            {
                "记录": run.get("name") or "—",
                "导师": professor.name.value or professor.professor_id,
                "研究匹配": match.research_fit.value,
                "建议": match.recommendation.value,
                "证据充分度": match.evidence_sufficiency,
                "强项": "、".join(d.label for d in match.strengths) or "—",
            }
        )
    return rows


st.set_page_config(page_title="导师双选 AI 助手", layout="wide")
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; }
    h1 { color: #1f3a5f; }
    h2, h3 { color: #1f3a5f; border-bottom: 2px solid #e3e8f0; padding-bottom: 0.3rem; }
    [data-testid="stSidebar"] { background: #f7f9fc; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("导师双选 AI 助手")
st.caption("上传简历 → 检索并核验导师 → 生成证据可溯的匹配报告与个性化邮件")

for key, default in (
    ("student", None),
    ("student_fact_editor", []),
    ("parsed_text", ""),
    ("result", None),
    ("student_name", ""),
):
    if key not in st.session_state:
        st.session_state[key] = default
if "repo" not in st.session_state:
    _new_run()

with st.sidebar:
    st.header("📁 历史记录")
    runs = st.session_state.repo.list_runs()
    completed = [run for run in runs if run["status"] == "COMPLETED"]
    if not completed:
        st.caption("暂无已完成的记录（生成一次报告后出现）")
    for run in completed[:20]:
        label = run.get("name") or f"{run['id'][:8]} · {(run['created_at'] or '')[:16]}"
        if st.button(label, key=f"hist_{run['id']}"):
            view = _load_run_view(st.session_state.repo, run["id"])
            if view is not None:
                st.session_state.viewed_run = view
    st.divider()
    if st.button("📊 对比历史导师", use_container_width=True):
        st.session_state.show_compare = not st.session_state.get("show_compare", False)

if st.session_state.get("viewed_run") is not None:
    st.header("📁 历史报告")
    st.caption(f"运行 ID：{st.session_state.viewed_run.run_id}")
    _render_report(st.session_state.viewed_run)
    st.subheader("邮件草稿")
    _render_draft(st.session_state.viewed_run)
    if st.button("关闭历史报告"):
        st.session_state.pop("viewed_run", None)
        st.rerun()
    st.divider()

if st.session_state.get("show_compare"):
    st.header("📊 历史导师对比")
    st.caption("已生成报告的历史导师横向对比；再次点击侧栏「📊 对比历史导师」可收起。")
    rows = _compare_runs(st.session_state.repo)
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
    else:
        st.info("暂无可对比的历史记录（先生成至少一次报告）。")
    if st.button("收起对比"):
        st.session_state.show_compare = False
        st.rerun()
    st.divider()


st.header("① 上传并确认简历")
uploaded = st.file_uploader("上传 CV（PDF，仅在本机解析）", type=["pdf"])
if st.button("解析 CV", type="primary", disabled=uploaded is None):
    upload_path = settings.uploads_dir / f"{st.session_state.run_id}.pdf"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(uploaded.getvalue())
    st.session_state["cv_path"] = str(upload_path)
    parsed = extract_pdf_text(upload_path)
    student = _build_student_profile(upload_path, parsed)
    st.session_state.student = student
    st.session_state.student_fact_editor = _fact_rows(student)
    st.session_state.parsed_text = parsed.text
    if student.name:
        st.session_state["student_name"] = student.name
    if parsed.warnings:
        st.warning("；".join(parsed.warnings))
student_name = st.text_input(
    "你的姓名（用于邮件落款，已自动从简历填入，请核对修改）", key="student_name"
)

if st.session_state.parsed_text:
    with st.expander("查看 PDF 提取文字"):
        st.text(st.session_state.parsed_text)


st.header("② 编辑并确认学生事实")
student = st.session_state.student
edited_student = None
confirmed_fact_ids: set[str] = set()
if student is None:
    st.info("请先上传并解析简历。解析失败时，可在后续版本中完全手动填写。")
else:
    st.caption("已默认勾选简历中解析出的全部事实，取消勾选你不想使用的项即可。")
    col_all, col_none, _ = st.columns([0.1, 0.12, 0.78], gap="small")
    if col_all.button("全选"):
        _set_all_facts(True)
    if col_none.button("全不选"):
        _set_all_facts(False)
    h1, h2, h3, h4 = st.columns([0.08, 0.16, 0.68, 0.08], gap="small")
    h1.caption("确认")
    h2.caption("类型")
    h3.caption("内容")
    h4.caption("删")
    for index, row in enumerate(st.session_state.student_fact_editor):
        row_id = row.get("id") or f"row_{index}"
        c1, c2, c3, c4 = st.columns([0.08, 0.16, 0.68, 0.08], gap="small")
        with c1:
            row["confirmed"] = st.checkbox(
                "确认", value=bool(row.get("confirmed")), key=f"fact_conf_{row_id}",
                label_visibility="collapsed",
            )
        with c2:
            field = row.get("field") if row.get("field") in FACT_FIELDS else "skill"
            row["field"] = st.selectbox(
                "类型", FACT_FIELDS, index=FACT_FIELDS.index(field), key=f"fact_field_{row_id}",
                label_visibility="collapsed",
            )
        with c3:
            row["value"] = st.text_input(
                "内容", value=str(row.get("value") or ""), key=f"fact_value_{row_id}",
                label_visibility="collapsed",
            )
        with c4:
            st.button(
                "✕",
                key=f"fact_del_{row_id}",
                on_click=_remove_fact,
                args=(row_id,),
                help="删除此行",
            )

    st.button("＋ 添加一行", on_click=_add_fact)
    edited_student = apply_fact_edits(student, st.session_state.student_fact_editor)
    confirmed_fact_ids = edited_student.confirmed_fact_ids()
    if not confirmed_fact_ids:
        st.warning("请至少确认一项真实的学生事实。")


st.header("③ 导师资料与已核实论文")
st.caption("粘贴主页链接自动解析，或手动填写；随后用 Agent 检索并逐条确认候选论文。")

for _key in (
    "prof_name",
    "prof_institution",
    "prof_department",
    "prof_title",
    "prof_email",
    "prof_interests",
    "prof_homepage",
    "prof_english_name",
):
    st.session_state.setdefault(_key, "")

col_new, col_parse = st.columns([0.32, 0.68])
with col_new:
    if st.button("🆕 开始新导师", use_container_width=True):
        _reset_professor()
        st.rerun()
with col_parse:
    homepage_url = st.text_input(
        "导师主页链接（可选，用于自动解析）",
        key="prof_homepage",
        label_visibility="collapsed",
        placeholder="粘贴导师主页或学校个人主页链接",
    )
if st.button("🔍 解析主页并自动检索"):
    url = homepage_url.strip()
    if not url:
        st.error("请先填写主页链接")
    else:
        try:
            with st.spinner("正在解析主页…"):
                profile = extract_homepage_profile(url, _llm())
            if profile.name:
                st.session_state["prof_name"] = profile.name
            if profile.institution:
                st.session_state["prof_institution"] = profile.institution
            if profile.department:
                st.session_state["prof_department"] = profile.department
            if profile.title:
                st.session_state["prof_title"] = profile.title
            if profile.email:
                st.session_state["prof_email"] = profile.email
            if profile.declared_interests:
                st.session_state["prof_interests"] = "、".join(profile.declared_interests)
            if profile.name:
                st.session_state["_research_steps"] = None
                st.session_state["_research_granted"] = []
                st.session_state["_auto_search"] = True
            st.success("已解析主页，字段已填入下方表格，请核对后继续。")
        except Exception as exc:  # noqa: BLE001 - 解析失败降级到手动录入
            st.error(f"主页解析失败（不影响手动录入）：{exc}")

left, right = st.columns(2)
with left:
    professor_name = st.text_input("导师姓名（必填）", key="prof_name")
    institution = st.text_input("学校/单位（必填）", key="prof_institution")
    department = st.text_input("院系（可选）", key="prof_department")
    professor_title = st.text_input("职称（可选）", key="prof_title")
with right:
    professor_email = st.text_input("导师邮箱（可选）", key="prof_email")
    declared_interests_text = st.text_area(
        "官网公开研究方向（可选，用逗号或分号分隔）", key="prof_interests"
    )
identity_confirmed = st.checkbox("我已核对并确认以上信息属于目标导师")
paper_read_confirmed = st.checkbox("我已阅读以上论文（可选，允许邮件提及）")

st.markdown("### 🔎 智能检索候选论文")
st.caption("Agent 先按「姓名 + 学校」查万方，再逐篇消歧；结果须你勾选确认归属。")
selected_mode = st.radio(
    "检索方式",
    ["auto", "zh", "en"],
    format_func=lambda k: {"auto": "自动（推荐）", "zh": "仅中文", "en": "仅英文"}[k],
    horizontal=True,
    key="search_mode",
)
english_name = st.text_input("导师英文名（英文检索时使用，可选）", key="prof_english_name")
search_name = english_name.strip() if selected_mode == "en" else professor_name.strip()

if "candidate_papers" not in st.session_state:
    st.session_state.candidate_papers = []


def _trigger_search() -> None:
    if not settings.wanfang_app_key:
        st.error("请先在 .env 里配置 WANFANG_APP_KEY（万方数据开放平台申请）")
        return
    if selected_mode == "en" and not english_name.strip():
        st.error("仅英文检索需要填写「导师英文名」")
        return
    if not search_name:
        st.error("请先填写导师姓名")
        return
    try:
        with st.spinner("Agent 正在检索与消歧…"):
            _run_research(search_name, institution.strip(), selected_mode, english_name.strip())
    except Exception as exc:  # noqa: BLE001 - 检索失败降级到手动录入
        st.error(f"检索失败（不影响手动录入）：{exc}")
        st.session_state.candidate_papers = []


if st.button("🔎 一键研究（Agent）", type="primary"):
    _trigger_search()

if st.session_state.get("_auto_search"):
    st.session_state["_auto_search"] = False
    _trigger_search()

if st.session_state.get("_research_confirm"):
    msg = st.session_state["_research_confirm"]
    st.warning(f"Agent 请求确认：{msg}")
    papers = st.session_state.candidate_papers
    insts = _candidate_institutions()
    st.caption("请选择处理方式：")
    c1, c2, c3 = st.columns(3)
    if c1.button("✅ 确认并继续", use_container_width=True):
        st.session_state["_research_granted"] = [msg]
        _trigger_search()
    if c2.button("🔁 去掉学校重新检索", use_container_width=True):
        st.session_state.pop("_research_confirm", None)
        st.session_state["_research_steps"] = None
        st.session_state["_research_granted"] = []
        _run_research(search_name, "", selected_mode, english_name.strip())
    if c3.button("📋 全部保留，我手动核对", use_container_width=True):
        for paper in papers:
            paper["belongs"] = True
            paper["needs_review"] = False
        st.session_state.pop("_research_confirm", None)
        st.session_state["_research_steps"] = None
        st.session_state["_research_granted"] = []
    if insts:
        st.caption("或按机构保留（应对导师刚调动单位的情况）：")
        icols = st.columns(len(insts[:4]))
        for idx, inst in enumerate(insts[:4]):
            if icols[idx].button(f"仅保留「{inst}」", use_container_width=True):
                for paper in papers:
                    keep = (paper.get("institution") or "").strip() == inst
                    paper["belongs"] = keep
                    paper["needs_review"] = False
                    if not keep:
                        paper["user_confirmed"] = False
                st.session_state.pop("_research_confirm", None)
                st.session_state["_research_steps"] = None
                st.session_state["_research_granted"] = []

papers = st.session_state.candidate_papers
paper_values: list[dict] = []
if papers:
    normal = [
        i for i, paper in enumerate(papers)
        if paper.get("belongs", True) and not paper.get("needs_review", False)
    ]
    review = [
        i for i, paper in enumerate(papers)
        if paper.get("belongs", True) and paper.get("needs_review", False)
    ]
    homonym = [i for i, paper in enumerate(papers) if not paper.get("belongs", True)]
    st.caption(
        f"共 {len(papers)} 篇候选：{len(normal)} 篇匹配 · "
        f"{len(review)} 篇需审核 · {len(homonym)} 篇疑似同名。"
    )
    col_all, col_none, _ = st.columns([0.15, 0.15, 0.7])
    if col_all.button("全选匹配项", use_container_width=True):
        _set_all_candidates(True, matching_only=True)
    if col_none.button("全不选", use_container_width=True):
        _set_all_candidates(False)

    def _render_candidate(index: int, cand: dict) -> None:
        label = f"{cand['title']}（{cand['year'] or '年份未知'}）"
        if cand.get("venue"):
            label += f" · {cand['venue']}"
        if cand.get("institution"):
            label += f" · {cand['institution']}"
        if cand.get("authors"):
            label += f"〔{'、'.join(cand['authors'])}〕"
        cand["user_confirmed"] = st.checkbox(label, key=f"cand_paper_{index}")
        if cand.get("source_url"):
            st.markdown(f"　🔗 [查看原文]({cand['source_url']})")

    for i in normal:
        _render_candidate(i, papers[i])
    if review:
        with st.expander(f"⚠ 机构不符需审核（{len(review)} 篇，可能是导师曾任职单位）"):
            for i in review:
                _render_candidate(i, papers[i])
                if papers[i].get("disambig_reason"):
                    st.caption(papers[i]["disambig_reason"])
    if homonym:
        with st.expander(f"🚫 疑似同名（{len(homonym)} 篇，Agent 已排除，可手动加回）"):
            for i in homonym:
                _render_candidate(i, papers[i])
                if papers[i].get("disambig_reason"):
                    st.caption(papers[i]["disambig_reason"])
    paper_values.extend([cand for cand in papers if cand["user_confirmed"]])

with st.expander("✍️ 手动补录论文（检索不到时使用）"):
    paper_count = int(st.number_input("补录论文数量", min_value=1, max_value=10, value=1))
    for index in range(paper_count):
        number = index + 1
        with st.expander(f"补录论文 {number}", expanded=number == 1):
            paper_title = st.text_input("论文标题（必填）", key=f"paper_title_{index}")
            paper_year_text = st.text_input("发表年份（可选）", key=f"paper_year_{index}")
            paper_abstract = st.text_area("论文摘要（必填）", key=f"paper_abstract_{index}")
            paper_url = st.text_input("来源链接（必填）", key=f"paper_url_{index}")
            paper_platform = st.selectbox(
                "来源平台",
                ["DOI/出版社", "知网", "万方", "Google Scholar", "学校页面", "其他"],
                key=f"paper_platform_{index}",
            )
            paper_keywords = st.text_input(
                "关键词/研究主题（可选，用逗号或分号分隔）",
                key=f"paper_keywords_{index}",
            )
            paper_confirmed = st.checkbox(
                "我已确认这篇论文属于该导师", key=f"paper_confirmed_{index}"
            )
            paper_values.append(
                {
                    "title": paper_title,
                    "year": int(paper_year_text) if paper_year_text.strip().isdigit() else None,
                    "abstract": paper_abstract,
                    "source_url": paper_url,
                    "source_platform": paper_platform,
                    "keywords": _split_terms(paper_keywords),
                    "user_confirmed": paper_confirmed,
                }
            )

run_label = st.text_input("本次记录名称（留空则用「导师名 · 单位」）", key="run_label")

can_generate = edited_student is not None and bool(confirmed_fact_ids)
if st.button("生成报告与邮件草稿", type="primary", disabled=not can_generate):
    if not professor_name.strip() or not institution.strip():
        st.error("请填写「导师姓名」和「学校/单位」")
        st.stop()
    if not identity_confirmed:
        st.error("请勾选「我已核对并确认以上信息属于目标导师」")
        st.stop()
    confirmed_papers = [values for values in paper_values if values["user_confirmed"]]
    if not confirmed_papers:
        st.error("请至少勾选确认一篇论文")
        st.stop()
    missing = validate_paper_values(confirmed_papers)
    if missing:
        st.error("；".join(missing))
        st.stop()
    try:
        professor_input = ManualProfessorInput(
            name=professor_name,
            institution=institution,
            department=department or None,
            title=professor_title or None,
            homepage_url=homepage_url or None,
            email=professor_email or None,
            declared_interests=_split_terms(declared_interests_text),
            identity_confirmed=identity_confirmed,
            papers=[
                ManualPaperInput(
                    **{
                        k: v
                        for k, v in values.items()
                        if k not in ("authors", "institution", "venue")
                    }
                )
                for values in confirmed_papers
            ],
        )
        with st.spinner("正在整理证据并生成报告…"):
            student_for_pipeline = edited_student.model_copy(
                update={"name": student_name.strip() or None}
            )
            st.session_state.result = run_manual_pipeline(
                student=student_for_pipeline,
                confirmed_fact_ids=confirmed_fact_ids,
                professor_input=professor_input,
                llm=_llm(),
                repository=st.session_state.repo,
                run_id=st.session_state.run_id,
                paper_read_confirmed=paper_read_confirmed,
            )
        name = run_label.strip() or f"{professor_name.strip()} · {institution.strip()}"
        st.session_state.repo.set_run_name(st.session_state.run_id, name)
    except (ValidationError, ValueError) as exc:
        st.error(f"请检查输入：{exc}")
    except Exception as exc:  # noqa: BLE001 - UI 必须显示可操作错误
        st.error(f"生成失败：{exc}")


result = st.session_state.result
if result is not None:
    st.header("④ 匹配报告")
    _render_report(result)
    st.header("⑤ 邮件、导出与删除")
    _render_draft(result)
    st.download_button("下载 Markdown", export_markdown(result), file_name="advisor-report.md")
    st.download_button("下载 JSON", export_json(result), file_name="advisor-report.json")
    st.download_button(
        "下载 Word",
        export_docx(result),
        file_name="advisor-report.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    if st.button("🗑 清空全部数据", type="secondary"):
        _reset_all()
        st.rerun()
