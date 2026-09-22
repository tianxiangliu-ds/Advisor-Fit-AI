# ruff: noqa: E402
"""AdvisorFit AI：Evidence-Grounded 导师匹配 Agent 的 Streamlit 演示前端。

流程：CV 本地解析与人工确认 → 导师身份核对 → Agent 检索论文并消歧
→ 可解释匹配 → 事实锁定邮件 → 导出与完整删除。
Agent 的每一步都记录在 Harness 轨迹里，并在「论文核验」页对外展示。
"""

from __future__ import annotations

import json
import sys
import uuid
from html import escape
from pathlib import Path

# 支持直接使用系统 Python 启动 Streamlit：源码采用 src/ 目录布局。
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import streamlit as st
import streamlit.components.v1 as components
from pydantic import ValidationError

from advisor_fit import __version__, ui_trace
from advisor_fit.analysis.direction_search import (
    completeness_text,
    rank_candidates,
    split_terms,
)
from advisor_fit.config import settings
from advisor_fit.demo_privacy import (
    cleanup_stale_visitor_dirs,
    uploads_dir_for_visitor,
    visible_run_ids,
)
from advisor_fit.export.report import export_docx, export_json, export_markdown
from advisor_fit.harness.state import RunState
from advisor_fit.ingest.cv import (
    FACT_FIELD_LABELS,
    ParsedDocument,
    apply_fact_edits,
    build_student_profile,
    delete_uploaded_cv,
    extract_pdf_content,
)
from advisor_fit.ingest.cv_llm import build_student_profile_llm
from advisor_fit.ingest.fetch import Fetcher
from advisor_fit.ingest.homepage import html_to_text, parse_homepage_html
from advisor_fit.ingest.manual_professor import (
    ManualPaperInput,
    ManualProfessorInput,
    professor_identity_key,
    validate_paper_values,
)
from advisor_fit.ingest.profile_fallback import REQUIRED_FIELDS, build_field_report
from advisor_fit.llm.provider import NullLLM, build_llm
from advisor_fit.manual_pipeline import run_manual_pipeline
from advisor_fit.providers.disciplines import DISCIPLINE_LABELS
from advisor_fit.services.research import ResearchRequest, run_research
from advisor_fit.storage.advisor_repo import AdvisorRepository
from advisor_fit.storage.backup import (
    build_backup_bytes,
    collect_entries,
    default_backup_name,
    manifest_text,
)
from advisor_fit.storage.cache import PageCache
from advisor_fit.storage.repository import Repository
from advisor_fit.storage.roster_repo import RosterRepository
from advisor_fit.storage.uploads import (
    DEFAULT_RETENTION_DAYS,
    cleanup_orphans,
    scan_uploads,
)
from advisor_fit.ui_state import forget_widgets, remember_widgets, restore_widgets
from advisor_fit.ui_theme import CSS

_PERSISTED_WIDGETS = (
    "student_name", "prof_name", "prof_institution", "prof_department", "prof_title",
    "prof_email", "prof_interests", "prof_homepage", "prof_english_name",
    "prof_search_institution", "prof_seed_titles", "search_mode",
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
    "candidate_papers", "_research_confirm", "_run_state",
    "_research_degraded", "_research_sources", "_research_discipline", "_research_health",
    "_research_clues",
    "_agent_trace", "_show_research_correction",
    "field_report", "identity_result", "homepage_profile",
    "result", "identity_confirmed", "paper_read_confirmed",
    "manual_paper_count", "duplicate_archive_action",
)

# 会话级状态：清空全部数据时在学生简历之外额外要清掉的键。
_SESSION_STATE_KEYS = (
    "student", "student_fact_editor", "parsed_text", "pdf_extraction_engine",
    "student_name", "cv_path",
    "session_run_ids", "viewed_run", "show_compare", "ui_saved_widgets",
)

