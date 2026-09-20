"""导师双选 AI 助手 v0.1：人工证据输入版 Streamlit 应用。

流程：CV 本地解析与人工确认 → 手动导师资料 → 手动录入已核实论文
→ 可解释匹配 → 事实锁定邮件 → 导出与完整删除。
"""

from __future__ import annotations

import uuid
from html import escape
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from advisor_fit.agents.research import research_professor
from advisor_fit.analysis.direction_search import (
    completeness_text,
    rank_candidates,
    split_terms,
)
from advisor_fit.analysis.identity import STATUS_LABELS, assess_identity
from advisor_fit.config import settings
from advisor_fit.export.report import export_docx, export_json, export_markdown
from advisor_fit.harness.budget import BudgetTracker
from advisor_fit.harness.trace import RunTrace
from advisor_fit.ingest.cv import (
    apply_fact_edits,
    build_student_profile,
    delete_uploaded_cv,
    extract_pdf_markdown,
    extract_pdf_text,
)
from advisor_fit.ingest.cv_llm import build_student_profile_llm
from advisor_fit.ingest.fetch import Fetcher
from advisor_fit.ingest.homepage import html_to_text, parse_homepage_html
from advisor_fit.ingest.manual_professor import (
    ManualPaperInput,
    ManualProfessorInput,
    validate_paper_values,
)
from advisor_fit.ingest.profile_fallback import REQUIRED_FIELDS, build_field_report
from advisor_fit.llm.provider import NullLLM, build_llm
from advisor_fit.manual_pipeline import run_manual_pipeline
from advisor_fit.providers.disciplines import DISCIPLINE_LABELS
from advisor_fit.providers.router import SearchRouter
from advisor_fit.storage.advisor_repo import AdvisorRepository
from advisor_fit.storage.cache import PageCache
from advisor_fit.storage.repository import Repository
from advisor_fit.storage.roster_repo import RosterRepository
from advisor_fit.ui_state import forget_widgets, remember_widgets, restore_widgets
from advisor_fit.ui_theme import CSS

_PERSISTED_WIDGETS = (
    "student_name", "prof_name", "prof_institution", "prof_department", "prof_title",
    "prof_email", "prof_interests", "prof_homepage", "prof_english_name",
    "prof_search_institution", "prof_seed_titles", "search_mode", "run_label",
    "identity_confirmed", "paper_read_confirmed", "manual_paper_count",
    "draft_subject", "draft_body",
    *(f"paper_{field}_{index}" for index in range(10)
      for field in ("title", "year", "abstract", "url", "platform", "keywords", "confirmed")),
)

# 导师级状态：换一位导师（「开始新导师」）时清空，学生简历与历史记录保留。
_PROFESSOR_STATE_KEYS = (
    "prof_name", "prof_institution", "prof_department", "prof_title", "prof_email",
    "prof_interests", "prof_homepage", "prof_english_name", "prof_search_institution",
    "prof_seed_titles", "_faculty_directions", "_faculty_seed_titles",
    "candidate_papers", "_research_confirm", "_research_steps", "_research_granted",
    "_research_degraded", "field_report", "identity_result", "homepage_profile",
    "result", "identity_confirmed", "paper_read_confirmed", "run_label",
    "manual_paper_count",
)

# 会话级状态：清空全部数据时在学生简历之外额外要清掉的键。
_SESSION_STATE_KEYS = (
    "student", "student_fact_editor", "parsed_text", "student_name", "cv_path",
    "session_run_ids", "viewed_run", "show_compare", "ui_saved_widgets",
)


def _clear_candidate_widgets() -> None:
    """清掉候选论文勾选框的 widget 状态（键名带动态下标，无法预先枚举）。"""
    for key in list(st.session_state):
        if key.startswith("cand_paper_"):
            st.session_state.pop(key, None)


def _navigate(page: str) -> None:
    remember_widgets(st.session_state, _PERSISTED_WIDGETS)
    st.session_state.active_page = page


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
    forget_widgets(
        st.session_state,
        tuple(key for key in _PERSISTED_WIDGETS if key != "student_name"),
    )
    for key in _PROFESSOR_STATE_KEYS:
        st.session_state.pop(key, None)
    for key in _PERSISTED_WIDGETS:
        if key != "student_name":
            st.session_state.pop(key, None)
    _clear_candidate_widgets()


def _reset_professor() -> None:
    """开始新导师：清空导师区并开一条新记录，保留学生简历与历史。"""
    _clear_professor_state()
    _new_run()
    st.session_state.active_page = "professor"


def _reset_all() -> None:
    """清空本次会话全部数据：删除本次会话创建的所有记录与上传的 CV。"""
    repo = st.session_state.get("repo")
    for run_id in list(st.session_state.get("session_run_ids", [])):
        if repo is not None:
            try:
                repo.delete_run(run_id)
            except ValueError:
                pass
        try:
            # 只删除与精确 UUID 对应的上传 PDF
            delete_uploaded_cv(settings.uploads_dir, run_id)
        except (ValueError, OSError):
            pass
    cv_path = st.session_state.get("cv_path")
    if cv_path:
        try:
            Path(cv_path).unlink(missing_ok=True)
        except OSError:
            pass
    for key in (*_SESSION_STATE_KEYS, *_PROFESSOR_STATE_KEYS):
        st.session_state.pop(key, None)
    for key in _PERSISTED_WIDGETS:
        st.session_state.pop(key, None)
    _clear_candidate_widgets()
    _new_run()
    st.session_state.active_page = "home"


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


