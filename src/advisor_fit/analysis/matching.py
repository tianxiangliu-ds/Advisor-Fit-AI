"""可解释匹配引擎：分维度、无伪精确百分比。

- 硬门控：学生事实未确认 / 导师身份未确认 / 导师证据不足 → INSUFFICIENT_EVIDENCE；
- 主题匹配：已确认技能命中主题 → STRONG；仅兴趣关键词命中 → PARTIAL（不夸大）；
- 招生信号完全独立于学术匹配，单独输出；
- WORTH_CONTACTING 需要强主题命中 + 中等以上证据覆盖。
"""

from __future__ import annotations

from advisor_fit.models.match import FitLevel, MatchDimension, MatchReport, Recommendation
from advisor_fit.models.professor import ProfessorProfile, RecruitingStatus
from advisor_fit.models.student import StudentProfile

# 常见中英文主题同义词（MVP 用可解释关键词/同义词表，不做 embedding）
_SYNONYMS: dict[str, set[str]] = {
    "检索增强生成": {"rag", "检索增强", "retrieval-augmented generation"},
    "rag": {"检索增强生成", "检索增强", "retrieval-augmented"},
    "机器学习": {"machine learning", "ml"},
    "深度学习": {"deep learning", "dl"},
    "自然语言处理": {"nlp", "自然语言理解"},
    "信息检索": {"information retrieval", "ir", "检索"},
    "大语言模型": {"llm", "大模型", "language model"},
    "llm": {"大语言模型", "大模型", "language model"},
    "计算机视觉": {"cv", "computer vision"},
}


def _terms_match(a: str, b: str) -> bool:
    a_norm = a.strip().lower()
    b_norm = b.strip().lower()
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm or a_norm in b_norm or b_norm in a_norm:
        return True
    a_syn = _SYNONYMS.get(a_norm, set())
    b_syn = _SYNONYMS.get(b_norm, set())
    return bool(a_syn & b_syn) or a_norm in b_syn or b_norm in a_syn


def _professor_topics(professor: ProfessorProfile) -> list[tuple[str, list[str]]]:
    topics: list[tuple[str, list[str]]] = []
    for topic in professor.declared_interests:
        topics.append((topic.topic, topic.evidence_ids))
    for topic in professor.observed_recent_topics:
        topics.append((topic.topic, topic.evidence_ids))
    return topics


def _match_terms(
    terms: list[tuple[str, str]], topics: list[tuple[str, list[str]]]
) -> list[tuple[str, str, str, list[str]]]:
    """返回 (fact_id, 学生词, 命中主题, 主题证据 ids) 列表。"""
    matches: list[tuple[str, str, str, list[str]]] = []
    for fact_id, value in terms:
        for topic, evidence_ids in topics:
            if _terms_match(value, topic):
                matches.append((fact_id, value, topic, evidence_ids))
    return matches


def _insufficient_report(reason: str) -> MatchReport:
    return MatchReport(
        recommendation=Recommendation.INSUFFICIENT_EVIDENCE,
        research_fit=FitLevel.UNKNOWN,
        opportunity_signal="UNKNOWN",
        questions_to_ask=[reason],
        evidence_sufficiency="LOW",
    )


def build_match_report(student: StudentProfile, professor: ProfessorProfile) -> MatchReport:
    draftable = student.draftable_facts()
    if not draftable or not professor.identity_confirmed:
        return _insufficient_report("学生事实未确认或导师身份未确认")

    topics = _professor_topics(professor)
    has_papers = bool(professor.recent_publications)
    if not topics and not has_papers:
        return _insufficient_report("导师证据不足")

    skills = [(f.id, str(f.value)) for f in draftable if f.field == "skill"]
    interests = [(f.id, str(f.value)) for f in draftable if f.field == "interest"]

    skill_matches = _match_terms(skills, topics)
    interest_matches = _match_terms(interests, topics)

    if skill_matches:
        topic_level = FitLevel.STRONG
    elif interest_matches:
        topic_level = FitLevel.PARTIAL
    else:
        topic_level = FitLevel.WEAK

    if professor.observed_recent_topics and professor.declared_interests:
        sufficiency = "HIGH"
    elif topics or has_papers:
        sufficiency = "MEDIUM"
    else:
        sufficiency = "LOW"

    if topic_level == FitLevel.STRONG and sufficiency in ("HIGH", "MEDIUM"):
        recommendation = Recommendation.WORTH_CONTACTING
    elif topic_level in (FitLevel.STRONG, FitLevel.PARTIAL):
        recommendation = Recommendation.LEARN_MORE
    else:
        recommendation = Recommendation.LOW_PRIORITY

    all_matches = [*skill_matches, *interest_matches]
    topic_dim = MatchDimension(
        key="research_topic",
        label="研究方向匹配",
        level=topic_level,
        summary=f"技能命中 {len(skill_matches)} 项、兴趣命中 {len(interest_matches)} 项",
        student_fact_ids=[m[0] for m in all_matches],
        professor_evidence_ids=[ev for m in all_matches for ev in m[3]],
    )
    skill_dim = MatchDimension(
        key="skill",
        label="技能覆盖",
        level=FitLevel.STRONG if skills else FitLevel.WEAK,
        summary=f"已确认技能 {len(skills)} 项",
        student_fact_ids=[fid for fid, _ in skills],
        professor_evidence_ids=[],
    )
    evidence_dim = MatchDimension(
        key="evidence",
        label="证据充分度",
        level=(
            FitLevel.STRONG
            if sufficiency == "HIGH"
            else FitLevel.PARTIAL
            if sufficiency == "MEDIUM"
            else FitLevel.WEAK
        ),
        summary=sufficiency,
        student_fact_ids=[],
        professor_evidence_ids=[],
    )
    dimensions = [topic_dim, skill_dim, evidence_dim]

    gaps = [
        topic
        for topic, _ in topics
        if not any(_terms_match(value, topic) for _, value in [*skills, *interests])
    ]

    questions: list[str] = []
    if professor.recruiting.status == RecruitingStatus.UNKNOWN:
        questions.append("导师招生状态未找到公开声明，建议在邮件中礼貌询问。")

    return MatchReport(
        recommendation=recommendation,
        research_fit=topic_level,
        opportunity_signal=professor.recruiting.status.value,
        dimensions=dimensions,
        strengths=[d for d in dimensions if d.level == FitLevel.STRONG],
        weaknesses=[d for d in dimensions if d.level == FitLevel.WEAK],
        gaps=gaps,
        questions_to_ask=questions,
        evidence_sufficiency=sufficiency,
    )