# 简历可独立删除：研究记录会继续留在本地档案中，供后续比较。
_CV_STATE_KEYS = (
    "student", "student_fact_editor", "parsed_text", "pdf_extraction_engine",
    "student_name", "cv_path",
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


def _is_demo_mode() -> bool:
    return settings.app_mode.strip().lower() == "demo"


def _current_uploads_dir() -> Path:
    return uploads_dir_for_visitor(
        settings.uploads_dir,
        demo_mode=_is_demo_mode(),
        visitor_id=st.session_state.get("visitor_id", "unknown-visitor"),
    )


def _visible_run_filter(repo: Repository) -> set[str] | None:
    return visible_run_ids(
        repo,
        set(st.session_state.get("session_run_ids", [])),
        demo_mode=_is_demo_mode(),
    )


def _build_student_profile(upload_path, parsed):
    """LLM 结构化抽取优先；未配置或失败时降级为规则抽取。"""
    llm = _llm()
    if isinstance(llm, NullLLM):
        return build_student_profile(parsed)
    try:
        return build_student_profile_llm(parsed.text, llm)
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
            delete_uploaded_cv(_current_uploads_dir(), run_id)
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


def _clear_current_cv() -> None:
    """清除当前简历及其解析状态，不删除已保存的研究档案。"""
    run_id = st.session_state.get("run_id")
    if run_id:
        try:
            delete_uploaded_cv(_current_uploads_dir(), run_id)
        except (ValueError, OSError):
            pass
    cv_path = st.session_state.get("cv_path")
    if cv_path:
        try:
            Path(cv_path).unlink(missing_ok=True)
        except OSError:
            pass
    for key in _CV_STATE_KEYS:
        st.session_state.pop(key, None)


FACT_FIELDS = list(FACT_FIELD_LABELS)


def _fact_rows(student) -> list[dict]:
    return [
        {"id": fact.id, "field": fact.field, "value": str(fact.value or "")}
        for fact in student.facts
    ]


def _add_fact(field: str = "skill") -> None:
    """Add one fact directly inside its category card and open its inline editor."""
    row_id = f"user_{uuid.uuid4().hex[:8]}"
    st.session_state.student_fact_editor.append(
        {"id": row_id, "field": field, "value": ""}
    )
    st.session_state["editing_fact_id"] = row_id


def _remove_fact(row_id: str) -> None:
    st.session_state.student_fact_editor = [
        row for row in st.session_state.student_fact_editor if row.get("id") != row_id
    ]
    if st.session_state.get("editing_fact_id") == row_id:
        st.session_state.pop("editing_fact_id", None)


def _edit_fact(row_id: str) -> None:
    st.session_state["editing_fact_id"] = row_id


def _finish_fact_edit() -> None:
    st.session_state.pop("editing_fact_id", None)


def _focus_history_report() -> None:
    """把刚选择的档案带回页面顶部的报告区。"""
    components.html(
        """
        <script>
        const report = window.parent.document.getElementById('selected-history-report');
        if (report) {
          report.scrollIntoView({behavior: 'smooth', block: 'start'});
        }
        </script>
        """,
        height=0,
    )


def _set_all_facts(confirmed: bool) -> None:
    for row in st.session_state.student_fact_editor:
        row["confirmed"] = confirmed
    for key in list(st.session_state.keys()):
        if key.startswith("fact_conf_"):
            st.session_state[key] = confirmed


def _split_terms(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace("；", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _compact_text(value: object, *, limit: int = 72) -> str:
    """Keep comparison cells readable; full analysis remains in the record card."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _identity_part(value: object) -> str:
    """Normalize user-visible identity fields for local duplicate detection."""
    text = "".join(str(value or "").casefold().split())
    return "" if text == "学院未核实" else text


def _existing_archive_for_professor(
    repo, *, name: str, institution: str, department: str, current_run_id: str
):
    """Return one completed record with the same school/department/name, if any."""
    target = (_identity_part(name), _identity_part(institution), _identity_part(department))
    for row in _compare_runs(repo, allowed_ids=_visible_run_filter(repo)):
        existing = (
            _identity_part(row["导师"]),
            _identity_part(row["学校"]),
            _identity_part(row["学院"]),
        )
        if row["run_id"] != current_run_id and existing == target:
            return row
    return None


def _replace_current_with_new_run() -> str:
    """Discard the empty/current draft run before an explicit archive overwrite."""
    old_run_id = st.session_state.get("run_id")
    if old_run_id:
        try:
            st.session_state.repo.delete_run(old_run_id)
        except ValueError:
            pass
        st.session_state["session_run_ids"] = [
            item for item in st.session_state.get("session_run_ids", []) if item != old_run_id
        ]
    _new_run()
    return st.session_state.run_id


def _run_research(
    name: str,
    institution: str,
    mode: str,
    english_name: str,
    search_institution: str = "",
    seed_titles: list[str] | None = None,
    department: str = "",
    known_directions: list[str] | None = None,
    discipline: str = "",
) -> None:
    """用导师研究 Agent 检索并消歧，结果与确认门控写入 session_state。

    真正的编排在 `advisor_fit.services.research`——同一套流程也被 HTTP 接口复用，
    页面这边只负责"把结果放进会话、并在界面上呈现"。这样换个前端就不必重写一遍。
    """
    outcome = run_research(
        ResearchRequest(
            name=name,
            institution=institution,
            department=department,
            alternate_institution=search_institution,
            english_name=english_name,
            source=mode,
            discipline=discipline,
            seed_titles=list(seed_titles or []),
            known_directions=list(known_directions or []),
        ),
        llm=_llm(),
        state=RunState.from_dict(st.session_state.get("_run_state")),
    )

    st.session_state["_research_degraded"] = outcome.degraded_reason
    st.session_state["_research_sources"] = outcome.sources
    st.session_state["_research_health"] = outcome.health
    st.session_state["_research_discipline"] = outcome.discipline
    clues: list[str] = []
    if english_name.strip():
        clues.append(f"英文名：{english_name.strip()}")
    if search_institution.strip():
        clues.append(f"曾任/备选机构：{search_institution.strip()}")
    if seed_titles:
        clues.append(f"官网或补充论文题名：{len(seed_titles)} 篇")
    if department.strip():
        clues.append(f"院系线索：{department.strip()}")
    if known_directions:
        clues.append(f"已知研究方向：{len(known_directions)} 项")
    st.session_state["_research_clues"] = clues
    # 轨迹留在会话里，供「论文核验」页的 Agent 运行轨迹区展示
    st.session_state["_agent_trace"] = outcome.trace

    run_id = st.session_state.get("run_id")
    if run_id:
        try:
            # 轨迹落库失败不得影响检索结果
            st.session_state.repo.save_trace(run_id, outcome.trace)
        except Exception:  # noqa: BLE001
            pass

    if outcome.needs_confirmation:
        st.session_state["_research_confirm"] = outcome.needs_confirmation
    else:
        st.session_state.pop("_research_confirm", None)
    # 一个对象就是全部可续跑状态（阶段/步骤/已授权/候选论文）
    st.session_state["_run_state"] = (
        outcome.state.to_dict() if outcome.state else None
    )
    st.session_state.candidate_papers = outcome.papers


def _render_empty_result_guidance() -> None:
    """检索回来 0 篇时的说明。

    CLAUDE.md 要求"既不得报错中断，也不得静默返回空结果"。空着一片什么都不说，
    用户会以为工具坏了、或者以为这位导师没有论文——两者都是误导。
    """
    health = st.session_state.get("_research_health") or {}
    if health and health.get("searched", 0) == 0:
        labels = "、".join(health.get("failed_labels") or []) or "全部来源"
        st.warning(
            f"这次**一个来源都没查成**（{labels} 未返回）。"
            "这不代表这位导师没有论文——是检索侧出了问题。"
            "可以先「重试一次」；多次失败时检查网络，或直接手动补录几篇代表作继续。"
        )
        return
    st.info(
        "这些来源都正常返回了，但没有找到匹配的论文。"
        "常见原因是姓名或机构对不上：试试在下面填「导师英文名」、"
        "把「检索用机构」换成他以前的单位，或者直接填几篇代表论文标题按标题查。"
        "也可以手动补录，报告与邮件不依赖自动检索。"
    )


def _render_agent_trace(trace: dict | None) -> None:
    """先展示人话版研究过程，内部轨迹收进技术详情。"""
    if not ui_trace.has_content(trace):
        return

    st.markdown('<div class="section-note">RESEARCH PROCESS / 本次研究过程</div>',
                unsafe_allow_html=True)
    st.caption("这里展示系统实际完成的检索与核对动作；候选论文仍需你人工确认。")
    st.markdown(ui_trace.trace_progress_html(trace), unsafe_allow_html=True)

    user_steps = ui_trace.trace_user_steps(trace)
    if user_steps:
        with st.container(border=True):
            for step in user_steps:
                mark = "✓" if step["status"] == "已完成" else "!"
                st.markdown(f"**{mark}　{escape(step['label'])}**　{escape(step['status'])}")
                if step["detail"]:
                    st.caption(escape(step["detail"]))
    if ui_trace.is_rule_mode(trace):
        st.caption("本次由固定检索规则执行，未调用大语言模型；检索和核对步骤仍为真实执行。")

    if trace.get("degraded_reason"):
        st.markdown(
            ui_trace.trace_degraded_html(str(trace["degraded_reason"])),
            unsafe_allow_html=True,
        )

    tools = trace.get("tools") or []
    with st.expander("技术详情（工具、耗时与运行记录）"):
        mode_note = ui_trace.trace_mode_note(trace)
        if mode_note:
            st.info(mode_note)
        st.markdown(ui_trace.trace_panel_html(trace), unsafe_allow_html=True)
        if tools:
            st.markdown("**本次可调用的研究动作**")
            for spec in tools:
                st.markdown(
                    f"**{escape(ui_trace.user_tool_label(str(spec.get('name', ''))))}**　"
                    f"{escape(str(spec.get('description', '')))}"
                )
                permission = str(spec.get("permission", ""))
                if permission == "network":
                    st.caption("需要访问公开学术或学校网站。")
                elif permission:
                    st.caption("在本地整理已取得的信息。")
        st.download_button(
            "下载技术轨迹（JSON）",
            data=json.dumps(trace, ensure_ascii=False, indent=2),
            file_name="agent-trace.json",
            mime="application/json",
            help="完整步骤、工具清单与资源消耗，供开发调试或复盘。",
        )


def _known_run_ids() -> list[str]:
    """所有研究记录的 id：上传的简历按 `{run_id}.pdf` 命名，靠它判断哪些文件还有用。"""
    repo = st.session_state.get("repo")
    if repo is None:
        return []
    try:
        return [run["id"] for run in repo.list_runs(allowed_ids=_visible_run_filter(repo))]
    except Exception:  # noqa: BLE001 - 账本读不出来时按"全部是孤儿"处理太危险，返回空
        return []


def _uploads_report():
    return scan_uploads(_current_uploads_dir(), _known_run_ids())


def _auto_clean_uploads() -> None:
    """启动时按保留期清理一次孤儿简历（每个会话只做一次，失败不影响使用）。"""
    if st.session_state.get("_uploads_cleaned"):
        return
    st.session_state["_uploads_cleaned"] = True
    try:
        if _is_demo_mode():
            cleanup_stale_visitor_dirs(
                settings.uploads_dir,
                older_than_days=DEFAULT_RETENTION_DAYS,
            )
        cleanup_orphans(
            _current_uploads_dir(),
            _known_run_ids(),
            older_than_days=DEFAULT_RETENTION_DAYS,
        )
    except Exception:  # noqa: BLE001 - 清理失败绝不能挡住页面
        pass


def _faculty_repo():
    from advisor_fit.storage.faculty_repo import FacultyRepository

    return FacultyRepository(settings.data_dir / "faculty.db")


@st.cache_resource(show_spinner=False)
def _embedder():
    """语义召回用的向量后端（可替换）。构建一次复用。

    默认是内置的离线档——它只做**表层相似**，不是同义词理解；
    配了 embedding 接口或装了本机模型才会升级。界面上的措辞按
    `embedder.semantic` 区分，不会把表层相似说成"语义理解"。
    """
    from advisor_fit.providers.embedding import build_embedder

    return build_embedder()


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
    """重算字段来源清单；官网自动填入不能拿来与自身交叉核对。

    manual 用于字段来源的归属：解析主页后要传入"解析前用户已填的内容"，
    否则官网抓来的值会被误标成"你手动填写"。
    """
    profile = st.session_state.get("homepage_profile")
    homepage_html = st.session_state.get("homepage_html", "")
    sources = dict(manual) if manual is not None else _manual_professor_fields()
    st.session_state["field_report"] = build_field_report(
        manual=sources,
        homepage_html=homepage_html,
        page_text=html_to_text(homepage_html) if homepage_html else "",
        llm_profile=profile,
        source_url=st.session_state.get("prof_homepage") or None,
    )
    # 外部论文机构、作者档案或导师库独立命中到来前，不能把同一主页的内容称为“已核对”。
    st.session_state["identity_result"] = None


def _render_identity_anchor(profile) -> None:
    """展示身份锚点及其来源，不把同一官网内容当作独立核对证据。"""
    with st.expander("导师主页解析与身份依据", expanded=False):
        st.markdown(
            '<div class="section-note">IDENTITY ANCHOR / 导师身份依据</div>',
            unsafe_allow_html=True,
        )
        homepage = st.session_state.get("prof_homepage", "").strip()
        current = _manual_professor_fields()
        if profile is not None and homepage:
            st.markdown("**官方主页提取**　已将姓名、学校、学院等信息作为本次研究的身份锚点。")
            extracted = [
                label for key, label in (("name", "姓名"), ("institution", "学校"),
                                         ("department", "学院"), ("email", "邮箱"))
                if str(getattr(profile, key, "") or "").strip()
            ]
            st.caption(
                "已提取：" + "、".join(extracted or ["基础页面信息"]) +
                "。这些来自同一主页，尚未构成独立交叉核验。"
            )
            st.markdown(f"[查看官方主页 ↗]({homepage})")
        else:
            st.caption("尚未解析官方主页。填写姓名和学校后仍可继续，但同名风险需要你后续逐篇核对。")
        identity_key = professor_identity_key(
            homepage or None,
            current["institution"],
            current["department"],
            current["name"],
        )
        st.caption(f"本地身份标识：{identity_key}。后续会用它尝试匹配导师库和学术作者档案。")


def _render_local_advisor_supplement() -> None:
    """Show cached advisor data only after exact homepage-key matching."""
    homepage = str(st.session_state.get("prof_homepage") or "").strip()
    if not homepage:
        return
    identity_key = professor_identity_key(homepage, "", "", "")
    try:
        advisor = AdvisorRepository(settings.data_dir / "advisors.db").lookup_by_identity_key(
            identity_key
        )
    except Exception:  # noqa: BLE001 - incomplete local database never blocks entry
        return
    if advisor is None:
        return
    with st.container(border=True):
        st.markdown('<div class="section-note">LOCAL PROFILE / 本地资料补充</div>',
                    unsafe_allow_html=True)
        st.caption("该资料与当前主页身份标识精确一致，仅作补充；不会覆盖你已核对的字段。")
        if advisor.research_directions:
            st.write("**已存研究方向**　" + "、".join(advisor.research_directions[:6]))
        if advisor.publications:
            st.write("**已存代表作**　" + "、".join(advisor.publications[:4]))
        if advisor.retrieved_at:
            st.caption(f"资料抓取时间：{advisor.retrieved_at}")
        if advisor.source_url:
            st.markdown(f"[查看本地资料来源 ↗]({advisor.source_url})")


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
    """紧凑展示字段覆盖与来源；缺少可选字段不阻塞流程。"""
    with st.expander("已提取的信息与来源", expanded=False):
        st.markdown('<div class="section-note">PROFILE COVERAGE / 已提取的信息</div>',
                    unsafe_allow_html=True)
        st.caption(report.summary())
        entries = list(report.fields.values())
        for index in range(0, len(entries), 2):
            columns = st.columns(2, gap="small")
            for column, entry in zip(columns, entries[index:index + 2], strict=False):
                with column:
                    badge = _FIELD_STATUS_BADGES.get(entry.status.value, entry.status.value)
                    value = entry.value if entry.known else "待补充"
                    st.markdown(f"**{entry.label}**　{badge}")
                    st.write(value)
                    st.caption(f"来源：{entry.source}" if entry.known else "可选信息，可稍后补充")
                    if entry.note:
                        st.caption(entry.note)
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


def _brief_heading(number: str, title: str) -> None:
    """无锚点的小节标题，避免 Streamlit 自动生成无意义的 #01 链接。"""
    st.markdown(
        f'<div class="brief-section-title"><span>{escape(number)}</span>'
        f"{escape(title)}</div>",
        unsafe_allow_html=True,
    )


def _render_point_with_sources(point, evidence_map: dict) -> None:
    """分析结论与它实际引用的来源放在一起展示。"""
    st.write(f"· {point.text}")
    linked = [evidence_map[eid] for eid in point.evidence_ids if eid in evidence_map]
    for evidence in linked:
        if evidence.source_url:
            title = str(evidence.title or "查看来源").replace("[", "［").replace("]", "］")
            st.markdown(f"　[📄 {title}]({evidence.source_url})")


def _render_brief(result) -> None:
    """单栏研究简报：判断、理由与来源在同一阅读路径中。"""
    professor = result.professor
    report = result.match_report
    deep = getattr(result, "deep_analysis", None)
    direction = getattr(result, "direction_summary", None)
    evidence_map = {evidence.id: evidence for evidence in result.evidences}
    fact_ids = {
        fact_id for dimension in report.dimensions for fact_id in dimension.student_fact_ids
    }
    cited_evidence_ids = {
        evidence_id
        for dimension in report.dimensions
        for evidence_id in dimension.professor_evidence_ids
    }

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
        st.caption(
            "颜色说明：🟢 强交集＝已有直接且可追溯的研究/能力联系；"
            "🟡 部分交集＝已找到交集，但证据或深度仍不足；"
            "🔴 交集较少＝当前材料未形成直接联系；"
            "⚪ 证据不足＝资料不足，暂不判断。颜色不代表录取概率。"
        )
        if report.research_fit.value == "PARTIAL":
            st.markdown("**黄色表示：已经找到可核验的交集，但目前不足以得出强匹配结论。**")
            st.caption(
                f"为什么是黄色：本次有 {len(fact_ids)} 项学生事实与 "
                f"{len(cited_evidence_ids)} 条导师证据形成联系；仍需结合下方缺口继续判断。"
            )
        elif report.research_fit.value == "STRONG":
            st.caption("绿色表示：已确认能力与导师研究存在直接、可追溯的交集；仍不代表录取结果。")
        elif report.research_fit.value == "WEAK":
            st.caption("红色表示：现有已确认材料中尚未形成直接交集，不代表未来无法补足。")
        else:
            st.caption("灰色表示：当前证据不足，暂不作匹配判断。")
        st.caption(
            f"本次使用：已核实论文 {len(professor.recent_publications)} 篇；"
            f"用于交集判断的学生事实 {len(fact_ids)} 项。"
        )
        if report.opportunity_signal == "UNKNOWN":
            st.info("招生机会：未找到可核验的公开声明，建议在邮件中礼貌询问。")

        _brief_heading("01", "有据可循的交集")
        grounded_strengths = [
            *(deep.research_intersection if deep is not None else []),
            *(deep.method_match if deep is not None else []),
        ]
        if grounded_strengths:
            for point in grounded_strengths:
                _render_point_with_sources(point, evidence_map)
        else:
            shown = [
                item for item in report.dimensions
                if item.key == "research_topic" and item.summary
            ]
            if not shown:
                shown = [item for item in report.strengths if item.key != "evidence"]
            for item in shown:
                st.markdown(f"**{item.label}**　{item.summary}")
            if not shown:
                st.write("目前缺少足够的已确认交集，不宜将兴趣相近写成能力匹配。")
        if direction is not None and direction.summary:
            st.markdown("**导师近期研究方向**")
            st.write(direction.summary)

        _brief_heading("02", "尚需澄清的距离")
        grounded_gaps = deep.background_gaps if deep is not None else []
        if grounded_gaps:
            for point in grounded_gaps:
                _render_point_with_sources(point, evidence_map)
        elif report.gaps:
            for gap in report.gaps:
                st.write(f"· {gap}")
        else:
            st.write("暂无明确缺口；仍需核实近期课题与招生情况。")

        if deep is not None and deep.recommended_papers:
            _brief_heading("03", "最值得读的论文")
            for point in deep.recommended_papers:
                _render_point_with_sources(point, evidence_map)

        _brief_heading("04", "建议的联系角度")
        if deep is not None:
            for point in deep.knowledge_to_supplement:
                _render_point_with_sources(point, evidence_map)
        if report.questions_to_ask:
            for question in report.questions_to_ask:
                st.write(f"· {question}")
        elif not (deep is not None and deep.knowledge_to_supplement):
            st.write("围绕已核实论文提出具体问题，并说明自己的真实工作。")
        st.caption("方法说明：未确认的候选论文与推断身份，不作为确定性结论。")

    with st.expander(f"查看全部证据来源（{len(result.evidences)} 条）"):
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
            st.write("官网公开方向：" + "、".join(t.topic for t in professor.declared_interests))
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
            st.text_input("邮件主题", value=draft.subject or "", key="draft_subject")
            body = "\n".join(sentence.text for sentence in draft.sentences)
            st.text_area("邮件正文（可直接编辑）", value=body, height=420, key="draft_body")
            st.caption("请在发送前按你的真实情况修改；系统不会发送，也不会替你承诺未证实的经历。")
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

    from advisor_fit.llm.analysis import DeepAnalysis, DirectionSummary
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
        deep_analysis=DeepAnalysis(**(repo.load_latest(run_id, "deep_analysis") or {})),
        direction_summary=DirectionSummary(
            **(repo.load_latest(run_id, "direction_summary") or {})
        ),
        evidences=[Evidence(**data) for data in repo.load_artifacts(run_id, "evidence")],
    )


def _compare_runs(repo, *, allowed_ids: set[str] | None = None) -> list[dict]:
    """把历史已完成的 run 整理成档案卡与对比行。"""
    from advisor_fit.llm.analysis import DeepAnalysis
    from advisor_fit.models.match import MatchReport
    from advisor_fit.models.professor import ProfessorProfile

    fit_labels = {
        "STRONG": "强交集",
        "PARTIAL": "部分交集",
        "WEAK": "交集较少",
        "UNKNOWN": "证据不足",
    }
    recommendation_labels = {
        "WORTH_CONTACTING": "值得进一步联系",
        "LEARN_MORE": "先补充了解",
        "LOW_PRIORITY": "当前优先级较低",
        "INSUFFICIENT_EVIDENCE": "暂无法建议",
    }
    evidence_labels = {"HIGH": "较充分", "MEDIUM": "一般", "LOW": "不足"}

    rows: list[dict] = []
    for run in repo.list_runs(allowed_ids=allowed_ids):
        if run["status"] != "COMPLETED":
            continue
        professor_data = repo.load_latest(run["id"], "professor_profile")
        match_data = repo.load_latest(run["id"], "match")
        if not (professor_data and match_data):
            continue
        professor = ProfessorProfile(**professor_data)
        match = MatchReport(**match_data)
        deep_analysis = DeepAnalysis(**(repo.load_latest(run["id"], "deep_analysis") or {}))
        personalised_strengths = [
            point.text
            for point in [
                *deep_analysis.research_intersection,
                *deep_analysis.method_match,
            ]
        ]
        personalised_gaps = [point.text for point in deep_analysis.background_gaps]
        specific_fallbacks = [
            dimension.summary
            for dimension in match.dimensions
            if dimension.key == "research_topic"
            and dimension.summary
            and "学生「" in dimension.summary
            and "导师研究「" in dimension.summary
        ]
        professor_name = str(professor.name.value or professor.professor_id)
        institution_name = str(professor.institution.value or "学校未核实")
        department_name = str(professor.department.value or "学院未核实")
        rows.append(
            {
                "run_id": run["id"],
                "记录": run.get("name")
                or f"{professor_name} · {institution_name} · {department_name}",
                "导师": professor_name,
                "学校": institution_name,
                "学院": department_name,
                "研究匹配": fit_labels.get(match.research_fit.value, "证据不足"),
                "建议": recommendation_labels.get(
                    match.recommendation.value, "暂无法建议"
                ),
                "证据充分度": evidence_labels.get(
                    match.evidence_sufficiency, "不足"
                ),
                "个人化交集": "；".join(personalised_strengths[:2])
                or "；".join(specific_fallbacks[:2])
                or "旧记录暂无个性化分析；重新研究后可补齐",
                "需补充": "；".join(personalised_gaps[:2])
                or "；".join(match.gaps[:2])
                or "暂无已核实的缺口结论",
                "updated_at": run.get("updated_at") or run.get("created_at") or "",
                "professor": professor,
            }
        )
    return rows


st.set_page_config(page_title="导师双选 AI 助手", layout="wide", page_icon="🎓")
st.markdown(CSS, unsafe_allow_html=True)

for key, default in (
    ("visitor_id", str(uuid.uuid4())),
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
_auto_clean_uploads()

with st.sidebar:
    st.markdown('<div class="studio-brand"><span class="brand-mark">◎</span>择研'
                f'<small>ADVISOR FIT STUDIO · v{__version__}</small></div>',
                unsafe_allow_html=True)
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
            "建库方式（可选，需要联网、由你手动触发）：导师数据的采集与建库工具是"
            "**配套的独立项目** `../advisor-fit-crawl/`，用法见那边的 `README.md`。\n\n"
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
                st.session_state["direction_backend"] = (
                    "语义相近" if _embedder().semantic else "字面相近"
                )
                st.session_state.pop("direction_picked", None)

        hits = st.session_state.get("direction_hits") or []
        if hits:
            terms = st.session_state.get("direction_terms") or []
            st.markdown('<div class="section-note">CANDIDATES / 候选导师</div>',
                        unsafe_allow_html=True)
            st.caption(
                f"关键词：{'、'.join(terms)}　·　共 {len(hits)} 位候选，"
                "按「命中权重 → 向量相似度 → 资料完整度」排序；"
                "匹配理由就是推荐依据，请自行核对。"
            )
            _backend = st.session_state.get("direction_backend")
            if _backend == "字面相近":
                st.caption(
                    "提示：当前用的是内置的离线向量档，它只能识别**字面相近**"
                    "（词序不同、部分重合），认不出同义词。要真正的语义召回，"
                    "请在 `.env` 里配置 embedding 接口，或安装 `embed` 可选依赖。"
                )
            elif _backend == "语义相近":
                st.caption("当前已启用语义向量后端，可召回「没命中字面但意思接近」的候选。")
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
    archive_rows = _compare_runs(
        st.session_state.repo,
        allowed_ids=_visible_run_filter(st.session_state.repo),
    )
    viewed_run = st.session_state.get("viewed_run")
    history_focus_nonce = int(st.session_state.pop("_history_report_focus_nonce", 0) or 0)
    if viewed_run is not None:
        st.markdown('<div id="selected-history-report"></div>', unsafe_allow_html=True)
        # 切换卡片时更换展开框标识，避免沿用上一份报告的收起状态。
        history_label = "历史报告（点击展开 / 收起）" + ("\u200b" * history_focus_nonce)
        with st.expander(history_label, expanded=True):
            _render_brief(viewed_run)
            _render_agent_trace(st.session_state.get("_agent_trace"))
            with st.expander("查看该记录的邮件草稿"):
                _render_letter(viewed_run)
        if history_focus_nonce:
            _focus_history_report()
    if not archive_rows:
        st.info("暂无已完成的研究记录。生成一份报告后会出现在这里。")
    else:
        st.markdown('<div class="section-note">ARCHIVE / 已完成档案</div>',
                    unsafe_allow_html=True)
        filter_left, filter_middle, filter_right = st.columns((2, 1, 1), gap="medium")
        with filter_left:
            archive_query = st.text_input(
                "搜索导师、学校或学院", key="archive_query"
            ).strip().casefold()
        schools = sorted(
            {row["学校"] for row in archive_rows if row["学校"] != "学校未核实"}
        )
        with filter_middle:
            archive_school = st.selectbox(
                "学校", ["全部学校", *schools], key="archive_school"
            )
        departments = sorted(
            {
                row["学院"]
                for row in archive_rows
                if row["学院"] != "学院未核实"
                and (archive_school == "全部学校" or row["学校"] == archive_school)
            }
        )
        with filter_right:
            archive_department = st.selectbox(
                "学院", ["全部学院", *departments], key="archive_department"
            )

        filtered_rows = [
            row
            for row in archive_rows
            if (
                not archive_query
                or archive_query
                in " ".join(
                    (row["记录"], row["导师"], row["学校"], row["学院"])
                ).casefold()
            )
            and (archive_school == "全部学校" or row["学校"] == archive_school)
            and (
                archive_department == "全部学院" or row["学院"] == archive_department
            )
        ]
        st.caption(f"共 {len(filtered_rows)} 份已完成档案；只有已生成报告的记录会出现在这里。")

        from advisor_fit.analysis.outreach_collision import (
            AdvisorContactRecord,
            CollisionLevel,
            assess_contact_group,
            normalise_department,
        )

        st.markdown('<div class="section-note">COMPARE & CONTACT / 导师对比与联系提醒</div>',
                    unsafe_allow_html=True)
        st.caption("先点选 2–5 位导师；选中的标签会高亮，再生成一张精简的比较看板。")
        choices = {row["run_id"]: row for row in archive_rows}
        selected_ids = st.pills(
            "选择要比较的导师（最多 5 位）",
            list(choices),
            selection_mode="multi",
            format_func=lambda run_id: (
                f"{choices[run_id]['导师']} · {choices[run_id]['学校']}"
                f" · {choices[run_id]['学院']}"
            ),
            key="archive_compare_ids",
        )
        if len(selected_ids) > 5:
            selected_ids = selected_ids[:5]
            st.warning("一次最多比较 5 位导师；已只保留前 5 位。")
        if len(selected_ids) == 1:
            st.info("再选一位导师，就能生成比较与联系碰撞提醒。")
        elif len(selected_ids) >= 2:
            selected_rows = [choices[run_id] for run_id in selected_ids]
            st.dataframe(
                [
                    {
                        "导师": row["导师"],
                        "学校": row["学校"],
                        "学院": normalise_department(row["学院"]),
                        "研究匹配": row["研究匹配"],
                        "建议": _compact_text(row["建议"], limit=28),
                        "证据": row["证据充分度"],
                    }
                    for row in selected_rows
                ],
                hide_index=True,
                use_container_width=True,
            )
            with st.expander("查看各导师的关键交集与需补充项"):
                for row in selected_rows:
                    st.markdown(f"**{row['导师']} · {row['学校']}**")
                    st.write("关键交集：" + _compact_text(row["个人化交集"], limit=180))
                    st.write("需补充：" + _compact_text(row["需补充"], limit=120))
            contact_records = {
                row["run_id"]: AdvisorContactRecord(
                    run_id=row["run_id"],
                    name=row["导师"],
                    institution=row["学校"],
                    department=row["学院"] if row["学院"] != "学院未核实" else "",
                    publications=row["professor"].recent_publications,
                )
                for row in selected_rows
            }
            st.markdown("**联系碰撞提醒**")
            assessment = assess_contact_group(list(contact_records.values()))
            message = assessment.reason
            if assessment.high_risk_pairs:
                direct_lines = [
                    (
                        f"{choices[pair.left_run_id]['导师']} ↔ "
                        f"{choices[pair.right_run_id]['导师']}：{pair.reason}"
                    )
                    for pair in assessment.high_risk_pairs
                ]
                message += " 已发现的直接合作线索：" + "；".join(direct_lines)
            if assessment.level == CollisionLevel.HIGH:
                st.error("🔴 建议错开联系　" + message)
            elif assessment.level == CollisionLevel.CAUTION:
                st.warning("🟠 建议分批联系　" + message)
            elif assessment.level == CollisionLevel.LOW:
                st.success("🟢 当前公开证据未发现明显碰撞　" + message)
            else:
                st.info("⚪ 信息不足　" + message)
            if assessment.high_risk_pairs:
                for pair in assessment.high_risk_pairs:
                    if pair.evidence:
                        st.caption("合作线索依据：" + "；".join(pair.evidence))

        page_size = 9
        page_count = max(1, (len(filtered_rows) + page_size - 1) // page_size)
        archive_page = 1
        if page_count > 1:
            archive_page = st.selectbox(
                "档案页码",
                list(range(1, page_count + 1)),
                format_func=lambda page: f"第 {page} / {page_count} 页",
                key="archive_page",
            )
        start = (archive_page - 1) * page_size
        visible_rows = filtered_rows[start : start + page_size]
        for offset in range(0, len(visible_rows), 3):
            card_columns = st.columns(3, gap="large")
            for column, row in zip(
                card_columns, visible_rows[offset : offset + 3], strict=False
            ):
                with column, st.container(border=True):
                    st.subheader(row["导师"])
                    st.caption(f"{row['学校']} · {row['学院']}")
                    st.write(f"**研究匹配**　{row['研究匹配']}")
                    with st.container(height=150, border=False):
                        st.write(row["个人化交集"])
                    if st.button(row["记录"], key=f"hist_{row['run_id']}"):
                        st.session_state.viewed_run = _load_run_view(
                            st.session_state.repo, row["run_id"]
                        )
                        # 把当时那次 Agent 运行轨迹一并读回，方便回看。
                        try:
                            st.session_state["_agent_trace"] = (
                                st.session_state.repo.load_trace(row["run_id"])
                            )
                        except Exception:  # noqa: BLE001 - 轨迹失败不影响报告
                            st.session_state["_agent_trace"] = None
                        # 重新运行后，报告自动展开并定位到页面顶部，不会藏在卡片下方。
                        st.session_state["_history_report_focus_nonce"] = (
                            int(st.session_state.get("_history_report_focus_nonce", 0)) + 1
                        )
                        st.rerun()
        if not visible_rows:
            st.info("没有符合当前筛选条件的导师档案。")

    st.divider()
    st.markdown('<div class="section-note">BACKUP / 备份与恢复</div>',
                unsafe_allow_html=True)
    if _is_demo_mode():
        st.caption("公开演示站不提供数据库备份，避免把其他访客的数据打包带出。")
    else:
        st.caption(
            "把研究记录打包下载，换电脑或误删时可恢复。"
            "备份包**不含** API Key（.env）与上传的简历；导师大库可用采集脚本重建。"
        )
        try:
            # 项目根目录（.env / uploads 相对它）+ 实际数据目录（可能是 DATA_DIR 指定的别处）
            entries, _, skipped = collect_entries(
                Path(__file__).resolve().parent, data_dir=settings.data_dir
            )
            if entries:
                st.download_button(
                    "💾 下载数据备份（.zip）",
                    build_backup_bytes(
                        entries, manifest_text(entries, skipped, version=__version__)
                    ),
                    file_name=default_backup_name(),
                    mime="application/zip",
                )
            else:
                st.caption("暂无可备份的数据。")
        except Exception as exc:  # noqa: BLE001 - 备份失败不该影响页面
            st.caption(f"备份暂不可用：{exc}")

    st.divider()
    st.markdown('<div class="section-note">LOCAL DATA / 本地数据管理</div>',
                unsafe_allow_html=True)
    st.caption("清空简历不会影响已完成档案；只有明确删除档案时，历史报告才会被移除。")
    if st.button("仅清空当前简历"):
        _clear_current_cv()
        st.success("当前简历和学生事实已清空；已保存的研究档案仍可在本页比较。")
        st.rerun()
    confirm_clear = st.checkbox("我确认删除当前本地研究档案与上传简历")
    if st.button("清空本地研究档案", disabled=not confirm_clear):
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
        st.caption("这份报告和草稿在生成时已保存到本地研究档案。")
        st.button(
            "查看已保存的研究档案 →",
            type="primary",
            on_click=_navigate,
            args=("history",),
        )
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
    st.markdown('<p class="page-intro">从 PDF 简历提取事实；保留、编辑或删除。'
                '保留的内容才会进入匹配。</p>', unsafe_allow_html=True)
    st.markdown(_step_markup, unsafe_allow_html=True)
    resume_left, resume_right = st.columns([0.9, 1.1], gap="large")
    with resume_left:
        st.subheader("原始简历 / 本地解析")
        uploaded = st.file_uploader("上传 CV（PDF，仅在本机解析）", type=["pdf"])
        if st.button("解析 CV", type="primary", disabled=uploaded is None):
            upload_path = _current_uploads_dir() / f"{st.session_state.run_id}.pdf"
            upload_path.parent.mkdir(parents=True, exist_ok=True)
            upload_path.write_bytes(uploaded.getvalue())
            st.session_state["cv_path"] = str(upload_path)
            extraction = extract_pdf_content(upload_path)
            parsed = ParsedDocument(text=extraction.text, warnings=extraction.warnings)
            student = _build_student_profile(upload_path, parsed)
            st.session_state.student = student
            st.session_state.student_fact_editor = _fact_rows(student)
            st.session_state.parsed_text = parsed.text
            st.session_state.pdf_extraction_engine = extraction.engine
            if student.name:
                st.session_state["student_name"] = student.name
            if parsed.warnings:
                st.warning("；".join(parsed.warnings))
        student_name = st.text_input(
            "你的姓名（用于邮件落款，已自动从简历填入，请核对修改）", key="student_name"
        )

        if st.session_state.get("pdf_extraction_engine") == "docling":
            st.caption("已使用 Docling 识别简历版面、栏目与多栏结构。")
        elif st.session_state.get("pdf_extraction_engine") == "pypdf":
            st.caption("已使用基础 PDF 文本解析；复杂多栏简历建议安装 Docling 增强解析。")

        if st.session_state.parsed_text:
            with st.expander("查看 PDF 提取文字"):
                st.text(st.session_state.parsed_text)

        with st.expander("🧹 本机简历文件（清理规则）"):
            report = _uploads_report()
            st.caption(report.describe())
            st.caption(
                f"规则：与某条研究记录对应的简历会被保留；已无对应记录的简历在 "
                f"{DEFAULT_RETENTION_DAYS} 天后自动清理。不是「UUID.pdf」命名的文件不会被系统碰。"
            )
            if report.orphan_count:
                if st.button(f"立即清理这 {report.orphan_count} 份无对应记录的简历"):
                    removed = cleanup_orphans(
                        settings.uploads_dir, _known_run_ids(), older_than_days=0
                    )
                    freed = report.orphan_bytes / 1024
                    st.success(f"已清理 {len(removed)} 份简历，释放 {freed:.0f} KB。")
                    st.rerun()
            else:
                st.caption("目前没有需要清理的文件。")


    with resume_right:
        st.markdown('<div class="section-note">VERIFIED STUDENT FACTS / 可用于匹配的事实</div>',
                    unsafe_allow_html=True)
        student = st.session_state.student
        edited_student = None
        confirmed_fact_ids: set[str] = set()
        if student is None:
            st.info("请先上传并解析简历。解析失败时，可在后续版本中完全手动填写。")
        else:
            st.caption("这里保留的每一项都会用于后续匹配；不准确或不想展示的内容直接删除即可。")
            grouped_facts: dict[str, list[dict]] = {field: [] for field in FACT_FIELDS}
            for row in st.session_state.student_fact_editor:
                field = str(row.get("field") or "skill")
                value = str(row.get("value") or "").strip()
                if field in grouped_facts and (
                    value or st.session_state.get("editing_fact_id") == row.get("id")
                ):
                    grouped_facts[field].append(row)

            for pair_start in range(0, len(FACT_FIELD_LABELS), 2):
                columns = st.columns(2, gap="small")
                for column, (field, label) in zip(
                    columns,
                    list(FACT_FIELD_LABELS.items())[pair_start : pair_start + 2],
                    strict=False,
                ):
                    rows = grouped_facts[field]
                    with column, st.container(border=True):
                        st.markdown(f"**{label} · {len(rows)}**")
                        with st.container(height=150, border=False):
                            if not rows:
                                st.caption("暂未提取")
                            # 沿用上一版的紧凑标签布局；标签右侧的 × 就是删除。
                            for row_start in range(0, len(rows), 3):
                                for item_column, row in zip(
                                    st.columns(3, gap="small"),
                                    rows[row_start : row_start + 3],
                                    strict=False,
                                ):
                                    row_id = str(row.get("id") or uuid.uuid4().hex)
                                    with item_column:
                                        if st.session_state.get("editing_fact_id") == row_id:
                                            row["value"] = st.text_input(
                                                "补充内容",
                                                value=str(row.get("value") or ""),
                                                key=f"fact_value_{row_id}",
                                                label_visibility="collapsed",
                                            )
                                            st.button(
                                                "保存", key=f"fact_save_{row_id}",
                                                on_click=_finish_fact_edit,
                                            )
                                        else:
                                            st.button(
                                                f"{str(row.get('value') or '')}  ×",
                                                key=f"fact_del_{row_id}",
                                                on_click=_remove_fact,
                                                args=(row_id,),
                                                help="点击右侧 × 删除此项",
                                            )
                        spacer, add = st.columns([0.55, 0.45])
                        with add:
                            st.button(
                                f"＋ 添加{label}", key=f"fact_add_{field}",
                                on_click=_add_fact, args=(field,), use_container_width=True,
                            )
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

    if st.button("🆕 开始新导师", use_container_width=True):
        _reset_professor()
        st.rerun()
    col_homepage, col_parse = st.columns([0.76, 0.24], gap="small")
    with col_homepage:
        homepage_url = st.text_input(
            "导师主页链接（可选，用于自动解析）",
            key="prof_homepage",
            label_visibility="collapsed",
            placeholder="粘贴导师主页或学校个人主页链接",
        )
    with col_parse:
        parse_homepage = st.button("🔍 解析主页", use_container_width=True)
    if parse_homepage:
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
                    st.session_state["_run_state"] = None
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
        st.caption("请核对该邮箱是否为目标导师的公开工作邮箱；系统不会向此邮箱自动发送邮件。")
        declared_interests_text = st.text_area(
            "官网公开研究方向（可选，用逗号或分号分隔）", key="prof_interests"
        )
    identity_confirmed = st.checkbox("我已核对并确认以上信息属于目标导师",
                                     key="identity_confirmed")

    homepage_profile = st.session_state.get("homepage_profile")
    _render_identity_anchor(homepage_profile)
    _render_local_advisor_supplement()
    if st.session_state.get("field_report") is not None:
        _render_field_report(st.session_state["field_report"])
    if not homepage_profile:
        st.caption("姓名和学校填好后即可继续；官方主页可显著降低同名风险。")
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
    correction_needed = bool(
        st.session_state.get("_show_research_correction")
        or selected_mode == "en"
        or st.session_state.get("_research_confirm")
        or (
            st.session_state.get("_research_sources")
            and not st.session_state.get("candidate_papers")
        )
    )
    if not correction_needed and st.button(
        "结果不准确？补充纠错信息",
        help="英文名、导师曾任职单位和代表论文只在自动检索不准确时需要。",
    ):
        st.session_state["_show_research_correction"] = True
        st.rerun()

    if correction_needed:
        with st.container(border=True):
            correction_head, correction_close = st.columns([0.8, 0.2])
            with correction_head:
                st.markdown("**补充检索线索**")
                st.caption("只填写你确定的信息；留空不会影响一键研究。")
            with correction_close:
                if st.button("收起补充线索"):
                    st.session_state.pop("_show_research_correction", None)
                    st.rerun()
            english_name = st.text_input(
                "导师英文名（英文检索时使用，可选）", key="prof_english_name"
            )
            search_institution = st.text_input(
                "检索用机构（可选；导师有多个单位时填另一所，如清华大学）",
                key="prof_search_institution",
            )
            seed_titles_text = st.text_area(
                "代表论文标题（可选，一行一个；作者名查不到时按标题兜底检索）",
                key="prof_seed_titles",
            )
    else:
        english_name = str(st.session_state.get("prof_english_name") or "")
        search_institution = str(st.session_state.get("prof_search_institution") or "")
        seed_titles_text = str(st.session_state.get("prof_seed_titles") or "")
    seed_titles = [t.strip() for t in seed_titles_text.splitlines() if t.strip()]
    faculty_seed = st.session_state.get("_faculty_seed_titles", [])
    if faculty_seed:
        seed_titles = list(dict.fromkeys([*faculty_seed, *seed_titles]))
    faculty_directions = st.session_state.get("_faculty_directions", [])
    search_name = english_name.strip() if selected_mode == "en" else professor_name.strip()

    if "candidate_papers" not in st.session_state:
        st.session_state.candidate_papers = []


    def _trigger_search() -> None:
        if not professor_name.strip() or not institution.strip():
            st.error("请先完善导师档案：至少填写导师姓名和学校/单位。")
            return
        if selected_mode == "zh" and not settings.wanfang_app_key:
            st.error(
                "「仅中文」需要一个中文库的访问 Key（在 .env 里配置 WANFANG_APP_KEY）。"
                "不改配置的话，把「检索方式」换成「自动」，就能直接用免 Key 的国际学术库检索。"
            )
            return
        if selected_mode == "en" and not english_name.strip():
            st.error("仅英文检索需要填写「导师英文名」")
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
                    department,
                    faculty_directions,
                    selected_discipline,
                )
        except Exception as exc:  # noqa: BLE001 - 检索失败降级到手动录入
            st.error(f"检索失败（不影响手动录入）：{exc}")
            st.session_state.candidate_papers = []


    research_completed = bool(st.session_state.get("_research_sources"))
    research_button_label = (
        "✓ 研究已完成 · 重新研究" if research_completed else "🔎 开始一键研究"
    )
    if st.button(
        research_button_label,
        type="secondary" if research_completed else "primary",
        key="research_start",
    ):
        if not professor_name.strip() or not institution.strip():
            st.error("请先完善导师档案：至少填写导师姓名和学校/单位。")
        else:
            _trigger_search()
            st.rerun()

    if st.session_state.get("_research_sources"):
        st.caption(
            f"上次检索：{st.session_state['_research_sources']}"
            f"（学科：{st.session_state.get('_research_discipline') or '通用'}）"
        )
        used_clues = st.session_state.get("_research_clues") or []
        if used_clues:
            st.caption("本次使用的补充线索：" + "；".join(used_clues))

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
            state = RunState.from_dict(st.session_state.get("_run_state"))
            state.grant(msg)
            st.session_state["_run_state"] = state.to_dict()
            _trigger_search()
        if c2.button("🔁 去掉学校重新检索", use_container_width=True):
            st.session_state.pop("_research_confirm", None)
            st.session_state["_run_state"] = None
            _run_research(
                search_name, "", selected_mode, english_name.strip(),
                search_institution.strip(), seed_titles, department, faculty_directions,
                selected_discipline,
            )
        if c3.button("📋 全部保留，我手动核对", use_container_width=True):
            for paper in papers:
                paper["belongs"] = True
                paper["needs_review"] = False
            st.session_state.pop("_research_confirm", None)
            st.session_state["_run_state"] = None
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
                    st.session_state["_run_state"] = None

    # Agent 运行轨迹：跑过一次检索后才出现（没跑过不显示空面板）
    _render_agent_trace(st.session_state.get("_agent_trace"))

    papers = st.session_state.candidate_papers
    paper_values: list[dict] = []
    if not papers and st.session_state.get("_research_sources"):
        _render_empty_result_guidance()
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
        col_all, col_homonym, col_none, _ = st.columns([0.2, 0.2, 0.16, 0.44])
        if col_all.button("全选全部候选", use_container_width=True):
            _set_all_candidates(True)
        if col_homonym.button("全选疑似同名", use_container_width=True):
            for index in homonym:
                papers[index]["user_confirmed"] = True
                st.session_state[f"cand_paper_{index}"] = True
        if col_none.button("全部取消", use_container_width=True):
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

        workspace_list, workspace_detail = st.tabs([
            "候选论文", "论文证据详情"
        ])
        with workspace_list:
            st.markdown('<div class="section-note">CANDIDATE LIST / 候选论文</div>',
                        unsafe_allow_html=True)
            tab_match, tab_review, tab_homonym = st.tabs([
                f"匹配候选 {len(normal)}", f"需审核 {len(review)}", f"疑似同名 {len(homonym)}"
            ])
            for tab, indices in ((tab_match, normal), (tab_review, review),
                                 (tab_homonym, homonym)):
                with tab:
                    with st.container(height=480, border=False):
                        if not indices:
                            st.caption("这一类暂无候选论文。")
                        for i in indices:
                            with st.container(border=True):
                                _render_candidate(i, papers[i])
                                if papers[i].get("disambig_reason"):
                                    st.caption(papers[i]["disambig_reason"])
        with workspace_detail:
            st.markdown('<div class="section-note">EVIDENCE INSPECTOR / 论文证据详情</div>',
                        unsafe_allow_html=True)
            chosen_index = st.selectbox(
                "选择论文查看证据", list(range(len(papers))),
                format_func=lambda i: f"{i + 1:02d} · {papers[i].get('title') or '未命名论文'}",
                key="inspected_paper_index",
            )
            selected_paper = papers[chosen_index]
            with st.container(height=480, border=True):
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

    with st.expander("✍️ 手动补录论文（检索不到时使用）", expanded=True):
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

    has_confirmed_paper = any(values.get("user_confirmed") for values in paper_values)
    if has_confirmed_paper:
        paper_read_confirmed = st.checkbox(
            "我已阅读并理解至少一篇已确认论文（可选，允许邮件提及）",
            key="paper_read_confirmed",
            help="仅在你确实读过论文时勾选；勾选后邮件才能写“我阅读了某篇论文”。",
        )
    else:
        paper_read_confirmed = False
        st.caption("先确认至少一篇论文；确认后可选择是否允许邮件提及你已阅读它。")

    existing_archive = _existing_archive_for_professor(
        st.session_state.repo,
        name=professor_name,
        institution=institution,
        department=department,
        current_run_id=st.session_state.run_id,
    ) if professor_name.strip() and institution.strip() else None
    if existing_archive is not None:
        st.warning(
            f"已找到「{existing_archive['记录']}」的本地研究档案。"
            "请决定本次结果是更新这份档案，还是作为一份独立研究保留。"
        )
        duplicate_action = st.radio(
            "同一导师再次研究时",
            ["更新已有档案（推荐）", "保留为新研究"],
            key="duplicate_archive_action",
            horizontal=True,
        )
    else:
        duplicate_action = "保留为新研究"

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
            if existing_archive is not None and duplicate_action.startswith("更新"):
                # 用户明确选择覆盖：删除旧报告与当前空白草稿，再创建同一导师的新版本。
                st.session_state.repo.delete_run(existing_archive["run_id"])
                st.session_state["session_run_ids"] = [
                    item
                    for item in st.session_state.get("session_run_ids", [])
                    if item != existing_archive["run_id"]
                ]
                _replace_current_with_new_run()
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
                            if k not in ("institution", "venue")
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
                    # 关键词没命中时用向量补一层"用词不同但意思接近"的交集；
                    # 没配向量后端时它是离线档，行为与从前几乎一致
                    embedder=_embedder(),
                )
            name = " · ".join(
                part.strip()
                for part in (institution, department, professor_name)
                if part and part.strip()
            )
            st.session_state.repo.set_run_name(st.session_state.run_id, name)
            st.session_state.active_page = "report"
            st.rerun()
        except (ValidationError, ValueError) as exc:
            st.error(f"请检查输入：{exc}")
        except Exception as exc:  # noqa: BLE001 - UI 必须显示可操作错误
            st.error(f"生成失败：{exc}")