def _run_research(
    name: str,
    institution: str,
    mode: str,
    english_name: str,
    search_institution: str = "",
    seed_titles: list[str] | None = None,
    known_directions: list[str] | None = None,
    discipline: str = "",
) -> None:
    """用导师研究 Agent 检索并消歧，结果与确认门控写入 session_state。"""
    budget = BudgetTracker()
    provider = SearchRouter(
        key_values={
            "wanfang_app_key": settings.wanfang_app_key,
            "aminer_api_key": settings.aminer_api_key,
        },
        budget=budget,
        contact_email=settings.contact_email,
    )
    source = mode if mode in ("zh", "en") else "auto"
    trace = RunTrace(task=f"检索导师「{name}」的候选论文")
    result = research_professor(
        _llm(),
        provider,
        name=name,
        institution=institution or None,
        english_name=english_name or None,
        source=source,
        discipline=discipline or None,
        search_institution=search_institution or None,
        seed_titles=seed_titles or None,
        known_directions=known_directions or None,
        resume_steps=st.session_state.get("_research_steps"),
        granted_confirmations=st.session_state.get("_research_granted", []),
        budget=budget,
        trace=trace,
    )
    st.session_state["_research_degraded"] = trace.degraded_reason
    st.session_state["_research_sources"] = provider.describe()
    st.session_state["_research_discipline"] = provider.discipline_text()
    run_id = st.session_state.get("run_id")
    if run_id:
        try:
            # 轨迹落库失败不得影响检索结果
            st.session_state.repo.save_trace(run_id, trace)
        except Exception:  # noqa: BLE001
            pass
    if result.needs_confirmation:
        st.session_state["_research_confirm"] = result.needs_confirmation
        st.session_state["_research_steps"] = result.log
        st.session_state.candidate_papers = result.papers
    else:
        st.session_state.pop("_research_confirm", None)
        st.session_state["_research_steps"] = None
        st.session_state["_research_granted"] = []
        st.session_state.candidate_papers = result.papers


def _faculty_repo():
    from advisor_fit.storage.faculty_repo import FacultyRepository

    return FacultyRepository(settings.data_dir / "faculty.db")


def _advisor_repo() -> AdvisorRepository:
    """统一导师库（3 万条，全项目唯一的一份导师数据）。"""
    return AdvisorRepository(settings.data_dir / "advisors.db")


def _advisor_as_faculty(item):
    """把统一导师库的一条记录，适配成旧的导师库记录形状（复用已有的填充逻辑）。"""
    from advisor_fit.models.faculty import FacultyRecord

    return FacultyRecord(
        id=f"{item.university}|{item.department}|{item.name}",
        name=item.name,
        university=item.university,
        college=item.department,
        department=item.department,
        title=item.title,
        homepage_url=item.homepage_url,
        email=item.email,
        research_areas=item.research_areas,
        research_directions=item.research_directions,
        publications=item.publications,
        profile_text=item.profile_text,
        source_url=item.source_url,
        retrieved_at=item.retrieved_at,
    )


def _lookup_faculty(name: str, institution: str) -> list:
    """按姓名+学校查本地导师库：先查统一库（3 万条），没有再退回早期采集库。"""
    if not (name or "").strip():
        return []
    try:
        advisors = _advisor_repo().lookup(name, institution or None)
    except Exception:  # noqa: BLE001 - 导师库缺失/损坏不影响检索
        advisors = []
    if advisors:
        return [_advisor_as_faculty(item) for item in advisors]
    try:
        return _faculty_repo().lookup(name, institution or None)
    except Exception:  # noqa: BLE001
        return []


def _fill_professor_from_advisor(item) -> None:
    """把导师库记录填进「Ⅱ 导师档案」的各个字段（用户仍可修改）。"""
    st.session_state["prof_name"] = item.name
    st.session_state["prof_institution"] = item.university
    st.session_state["prof_department"] = item.department
    st.session_state["prof_title"] = item.title
    st.session_state["prof_email"] = item.email
    if item.homepage_url:
        st.session_state["prof_homepage"] = item.homepage_url
    directions = item.research_directions or item.research_areas
    if directions:
        st.session_state["prof_interests"] = "、".join(directions)
    if item.publications:
        st.session_state["prof_seed_titles"] = "\n".join(item.publications)
    st.session_state["_faculty_directions"] = directions
    st.session_state["_faculty_seed_titles"] = item.publications


def _manual_professor_fields() -> dict[str, str]:
    """把用户在导师页已经填好的字段收集起来，作为"补齐"的最高优先级来源。"""
    return {
        "name": st.session_state.get("prof_name", ""),
        "institution": st.session_state.get("prof_institution", ""),
        "department": st.session_state.get("prof_department", ""),
        "title": st.session_state.get("prof_title", ""),
        "email": st.session_state.get("prof_email", ""),
        "declared_interests": st.session_state.get("prof_interests", ""),
    }


_FIELD_STATUS_BADGES = {
    "CONFIRMED": "✅ 已确认",
    "INFERRED": "🟡 待核对",
    "UNKNOWN": "⚪ 未知",
}

_IDENTITY_MARKS = {True: "✅", False: "🔴", None: "⚪"}


def _page_cache() -> PageCache:
    """网页缓存的存放位置（同一链接在保质期内不再重复抓取）。"""
    return PageCache(settings.data_dir / "page_cache.db")


def _refresh_professor_review(manual: dict[str, str] | None = None) -> None:
    """重算「字段补齐情况」与「身份核对」，结果存进 session_state。

    manual 用于字段来源的归属：解析主页后要传入"解析前用户已填的内容"，
    否则官网抓来的值会被误标成"你手动填写"。
    """
    profile = st.session_state.get("homepage_profile")
    homepage_html = st.session_state.get("homepage_html", "")
    sources = dict(manual) if manual is not None else _manual_professor_fields()
    current = _manual_professor_fields()
    st.session_state["field_report"] = build_field_report(
        manual=sources,
        homepage_html=homepage_html,
        page_text=html_to_text(homepage_html) if homepage_html else "",
        llm_profile=profile,
        source_url=st.session_state.get("prof_homepage") or None,
    )
    st.session_state["identity_result"] = assess_identity(
        name=current["name"],
        institution=current["institution"],
        email=current["email"],
        department=current["department"],
        homepage_profile=profile,
        known_directions=_split_terms(current["declared_interests"]),
    )


def _render_identity_panel(result) -> None:
    """把身份核对的每条线索摊开：支持的打勾，对不上的标红并提醒核对。"""
    with st.container(border=True):
        st.markdown('<div class="section-note">IDENTITY CHECK / 导师身份核对</div>',
                    unsafe_allow_html=True)
        label = STATUS_LABELS.get(result.status.value, result.status.value)
        st.write(f"**{label}**　·　支持 {result.score} 项线索")
        for signal in result.signals:
            mark = _IDENTITY_MARKS.get(signal.matched, "⚪")
            st.markdown(f"{mark} **{signal.label}**　{signal.detail}")
        if not result.signals:
            st.caption("还没有可用于核对的线索（邮箱、机构、论文等）。先补主页或邮箱会更准。")
        if result.conflicts:
            st.warning("以下线索对不上，请核对是不是同名他人：" + "；".join(result.conflicts))


