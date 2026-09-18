"""导师双选 AI 助手 v0.1：人工证据输入版 Streamlit 应用。

流程：CV 本地解析与人工确认 → 手动导师资料 → 手动录入已核实论文
→ 可解释匹配 → 事实锁定邮件 → 导出与完整删除。
"""

from __future__ import annotations

import uuid

import streamlit as st
from pydantic import ValidationError

from advisor_fit.config import settings
from advisor_fit.export.report import export_json, export_markdown
from advisor_fit.ingest.cv import (
    apply_fact_edits,
    build_student_profile,
    delete_uploaded_cv,
    extract_pdf_markdown,
    extract_pdf_text,
)
from advisor_fit.ingest.cv_llm import build_student_profile_llm
from advisor_fit.ingest.manual_professor import (
    ManualPaperInput,
    ManualProfessorInput,
    validate_paper_values,
)
from advisor_fit.llm.provider import NullLLM, build_llm
from advisor_fit.manual_pipeline import run_manual_pipeline
from advisor_fit.providers.dblp import DblpProvider
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


def _reset_run() -> None:
    run_id = st.session_state.get("run_id")
    repo = st.session_state.get("repo")
    if run_id and repo is not None:
        repo.delete_run(run_id)
        delete_uploaded_cv(settings.uploads_dir, run_id)
    for key in ("student", "student_fact_editor", "parsed_text", "result"):
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
            del st.session_state[key]


def _split_terms(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace("；", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _work_to_paper(work) -> dict:
    return {
        "title": work.title,
        "year": work.year,
        "abstract": work.abstract or "（未提供摘要，请手动补充）",
        "source_url": work.source_url or "",
        "source_platform": work.source_platform or "DBLP",
        "keywords": work.topics or [],
        "user_confirmed": False,
    }


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
    st.write(f"研究匹配：**{report.research_fit.value}**")
    st.write(f"建议：**{report.recommendation.value}**")
    st.write(f"证据充分度：{report.evidence_sufficiency}")
    st.write(f"招生机会信号：{report.opportunity_signal}")
    if report.strengths:
        st.write("强项：" + "、".join(item.label for item in report.strengths))
    if report.gaps:
        st.write("待补足方向：" + "、".join(report.gaps))
    if report.questions_to_ask:
        st.write("建议询问：" + "、".join(report.questions_to_ask))

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


st.set_page_config(page_title="导师双选 AI 助手 v0.1", layout="wide")
st.title("导师双选 AI 助手 v0.1")
st.caption("人工核实资料输入 · 本地优先 · 不依赖 OpenAlex · 不自动发送邮件")

for key, default in (
    ("student", None),
    ("student_fact_editor", []),
    ("parsed_text", ""),
    ("result", None),
):
    if key not in st.session_state:
        st.session_state[key] = default
if "repo" not in st.session_state:
    _new_run()


st.header("① 上传并确认简历")
uploaded = st.file_uploader("上传 CV（PDF，仅在本机解析）", type=["pdf"])
student_name = st.text_input("你的姓名（可选，用于邮件落款）")
if st.button("解析 CV", type="primary", disabled=uploaded is None):
    upload_path = settings.uploads_dir / f"{st.session_state.run_id}.pdf"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(uploaded.getvalue())
    parsed = extract_pdf_text(upload_path)
    student = _build_student_profile(upload_path, parsed)
    st.session_state.student = student
    st.session_state.student_fact_editor = _fact_rows(student)
    st.session_state.parsed_text = parsed.text
    if parsed.warnings:
        st.warning("；".join(parsed.warnings))

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
st.caption("本版本不抓取导师网页。请填写你已人工核实的信息与论文摘要。")
left, right = st.columns(2)
with left:
    professor_name = st.text_input("导师姓名（必填）")
    institution = st.text_input("学校/单位（必填）")
    department = st.text_input("院系（可选）")
    professor_title = st.text_input("职称（可选）")
with right:
    homepage_url = st.text_input("官方主页链接（可选）")
    professor_email = st.text_input("导师邮箱（可选）")
    declared_interests_text = st.text_area("官网公开研究方向（可选，用逗号或分号分隔）")
identity_confirmed = st.checkbox("我已核对并确认以上信息属于目标导师")

paper_count = int(st.number_input("录入论文数量", min_value=1, max_value=10, value=1))
paper_values: list[dict] = []
for index in range(paper_count):
    number = index + 1
    with st.expander(f"论文 {number}", expanded=number == 1):
        paper_title = st.text_input("论文标题（必填）", key=f"paper_title_{index}")
        paper_year_text = st.text_input("发表年份（可选）", key=f"paper_year_{index}")
        paper_abstract = st.text_area("论文摘要（必填）", key=f"paper_abstract_{index}")
        paper_url = st.text_input("来源链接（必填）", key=f"paper_url_{index}")
        paper_platform = st.selectbox(
            "来源平台",
            ["DOI/出版社", "知网", "Google Scholar", "学校页面", "其他"],
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

st.markdown("**或从 DBLP 检索候选论文（可选，需联网）**")
st.caption("检索结果仅供参考，必须由你确认归属后才可用；DBLP 主要收录计算机领域论文。")
if "candidate_papers" not in st.session_state:
    st.session_state.candidate_papers = []
if st.button("🔍 检索候选论文（DBLP）"):
    if not professor_name.strip():
        st.error("请先填写导师姓名")
    else:
        try:
            provider = DblpProvider()
            works = provider.search_publications(professor_name)
            st.session_state.candidate_papers = [_work_to_paper(work) for work in works]
            if not works:
                st.info("未检索到候选论文，请检查姓名，或改为手动录入。")
        except Exception as exc:  # noqa: BLE001 - 检索失败必须降级到手动录入
            st.error(f"检索失败（不影响手动录入）：{exc}")
            st.session_state.candidate_papers = []

if st.session_state.candidate_papers:
    st.caption(f"检索到 {len(st.session_state.candidate_papers)} 篇候选论文，勾选确认采用的：")
    for index, cand in enumerate(st.session_state.candidate_papers):
        cand["user_confirmed"] = st.checkbox(
            f"{cand['title']}（{cand['year'] or '年份未知'}）",
            key=f"cand_paper_{index}",
            help=cand["source_url"],
        )
    paper_values.extend(
        [cand for cand in st.session_state.candidate_papers if cand["user_confirmed"]]
    )

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
            papers=[ManualPaperInput(**values) for values in confirmed_papers],
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
            )
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
    if st.button("删除本次数据", type="secondary"):
        _reset_run()
        st.rerun()
