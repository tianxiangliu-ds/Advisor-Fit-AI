"""导师身份核对：用多条线索判断「这个人到底是不是我们要找的那位导师」。

为什么要做：同名同姓太常见。以前的流程是「解析到什么就认什么」，没有真正的判断。
现在系统会先自己核对几条线索，再决定是直接放行、还是请用户确认：

    学校邮箱域名      邮箱域名像不像高校邮箱
    所属机构          官网写的学校和用户填的是不是同一家
    院系              官网写的院系和用户填的是不是一致
    论文机构          论文里的单位是否包含目标学校
    研究方向          论文主题与已知方向是否有交集
    学者编号          有没有 ORCID / OpenAlex 这类唯一编号

判断规则（确定性、可解释，不依赖 LLM）：
- 有冲突且无任何支持 → REJECTED（明显不是同一个人）
- 有冲突但有支持   → REVIEW_REQUIRED（请用户确认）
- 无冲突且支持 ≥2  → CONFIRMED
- 其余              → REVIEW_REQUIRED
"""

from __future__ import annotations

from typing import Any

from advisor_fit.ingest.profile_fallback import looks_like_school_email
from advisor_fit.models.resolution import IdentitySignal, ResolutionResult, ResolutionStatus

# 内部统一用的键名：匹配值可能是「疑似同一单位」的其它写法
_INSTITUTION_ALIASES = ("大学", "学院", "研究院", "研究所", "学校")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list | tuple | set):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def institutions_agree(left: str, right: str) -> bool | None:
    """两家单位是不是同一家。任一侧为空则返回 None（无法判断）。"""
    a, b = _text(left), _text(right)
    if not a or not b:
        return None
    if a in b or b in a:
        return True
    # 去掉「大学/学院」等后缀再比一次，容忍「武汉大学」与「武汉大学信息管理学院」之外的写法
    a_core = a
    b_core = b
    for suffix in _INSTITUTION_ALIASES:
        a_core = a_core.replace(suffix, "")
        b_core = b_core.replace(suffix, "")
    if a_core and b_core and (a_core in b_core or b_core in a_core):
        return True
    return False


def _signal(key: str, label: str, matched: bool | None, detail: str = "") -> IdentitySignal:
    return IdentitySignal(key=key, label=label, matched=matched, detail=detail)


def assess_identity(
    *,
    name: str,
    institution: str = "",
    email: str = "",
    department: str = "",
    homepage_profile: Any = None,
    known_directions: list[str] | None = None,
    paper_institutions: list[str] | None = None,
    paper_topics: list[str] | None = None,
    scholar_ids: dict[str, str] | None = None,
) -> ResolutionResult:
    """按多锚点打分给出身份判断；判断不了就请用户确认，不擅自认定。"""
    signals: list[IdentitySignal] = []

    profile_email = _text(getattr(homepage_profile, "email", "")) if homepage_profile else ""
    profile_institution = (
        _text(getattr(homepage_profile, "institution", "")) if homepage_profile else ""
    )
    profile_department = (
        _text(getattr(homepage_profile, "department", "")) if homepage_profile else ""
    )

    # 1) 邮箱
    candidate_email = _text(email) or profile_email
    if candidate_email:
        looks_school = looks_like_school_email(candidate_email)
        signals.append(
            _signal(
                "email_domain",
                "学校邮箱域名",
                looks_school,
                f"{candidate_email} 的域名{'像' if looks_school else '不像'}高校邮箱",
            )
        )

    # 2) 机构
    agree = institutions_agree(profile_institution, institution)
    if agree is not None:
        signals.append(
            _signal(
                "institution",
                "所属机构",
                agree,
                f"官网写「{profile_institution}」，你填「{institution}」",
            )
        )

    # 3) 院系
    if profile_department and _text(department):
        same_department = (
            profile_department in _text(department) or _text(department) in profile_department
        )
        signals.append(
            _signal(
                "department",
                "院系",
                same_department,
                f"官网写「{profile_department}」，你填「{department}」",
            )
        )

    # 4) 论文机构
    paper_institutions = [item for item in (paper_institutions or []) if _text(item)]
    if paper_institutions and _text(institution):
        matches = [item for item in paper_institutions if institutions_agree(item, institution)]
        signals.append(
            _signal(
                "paper_institution",
                "论文所属机构",
                bool(matches),
                f"{len(matches)}/{len(paper_institutions)} 篇论文的单位与目标学校一致",
            )
        )

    # 5) 研究方向
    directions = [item for item in (known_directions or []) if _text(item)]
    topics = [item for item in (paper_topics or []) if _text(item)]
    if directions and topics:
        overlap = [topic for topic in topics if any(topic in d or d in topic for d in directions)]
        signals.append(
            _signal(
                "topic_continuity",
                "研究方向连贯",
                bool(overlap),
                f"论文主题与已知方向重合 {len(overlap)} 项",
            )
        )

    # 6) 学者唯一编号
    scholar_ids = scholar_ids or {}
    if any(_text(value) for value in scholar_ids.values()):
        provided = "、".join(key for key, value in scholar_ids.items() if _text(value))
        signals.append(_signal("scholar_id", "学者唯一编号", True, f"已提供：{provided}"))

    supports = [item for item in signals if item.matched is True]
    conflicts = [item for item in signals if item.matched is False]

    if conflicts and not supports:
        status = ResolutionStatus.REJECTED
    elif conflicts:
        status = ResolutionStatus.REVIEW_REQUIRED
    elif len(supports) >= 2:
        status = ResolutionStatus.CONFIRMED
    else:
        status = ResolutionStatus.REVIEW_REQUIRED

    reasons = [f"{item.label}：{item.detail}" for item in supports]
    conflict_notes = [f"{item.label}：{item.detail}" for item in conflicts]
    if not signals:
        reasons.append("没有可用于核对的线索（邮箱、机构、论文等都没提供）")

    return ResolutionResult(
        status=status,
        selected_author_id=_text(name) or None,
        reasons=reasons,
        conflicts=conflict_notes,
        score=len(supports) - len(conflicts),
        signals=signals,
    )


STATUS_LABELS: dict[str, str] = {
    ResolutionStatus.CONFIRMED.value: "✅ 已核对一致",
    ResolutionStatus.REVIEW_REQUIRED.value: "🟡 需要你确认",
    ResolutionStatus.REJECTED.value: "🔴 明显对不上",
}