def _render_roster_picker() -> None:
    """从本地名册里挑一位导师（只含学校 / 学院 / 姓名，不含任何评价内容）。"""
    try:
        repo = RosterRepository(settings.data_dir / "supervisor_roster.db")
        universities = repo.universities()
    except Exception:  # noqa: BLE001 - 名册缺失或损坏不影响手动录入
        return
    if not universities:
        return

    with st.expander("📇 从导师名册里挑一位（只含学校 / 学院 / 姓名）"):
        st.caption(
            f"名册共收录 {repo.count()} 条记录。它只回答「这个学院有哪些导师」，"
            "不含评价内容，也不能替代官网核实。"
        )
        current = st.session_state.get("prof_institution", "")
        index = universities.index(current) if current in universities else 0
        university = st.selectbox("学校", universities, index=index, key="roster_university")
        departments = repo.departments(university)
        department = ""
        if departments:
            department = st.selectbox("学院", departments, key="roster_department")
        names = repo.lookup(university, department or None)
        if not names:
            st.caption("该学院暂无名册记录。")
            return
        name = st.selectbox("导师姓名", names, key="roster_supervisor")
        if st.button("填入导师信息", key="roster_fill"):
            st.session_state["prof_name"] = name
            st.session_state["prof_institution"] = university
            if department:
                st.session_state["prof_department"] = department
            st.success(f"已填入「{name} · {university} · {department}」。主页链接仍需你自己提供。")


def _render_field_report(report) -> None:
    """把「哪些字段拿到了、从哪来、还缺什么」摊开给用户看；缺字段不阻塞流程。"""
    with st.container(border=True):
        st.markdown('<div class="section-note">FIELD STATUS / 导师信息补齐情况</div>',
                    unsafe_allow_html=True)
        st.caption(report.summary())
        for entry in report.fields.values():
            badge = _FIELD_STATUS_BADGES.get(entry.status.value, entry.status.value)
            if entry.known:
                st.markdown(
                    f"**{entry.label}**　{entry.value}　·　{badge}　·　来源：{entry.source}"
                )
                if entry.note:
                    st.caption(entry.note)
            else:
                st.markdown(f"**{entry.label}**　—　·　{badge}　·　请手动补充")
        for note in report.notes:
            st.caption(note)
        missing = report.missing_required(REQUIRED_FIELDS)
        if missing:
            labels = "、".join(report.get(name).label for name in missing)
            st.warning(f"还缺必填项：{labels}（补齐后才能继续下一步）")
        else:
            st.caption("必填项已齐，可以继续；其余字段留空不影响后续流程。")
        if report.source_url:
            st.caption(f"抓取来源：{report.source_url}")


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


def _render_brief(result) -> None:
    """Readable research note with a parallel, traceable source index."""
    professor = result.professor
    report = result.match_report
    deep = getattr(result, "deep_analysis", None)
    direction = getattr(result, "direction_summary", None)
    main, rail = st.columns([2.5, 1], gap="large")
    with main:
        with st.container(border=True):
            st.markdown('<div class="folio">ADVISOR FIT / RESEARCH NOTE　·　'
                        '已确认事实与论文</div>', unsafe_allow_html=True)
            st.header(f"{professor.name.value or '目标导师'} · 研究匹配简报")
            st.write("这份判断只使用人工确认的学生事实与导师论文；它不是录取预测。")
            recommendation = escape(_badge(_REC_BADGES, report.recommendation.value))
            fit = escape(_badge(_FIT_BADGES, report.research_fit.value))
            st.markdown(
                f'<div class="brief-verdict"><strong>{recommendation}</strong>'
                f'<span>研究契合：{fit}</span></div>', unsafe_allow_html=True,
            )
            sufficiency = {"HIGH": "较充分", "MEDIUM": "中等", "LOW": "有限"}.get(
                report.evidence_sufficiency, "待核实"
            )
            opportunity = {"UNKNOWN": "未核实"}.get(
                report.opportunity_signal, report.opportunity_signal
            )
            st.caption(f"证据充分度：{sufficiency}　·　"
                       f"招生机会信号：{opportunity}（独立于研究契合）")

            st.subheader("01　有据可循的交集")
            if report.strengths:
                for item in report.strengths:
                    st.markdown(f"**{item.label}**　{item.summary}")
            else:
                st.write("目前缺少足够的已确认交集，不宜将兴趣相近写成能力匹配。")
            if direction is not None and direction.summary:
                st.markdown("**研究方向归纳**")
                st.write(direction.summary)

            st.subheader("02　尚需澄清的距离")
            for gap in report.gaps:
                st.write(f"· {gap}")
            if not report.gaps:
                st.write("暂无明确缺口；仍需核实近期课题与招生情况。")
            if deep is not None:
                for label, points in (
                    ("研究交集", deep.research_intersection),
                    ("方法能力匹配", deep.method_match),
                    ("背景缺口", deep.background_gaps),
                    ("最值得读的论文", deep.recommended_papers),
                    ("联系前应补的知识", deep.knowledge_to_supplement),
                ):
                    if points:
                        st.markdown(f"**{label}**")
                        for point in points:
                            st.write(f"· {point.text}")

            st.subheader("03　建议的联系角度")
            if report.questions_to_ask:
                for question in report.questions_to_ask:
                    st.write(f"· {question}")
            else:
                st.write("围绕已核实论文提出具体问题，并说明自己的真实工作。")
            st.caption("方法说明：未确认的候选论文与推断身份，不作为确定性结论。")

    with rail:
        with st.container(border=True):
            st.markdown('<div class="section-note">TRACEABLE SOURCES / 证据索引</div>',
                        unsafe_allow_html=True)
            st.subheader("证据来源")
            for number, evidence in enumerate(result.evidences, 1):
                st.markdown(f"**{number:02d}**　{evidence.title or evidence.id}")
                st.caption(f"{evidence.source_type}　{evidence.published_date or ''}")
                if evidence.source_url:
                    st.markdown(f"[查看来源 ↗]({evidence.source_url})")
                st.divider()
            if not result.evidences:
                st.info("暂无可展示的来源。")
        with st.expander("导师画像与已核实论文"):
            for field in professor.iter_asserted_fields():
                st.write(f"**{field.key}**：{field.value}")
            if professor.homepage:
                st.markdown(f"[查看官方主页]({professor.homepage})")
            if professor.declared_interests:
                st.write(
                    "官网公开方向："
                    + "、".join(t.topic for t in professor.declared_interests)
                )
            for publication in professor.recent_publications:
                label = f"{publication.title}（{publication.year or '年份未知'}）"
                if publication.source_url:
                    st.markdown(f"- [{label}]({publication.source_url})")
                else:
                    st.write(f"- {label}")


def _render_letter(result) -> None:
    """Editable evidence-bound draft; downloading never sends the email."""
    draft = result.draft
    main, rail = st.columns([2.3, 1], gap="large")
    with main:
        with st.container(border=True):
            st.markdown('<div class="section-note">PERSONALIZED DRAFT / 可编辑草稿</div>',
                        unsafe_allow_html=True)
            st.caption("收件人邮箱、称谓与招生情况请以官方渠道为准。")
            subject = st.text_input("邮件主题", value=draft.subject or "", key="draft_subject")
            body = "\n".join(sentence.text for sentence in draft.sentences)
            edited_body = st.text_area("邮件正文（可直接编辑）", value=body, height=380,
                                       key="draft_body")
            st.download_button("下载邮件文本", f"主题：{subject}\n\n{edited_body}".encode(),
                               file_name="联系邮件.txt", mime="text/plain")
            with st.expander("复制完整邮件文本"):
                st.code(f"主题：{subject}\n\n{edited_body}", language=None)
    with rail:
        with st.container(border=True):
            st.subheader("发送前核对")
            st.write("01　核实论文与导师近期方向")
            st.write("02　只陈述确实完成过的工作")
            st.write("03　检查邮箱、称谓与附件")
            st.write("04　由你人工发送，系统不会自动发送")
            if draft.warnings:
                st.info("；".join(draft.warnings))
            if not result.draft_validation.ok:
                st.warning("草稿仍有未通过校验的句子，请先修改。")


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
                "run_id": run["id"],
                "记录": run.get("name") or "—",
                "导师": professor.name.value or professor.professor_id,
                "研究匹配": match.research_fit.value,
                "建议": match.recommendation.value,
                "证据充分度": match.evidence_sufficiency,
                "强项": "、".join(d.label for d in match.strengths) or "—",
            }
        )
    return rows


st.set_page_config(page_title="导师双选 AI 助手", layout="wide", page_icon="🎓")
st.markdown(CSS, unsafe_allow_html=True)

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
remember_widgets(st.session_state, _PERSISTED_WIDGETS)
restore_widgets(st.session_state, _PERSISTED_WIDGETS)
st.session_state.setdefault("active_page", "home")

with st.sidebar:
    st.markdown('<div class="studio-brand"><span class="brand-mark">◎</span>择研'
                '<small>ADVISOR FIT STUDIO</small></div>', unsafe_allow_html=True)
    st.markdown('<div class="side-label">WORKSPACE / 工作台</div>', unsafe_allow_html=True)
    pages = (
        ("home", "✦  首屏 / 项目入口"),
        ("direction", "◌  方向找导师"),
        ("resume", "Ⅰ  学生事实"),
        ("professor", "Ⅱ  导师档案"),
        ("papers", "Ⅲ  论文核验"),
        ("report", "Ⅳ  匹配简报"),
        ("email", "Ⅴ  联系邮件"),
        ("history", "Ⅵ  研究档案"),
    )
    for page_key, label in pages:
        st.button(label, key=f"nav_{page_key}", on_click=_navigate, args=(page_key,))
    st.divider()
    st.markdown('<div class="side-label">CURRENT STUDY / 当前研究</div>',
                unsafe_allow_html=True)
    st.caption((st.session_state.get("prof_name") or "尚未选择导师") + " · "
               + (st.session_state.get("prof_institution") or "本地研究空间"))
    st.markdown('<div class="side-note"><b>◌ 以证据，而非印象作判断</b><br>'
                '学生事实与导师论文须经人工确认，才进入匹配简报与邮件草稿。</div>',
                unsafe_allow_html=True)

active_page = st.session_state.active_page
if active_page not in {page_key for page_key, _ in pages}:
    active_page = "home"
    st.session_state.active_page = active_page
st.markdown(
    f'<style>[data-testid="stSidebar"] .st-key-nav_{active_page} button '
    '{background:#34323d;border-color:#49434f;color:#fff}</style>',
    unsafe_allow_html=True,
)
if active_page == "home":
    st.markdown(
        '<div class="landing-hero"><div class="label">ADVISOR FIT · RESEARCH WITH EVIDENCE</div>'
        '<h1>找到契合的导师，<br><em>从理解开始。</em></h1>'
        '<p>从你的经历、导师的研究与可核验的论文出发，把模糊的「合不合适」'
        '梳理成清晰、有出处的判断。每一条进入报告的事实，都由你亲自确认。</p>'
        '<div class="orb">知 · 合</div></div>', unsafe_allow_html=True,
    )
    st.markdown('<div class="landing-path">一段有依据的研究旅程　'
                '<b>01</b>整理自身　<b>02</b>理解导师　<b>03</b>核实论文　'
                '<b>04</b>形成判断</div>', unsafe_allow_html=True)
    st.button("开始一项研究 →", type="primary", on_click=_navigate, args=("resume",))
    st.stop()

if active_page == "direction":
    st.markdown('<div class="page-eyebrow">00 / FIND BY DIRECTION</div>', unsafe_allow_html=True)
    st.title("先找方向，再找人。")
    st.markdown(
        '<p class="page-intro">用研究方向在本地导师库里粗筛候选，看清「为什么推荐他」，'
        '再挑人进入逐个深度研究。这一步不联网，也不会替你决定谁合适。</p>',
        unsafe_allow_html=True,
    )
    library_repo = _advisor_repo()
    try:
        library_total = library_repo.count()
    except Exception:  # noqa: BLE001 - 库损坏不该让页面崩掉
        library_total = 0

    if not library_total:
        st.info("本地导师库还是空的。")
        st.markdown(
            "建库方式（可选，需要联网、由你手动触发）：\n\n"
            "```powershell\n"
            ".\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\crawl_university.py"
            " --university 武汉大学\n"
            "```\n\n"
            "还没有库也不影响使用：可以在「Ⅱ 导师档案」里手动填写导师信息，"
            "或者用「📇 从导师名册里挑一位」按学校/学院/姓名选人。"
        )
    else:
        st.caption(
            f"本地导师库：{library_total} 位导师　·　"
            "数据来自官网采集与公开名册，请以你核对为准"
        )
        direction_query = st.text_input(
            "研究方向关键词（用逗号、顿号或分号分隔）",
            key="direction_query",
            placeholder="例如：知识图谱、数字人文、文化遗产",
        )
        try:
            all_universities = library_repo.universities()
        except Exception:  # noqa: BLE001
            all_universities = []
        direction_scope = st.multiselect(
            "限定学校（留空 = 全库）", all_universities, key="direction_scope"
        )

        if st.button("🔍 找候选导师", type="primary"):
            terms = split_terms(direction_query)
            if not terms:
                st.warning("请至少填一个研究方向关键词。")
            else:
                with st.spinner("在本地导师库里粗筛并排序…"):
                    try:
                        pool = library_repo.search_by_terms(
                            terms, universities=direction_scope or None
                        )
                        hits = rank_candidates(
                            pool,
                            terms,
                            universities=direction_scope or None,
                            limit=20,
                        )
                    except Exception as exc:  # noqa: BLE001
                        hits = []
                        st.error(f"检索本地导师库失败：{exc}")
                st.session_state["direction_hits"] = [
                    {
                        "row": hit.to_row(),
                        "advisor": hit.advisor.model_dump(),
                        "completeness_text": completeness_text(hit.advisor),
                    }
                    for hit in hits
                ]
                st.session_state["direction_terms"] = terms
                st.session_state.pop("direction_picked", None)

        hits = st.session_state.get("direction_hits") or []
        if hits:
            terms = st.session_state.get("direction_terms") or []
            st.markdown('<div class="section-note">CANDIDATES / 候选导师</div>',
                        unsafe_allow_html=True)
            st.caption(
                f"关键词：{'、'.join(terms)}　·　共 {len(hits)} 位候选，"
                "按「命中权重 → 资料完整度」排序；匹配理由就是推荐依据，请自行核对。"
            )
            st.dataframe(
                [
                    {
                        "姓名": hit["row"]["姓名"],
                        "学校": hit["row"]["学校"],
                        "院系": hit["row"]["院系"],
                        "职称": hit["row"]["职称"],
                        "研究方向": hit["row"]["研究方向"],
                        "匹配理由": hit["row"]["匹配理由"],
                        "资料完整度": hit["row"]["资料完整度"],
                    }
                    for hit in hits
                ],
                hide_index=True,
                use_container_width=True,
            )
            labels = {
                f"{index + 1:02d} · {hit['row']['姓名']} · {hit['row']['学校']}"
                f" · {hit['row']['院系'] or '院系未采集'}": index
                for index, hit in enumerate(hits)
            }
            picked = st.multiselect(
                "勾选要并排比较的候选（建议 2–5 位）", list(labels), key="direction_picked"
            )
            if picked:
                chosen = [hits[labels[label]] for label in picked][:5]
                st.markdown('<div class="section-note">SIDE BY SIDE / 并排比较</div>',
                            unsafe_allow_html=True)
                columns = st.columns(len(chosen), gap="large")
                for column, hit in zip(columns, chosen, strict=True):
                    with column, st.container(border=True):
                        st.subheader(hit["row"]["姓名"])
                        st.caption(
                            f"{hit['row']['学校']} · {hit['row']['院系'] or '院系未采集'}"
                        )
                        st.write("**职称**　" + hit["row"]["职称"])
                        st.write("**研究方向**　" + hit["row"]["研究方向"])
                        st.write("**匹配理由**　" + hit["row"]["匹配理由"])
                        st.write("**资料完整度**　" + hit["row"]["资料完整度"])
                        if hit["row"]["主页"] != "未采集":
                            st.markdown(f"[导师主页 ↗]({hit['row']['主页']})")
                        st.caption("资料来自：" + hit["row"]["来源"])
                        st.caption("字段核对：" + hit["completeness_text"])

            st.markdown('<div class="section-note">NEXT STEP / 下一步</div>',
                        unsafe_allow_html=True)
            st.caption(
                "挑一位进入「Ⅱ 导师档案」做深度研究（会去学术库检索并核对他的论文）；"
                "每位做完后都会存进「Ⅵ 研究档案」，可以在那里并排比较研究结果。"
            )
            target_label = st.selectbox(
                "选择要深入研究的导师", list(labels), key="direction_target"
            )
            if st.button("用这位导师开始研究 →", type="primary"):
                from advisor_fit.models.advisor import Advisor as _Advisor

                target = hits[labels[target_label]]
                _fill_professor_from_advisor(_Advisor(**target["advisor"]))
                _navigate("professor")
                st.rerun()
        elif "direction_hits" in st.session_state:
            st.warning("没有找到匹配的导师。可以换更通用的词（例如「机器学习」而不是具体课题），或把学校范围留空。")
    st.stop()

if active_page == "history":
    st.markdown('<div class="page-eyebrow">06 / RESEARCH ARCHIVE</div>', unsafe_allow_html=True)
    st.title("每次研究，都留下一条清晰的路径。")
    st.markdown('<p class="page-intro">按导师名与单位保存、回看和比较；删除时完整清除。</p>',
                unsafe_allow_html=True)
    runs = st.session_state.repo.list_runs()
    completed = [run for run in runs if run["status"] == "COMPLETED"]
    if not completed:
        st.info("暂无已完成的研究记录。生成一份报告后会出现在这里。")
    for run in completed[:20]:
        label = run.get("name") or f"{run['id'][:8]} · {(run['created_at'] or '')[:16]}"
        if st.button(label, key=f"hist_{run['id']}"):
            st.session_state.viewed_run = _load_run_view(st.session_state.repo, run["id"])
            st.session_state.show_compare = False
    if st.session_state.get("viewed_run") is not None:
        st.markdown('<div class="section-note">SELECTED RECORD / 所选记录</div>',
                    unsafe_allow_html=True)
        _render_brief(st.session_state.viewed_run)
        with st.expander("查看该记录的邮件草稿"):
            _render_letter(st.session_state.viewed_run)
        if st.button("关闭历史报告"):
            st.session_state.pop("viewed_run", None)
            st.rerun()
    if st.button("横向比较已完成记录"):
        st.session_state.show_compare = not st.session_state.get("show_compare", False)
    if st.session_state.get("show_compare"):
        rows = _compare_runs(st.session_state.repo)
        if rows:
            choices = {row["run_id"]: row for row in rows}
            selected_ids = st.multiselect(
                "并排阅读两位导师",
                list(choices),
                default=list(choices)[:2],
                max_selections=2,
                format_func=lambda run_id: choices[run_id]["记录"],
            )
            if len(selected_ids) == 2:
                left, right = st.columns(2, gap="large")
                for column, run_id in zip((left, right), selected_ids, strict=True):
                    with column, st.container(border=True):
                        row = choices[run_id]
                        st.subheader(row["记录"])
                        for field in ("导师", "研究匹配", "建议", "证据充分度", "强项"):
                            st.markdown(f"**{field}**　{row[field]}")
            st.markdown('<div class="section-note">ALL RECORDS / 完整记录</div>',
                        unsafe_allow_html=True)
            st.dataframe([{k: v for k, v in row.items() if k != "run_id"} for row in rows],
                         hide_index=True, use_container_width=True)
        else:
            st.info("尚无可比较的完整记录。")
    st.divider()
    confirm_clear = st.checkbox("我确认清空本次会话的数据与上传简历")
    if st.button("清空全部数据", disabled=not confirm_clear):
        _reset_all()
        st.rerun()
    st.stop()

if active_page == "report":
    st.markdown('<div class="page-eyebrow">04 / RESEARCH BRIEF</div>', unsafe_allow_html=True)
    st.title("把判断，写成可以回看的简报。")
    st.markdown('<p class="page-intro">结论、交集、缺口与证据来源并置；分数不替代判断。</p>',
                unsafe_allow_html=True)
    result = st.session_state.result
    if result is None:
        st.info("完成学生事实、导师资料及论文确认后，在论文页生成简报。")
    else:
        _render_brief(result)
        cols = st.columns(4)
        cols[0].download_button("下载 Markdown", export_markdown(result),
                                file_name="advisor-report.md")
        cols[1].download_button("下载 JSON", export_json(result), file_name="advisor-report.json")
        cols[2].download_button("下载 Word", export_docx(result),
                                file_name="advisor-report.docx",
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        cols[3].button("据此写联系邮件 →", on_click=_navigate, args=("email",))
    st.stop()

if active_page == "email":
    st.markdown('<div class="page-eyebrow">05 / OUTREACH DRAFT</div>', unsafe_allow_html=True)
    st.title("真诚的邮件，来自具体的理解。")
    st.markdown('<p class="page-intro">请亲自修改、核实称谓与邮箱；系统不会自动发送。</p>',
                unsafe_allow_html=True)
    if st.session_state.result is None:
        st.info("生成匹配简报后，才会有基于已确认事实的邮件草稿。")
    else:
        _render_letter(st.session_state.result)
    st.stop()

student = st.session_state.student
student_name = st.session_state.get("student_name", "")
edited_student = (
    apply_fact_edits(student, st.session_state.student_fact_editor) if student else None
)
confirmed_fact_ids = edited_student.confirmed_fact_ids() if edited_student else set()
professor_name = st.session_state.get("prof_name", "")
institution = st.session_state.get("prof_institution", "")
department = st.session_state.get("prof_department", "")
professor_title = st.session_state.get("prof_title", "")
professor_email = st.session_state.get("prof_email", "")
declared_interests_text = st.session_state.get("prof_interests", "")
homepage_url = st.session_state.get("prof_homepage", "")
identity_confirmed = st.session_state.get("identity_confirmed", False)
paper_read_confirmed = st.session_state.get("paper_read_confirmed", False)
selected_mode = st.session_state.get("search_mode", "auto")
english_name = st.session_state.get("prof_english_name", "")
search_institution = st.session_state.get("prof_search_institution", "")

_step_index = {"resume": 0, "professor": 1, "papers": 2}.get(active_page, 0)
_step_names = ("学生事实", "导师档案", "论文核验", "匹配简报")
_step_markup = '<div class="step-strip">' + ''.join(
    f'<span class="{"done" if i < _step_index else "current" if i == _step_index else ""}">'
    f'<b>0{i + 1}</b>{label}</span>' for i, label in enumerate(_step_names)
) + '</div>'


if active_page == "resume":
    st.markdown('<div class="page-eyebrow">01 / STUDENT PROFILE</div>', unsafe_allow_html=True)
    st.title("先确认，你是谁。")
    st.markdown('<p class="page-intro">从 PDF 简历提取事实；可编辑、增删与勾选。'
                '只有你确认的内容会进入匹配。</p>', unsafe_allow_html=True)
    st.markdown(_step_markup, unsafe_allow_html=True)
    resume_left, resume_right = st.columns([0.9, 1.1], gap="large")
    with resume_left:
        st.subheader("原始简历 / 本地解析")
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


    with resume_right:
        st.markdown('<div class="section-note">VERIFIED STUDENT FACTS / 可用于匹配的事实</div>',
                    unsafe_allow_html=True)
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
                        "类型", FACT_FIELDS, index=FACT_FIELDS.index(field),
                        key=f"fact_field_{row_id}",
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
        st.button("确认事实，继续 →", type="primary", on_click=_navigate, args=("professor",),
                  disabled=not bool(confirmed_fact_ids))



if active_page == "professor":
    st.markdown('<div class="page-eyebrow">02 / PROFESSOR PROFILE</div>', unsafe_allow_html=True)
    st.title("理解研究者，不止看关键词。")
    st.markdown('<p class="page-intro">主页资料与研究方向并置；自动提取只是建议，'
                '导师身份仍须由你核实。</p>', unsafe_allow_html=True)
    st.markdown(_step_markup, unsafe_allow_html=True)
    st.markdown('<div class="profile-band"><div class="label">PROFESSOR PROFILE / 人工核实中</div>'
                f'<h2>{escape(professor_name or "待确认导师")}</h2>'
                f'<p>{escape(institution or "学校 / 单位待填写")}　·　'
                f'{escape(department or "研究方向待核实")}</p></div>', unsafe_allow_html=True)

    for _key in (
        "prof_name",
        "prof_institution",
        "prof_department",
        "prof_title",
        "prof_email",
        "prof_interests",
        "prof_homepage",
        "prof_english_name",
        "prof_search_institution",
        "prof_seed_titles",
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
            # 先记下你此刻已经填好的字段：它们优先级最高，且来源要如实标注
            manual_before = _manual_professor_fields()
            fetched = None
            profile = None
            try:
                with st.spinner("正在解析主页…"):
                    fetched = Fetcher(cache=_page_cache()).fetch(url)
                    if fetched.ok:
                        profile = parse_homepage_html(fetched.text, _llm())
            except Exception as exc:  # noqa: BLE001 - 任何异常都降级为"按已填字段生成清单"
                st.info(f"主页解析遇到问题（{exc}），已改为按你填写的字段生成清单。")

            if profile is not None:
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
                if profile.publications:
                    st.session_state["prof_seed_titles"] = "\n".join(profile.publications)
                if profile.name:
                    st.session_state["_research_steps"] = None
                    st.session_state["_research_granted"] = []
                    st.session_state["_auto_search"] = True
                st.success("已解析主页，字段已填入下方表格，请核对后继续。")
            elif fetched is not None and not fetched.ok:
                st.info(
                    f"这个页面没抓到（{fetched.friendly_error()}）。"
                    "已按你填写的姓名/学校生成字段清单，缺的部分手动补充即可。"
                )

            html = fetched.text if (fetched is not None and fetched.ok) else ""
            st.session_state["homepage_profile"] = profile
            st.session_state["homepage_html"] = html
            _refresh_professor_review(manual=manual_before)
            if fetched is not None and fetched.from_cache:
                st.caption("这个链接的页面在保质期内，直接用了本地缓存，没有再访问对方网站。")

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
    identity_confirmed = st.checkbox("我已核对并确认以上信息属于目标导师",
                                     key="identity_confirmed")
    paper_read_confirmed = st.checkbox("我已阅读以上论文（可选，允许邮件提及）",
                                       key="paper_read_confirmed")

    if st.button("📋 检查信息补齐情况",
                 help="不联网，只看你已填的字段哪些已确认、哪些还缺"):
        _refresh_professor_review()

    if st.session_state.get("identity_result") is not None:
        _render_identity_panel(st.session_state["identity_result"])
    if st.session_state.get("field_report") is not None:
        _render_field_report(st.session_state["field_report"])

    _render_roster_picker()

    if st.button("🔍 从导师库填充", help="在已采集的高校导师库中按姓名+学校查找并自动填充"):
        matches = _lookup_faculty(professor_name, institution)
        if not matches:
            st.info("导师库中未找到该导师（可能其学院尚未采集，或姓名/学校不匹配）。")
        else:
            best = matches[0]
            if best.college:
                st.session_state["prof_department"] = best.college
            if best.title:
                st.session_state["prof_title"] = best.title
            if best.email:
                st.session_state["prof_email"] = best.email
            if best.homepage_url:
                st.session_state["prof_homepage"] = best.homepage_url
            directions = best.research_directions or best.research_areas
            if directions:
                st.session_state["prof_interests"] = "、".join(directions)
            if best.publications:
                st.session_state["prof_seed_titles"] = "\n".join(best.publications)
            st.session_state["_faculty_directions"] = directions
            st.session_state["_faculty_seed_titles"] = best.publications
            note = f"已从导师库填充「{best.name} · {best.university} · {best.college}」"
            if len(matches) > 1:
                note += f"（共 {len(matches)} 条同名匹配，已取第一条，请核对）"
            st.success(note)
            st.rerun()
    st.button("进入论文证据工作台 →", type="primary", on_click=_navigate, args=("papers",))


if active_page == "papers":
    st.markdown('<div class="page-eyebrow">03 / PAPER EVIDENCE WORKSPACE</div>',
                unsafe_allow_html=True)
    st.title("让每一篇论文，都有来处。")
    st.markdown('<p class="page-intro">筛选候选、核对机构与作者、阅读摘要；'
                '人工确认的论文才进入报告。</p>', unsafe_allow_html=True)
    st.markdown(_step_markup, unsafe_allow_html=True)
    st.markdown('<div class="section-note">RESEARCH CONTROLS / 检索与消歧</div>',
                unsafe_allow_html=True)
    st.caption(
        "Agent 会按学科分流检索多个免费学术库（OpenAlex 打底，计算机/医学/物理等补查专业库），"
        "跨库去掉重复后汇总；配了万方 Key 时中文库也会一起查。结果须你勾选确认归属。"
    )
    selected_mode = st.radio(
        "检索方式",
        ["auto", "zh", "en"],
        format_func=lambda k: {"auto": "自动（推荐）", "zh": "仅中文", "en": "仅英文"}[k],
        horizontal=True,
        key="search_mode",
    )
    discipline_options = ["", *DISCIPLINE_LABELS]
    selected_discipline = st.selectbox(
        "学科方向",
        discipline_options,
        format_func=lambda key: (
            "自动判断（推荐）" if key == "" else DISCIPLINE_LABELS[key]
        ),
        key="prof_discipline",
        help="用来决定补查哪些专业库：计算机去 DBLP、医学去 Europe PMC 等。",
    )
    english_name = st.text_input("导师英文名（英文检索时使用，可选）", key="prof_english_name")
    search_institution = st.text_input(
        "检索用机构（可选；导师有多个单位时填另一所，如西南财经大学）",
        key="prof_search_institution",
    )
    seed_titles_text = st.text_area(
        "代表论文标题（可选，一行一个；作者名查不到时按标题兜底检索）",
        key="prof_seed_titles",
    )
    seed_titles = [t.strip() for t in seed_titles_text.splitlines() if t.strip()]
    faculty_seed = st.session_state.get("_faculty_seed_titles", [])
    if faculty_seed:
        seed_titles = list(dict.fromkeys([*faculty_seed, *seed_titles]))
    faculty_directions = st.session_state.get("_faculty_directions", [])
    search_name = english_name.strip() if selected_mode == "en" else professor_name.strip()

    if "candidate_papers" not in st.session_state:
        st.session_state.candidate_papers = []


    def _trigger_search() -> None:
        if selected_mode == "zh" and not settings.wanfang_app_key:
            st.error(
                "「仅中文」需要一个中文库的访问 Key（在 .env 里配置 WANFANG_APP_KEY）。"
                "不改配置的话，把「检索方式」换成「自动」，就能直接用免 Key 的国际学术库检索。"
            )
            return
        if selected_mode == "en" and not english_name.strip():
            st.error("仅英文检索需要填写「导师英文名」")
            return
        if not search_name:
            st.error("请先填写导师姓名")
            return
        try:
            with st.spinner("Agent 正在按学科分流检索与消歧…"):
                _run_research(
                    search_name,
                    institution.strip(),
                    selected_mode,
                    english_name.strip(),
                    search_institution.strip(),
                    seed_titles,
                    faculty_directions,
                    selected_discipline,
                )
        except Exception as exc:  # noqa: BLE001 - 检索失败降级到手动录入
            st.error(f"检索失败（不影响手动录入）：{exc}")
            st.session_state.candidate_papers = []


    if st.button("🔎 一键研究（Agent）", type="primary"):
        _trigger_search()

    if st.session_state.get("_research_sources"):
        st.caption(
            f"上次检索：{st.session_state['_research_sources']}"
            f"（学科：{st.session_state.get('_research_discipline') or '通用'}）"
        )

    if st.session_state.get("_research_degraded"):
        st.info(
            f"本次检索触发了资源上限（{st.session_state['_research_degraded']}），"
            "已降级返回已完成的部分结果；可稍后重试或改用手动补录。"
        )

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
            _run_research(
                search_name, "", selected_mode, english_name.strip(),
                search_institution.strip(), seed_titles, faculty_directions,
                selected_discipline,
            )
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
            if cand.get("affiliation_note"):
                label += f" · {cand['affiliation_note']}"
            cand["user_confirmed"] = st.checkbox(
                label, value=bool(cand.get("user_confirmed")), key=f"cand_paper_{index}"
            )
            platform = cand.get("source_platform")
            found_in = cand.get("sources") or ([platform] if platform else [])
            if found_in:
                st.caption("收录于：" + "、".join(found_in))
            if cand.get("source_url"):
                st.markdown(f"[查看原文来源 ↗]({cand['source_url']})")

        workspace_list, workspace_detail = st.columns([1.65, 1], gap="large")
        with workspace_list:
            st.markdown('<div class="section-note">CANDIDATE LIST / 候选论文</div>',
                        unsafe_allow_html=True)
            tab_match, tab_review, tab_homonym = st.tabs([
                f"匹配候选 {len(normal)}", f"需审核 {len(review)}", f"疑似同名 {len(homonym)}"
            ])
            for tab, indices in ((tab_match, normal), (tab_review, review),
                                 (tab_homonym, homonym)):
                with tab:
                    if not indices:
                        st.caption("这一类暂无候选论文。")
                    for i in indices:
                        with st.container(border=True):
                            _render_candidate(i, papers[i])
                            if papers[i].get("disambig_reason"):
                                st.caption(papers[i]["disambig_reason"])
        with workspace_detail:
            st.markdown('<div class="section-note">EVIDENCE INSPECTOR / 证据详情</div>',
                        unsafe_allow_html=True)
            chosen_index = st.selectbox(
                "选择论文查看证据", list(range(len(papers))),
                format_func=lambda i: f"{i + 1:02d} · {papers[i].get('title') or '未命名论文'}",
                key="inspected_paper_index",
            )
            selected_paper = papers[chosen_index]
            with st.container(border=True):
                st.subheader(selected_paper.get("title") or "未命名论文")
                paper_source = (
                    selected_paper.get("venue")
                    or selected_paper.get("source_platform")
                    or "来源待核实"
                )
                st.caption(f"{selected_paper.get('year') or '年份未知'}　·　{paper_source}")
                found_in = selected_paper.get("sources") or []
                if found_in:
                    st.caption("收录于：" + "、".join(found_in))
                st.markdown("**归属线索**")
                st.write("机构：" + (selected_paper.get("institution") or "未提供，请核对原文"))
                st.write("作者：" + "、".join(selected_paper.get("authors") or ["待核对"]))
                if selected_paper.get("affiliation_note"):
                    st.caption(selected_paper["affiliation_note"])
                if selected_paper.get("disambig_reason"):
                    st.warning(selected_paper["disambig_reason"])
                st.markdown("**摘要摘录**")
                st.write(selected_paper.get("abstract") or "暂无摘要，请打开原文核对。")
                if selected_paper.get("source_url"):
                    st.markdown(f"[打开原文来源 ↗]({selected_paper['source_url']})")
                st.caption("系统判断只作辅助；勾选确认后才会纳入报告。")
        paper_values.extend([cand for cand in papers if cand["user_confirmed"]])

    with st.expander("✍️ 手动补录论文（检索不到时使用）"):
        paper_count = int(st.number_input("补录论文数量", min_value=1, max_value=10,
                                          value=1, key="manual_paper_count"))
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
            st.session_state.active_page = "report"
            st.rerun()
        except (ValidationError, ValueError) as exc:
            st.error(f"请检查输入：{exc}")
        except Exception as exc:  # noqa: BLE001 - UI 必须显示可操作错误
            st.error(f"生成失败：{exc}")
