"""按研究方向找候选导师：从本地导师库粗筛 + 可解释排序。

解决的问题：用户真实的起点常常是「我这个方向该找谁」，而不是已经知道导师名字。
这一步只在**本地导师库**里做粗筛（零网络请求），给出「几位候选 + 为什么推荐他」，
再由用户挑人进入逐个深度研究——不自动替用户决定谁合适。

排序规则（全部可解释、能摆给用户看）：

1. **命中权重**：研究方向/研究领域命中 3 分、院系或代表论文命中 2 分、简介正文命中 1 分。
   同一个词在同一字段只算一次，避免"一个词刷满分数"。
2. **资料完整度**（0–1）作为同分时的次级依据：资料越全，越值得先看。
3. 最后按姓名稳定排序，保证同样输入每次结果一致。

字段权重只用于排序，不会写进报告当结论——匹配与否最终仍由用户判断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from advisor_fit.models.advisor import Advisor

# 参与匹配的字段 -> 权重与中文名（展示"命中了哪里"时要用）
FIELD_WEIGHTS: dict[str, int] = {
    "research_directions": 3,
    "research_areas": 3,
    "department": 2,
    "publications": 2,
    "profile_text": 1,
}

FIELD_LABELS: dict[str, str] = {
    "research_directions": "研究方向",
    "research_areas": "研究领域",
    "department": "院系",
    "publications": "代表论文",
    "profile_text": "简介正文",
}

# 资料完整度看这几个字段：有方向、有职称、有邮箱、有主页
COMPLETENESS_FIELDS: tuple[str, ...] = (
    "research_directions",
    "title",
    "email",
    "homepage_url",
)

# 展示"字段核对"时的中文名（面向非技术用户，不能漏出英文字段名）
_COMPLETENESS_LABELS: dict[str, str] = {
    "research_directions": "研究方向",
    "title": "职称",
    "email": "邮箱",
    "homepage_url": "主页",
}

_TERM_SPLIT = re.compile(r"[,，;；、|\n\r]+")
_UNSAFE_LIKE = re.compile(r"[%_\\]")


def split_terms(text: str | None) -> list[str]:
    """把用户输入的一串方向词切成关键词列表（支持中英文逗号、分号、顿号、换行）。"""
    if not text:
        return []
    terms: list[str] = []
    for raw in _TERM_SPLIT.split(str(text)):
        term = raw.strip()
        if term and term not in terms:
            terms.append(term)
    return terms


def safe_like_terms(terms: list[str]) -> list[str]:
    """清掉 LIKE 的通配符，避免用户输入 `%` 时把整库捞出来。"""
    cleaned: list[str] = []
    for term in terms:
        value = _UNSAFE_LIKE.sub("", term).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _field_values(advisor: Advisor, field: str) -> list[str]:
    value = getattr(advisor, field, None)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return []


@dataclass(frozen=True)
class MatchReason:
    term: str
    field: str
    weight: int

    @property
    def label(self) -> str:
        return FIELD_LABELS.get(self.field, self.field)


def match_reasons(advisor: Advisor, terms: list[str]) -> tuple[MatchReason, ...]:
    """列出这位导师命中了哪些关键词、命中的是哪个字段。

    同一个词在同一个字段只计一次：导师的研究方向常写成
    `["知识图谱", "知识图谱与语义计算"]`，不去重的话同一个词会被算两遍，
    分数就失去可比性了。
    """
    reasons: list[MatchReason] = []
    seen: set[tuple[str, str]] = set()
    lowered = {term.lower(): term for term in terms if term}
    for field, weight in FIELD_WEIGHTS.items():
        for value in _field_values(advisor, field):
            haystack = value.lower()
            for key, original in lowered.items():
                if key not in haystack:
                    continue
                marker = (original, field)
                if marker in seen:
                    continue
                seen.add(marker)
                reasons.append(MatchReason(term=original, field=field, weight=weight))
    return tuple(reasons)


def completeness(advisor: Advisor) -> float:
    """资料完整度 0–1：这几个字段越全，越值得优先看。"""
    filled = 0
    for field in COMPLETENESS_FIELDS:
        value = getattr(advisor, field, None)
        if isinstance(value, list):
            filled += 1 if value else 0
        else:
            filled += 1 if str(value or "").strip() else 0
    return filled / len(COMPLETENESS_FIELDS)


def completeness_text(advisor: Advisor) -> str:
    """把完整度说成人话：研究方向 ✓　职称 ✓　邮箱 ✗　主页 ✓。"""
    parts: list[str] = []
    for field in COMPLETENESS_FIELDS:
        value = getattr(advisor, field, None)
        ok = bool(value) if isinstance(value, list) else bool(str(value or "").strip())
        label = _COMPLETENESS_LABELS.get(field, FIELD_LABELS.get(field, field))
        parts.append(f"{label} {'✓' if ok else '✗'}")
    return "　".join(parts)


@dataclass(frozen=True)
class DirectionHit:
    advisor: Advisor
    score: float
    match_score: int
    reasons: tuple[MatchReason, ...]
    completeness: float

    @property
    def reason_text(self) -> str:
        """一行说清"为什么推荐他"。"""
        if not self.reasons:
            return "无匹配理由"
        grouped: dict[str, list[str]] = {}
        for reason in self.reasons:
            grouped.setdefault(reason.label, [])
            if reason.term not in grouped[reason.label]:
                grouped[reason.label].append(reason.term)
        return "；".join(
            f"{label}命中 {'、'.join(values)}" for label, values in grouped.items()
        )

    def to_row(self) -> dict:
        """给表格/并排比较用的扁平数据。"""
        advisor = self.advisor
        return {
            "姓名": advisor.name,
            "学校": advisor.university,
            "院系": advisor.department,
            "职称": advisor.title or "未知",
            "研究方向": "、".join(advisor.research_directions) or "未采集",
            "匹配理由": self.reason_text,
            "资料完整度": f"{self.completeness:.0%}",
            "主页": advisor.homepage_url or "未采集",
            "邮箱": advisor.email or "未采集",
            "来源": "、".join(advisor.sources) or "未标注",
        }


def rank_candidates(
    advisors: list[Advisor],
    terms: list[str],
    *,
    universities: list[str] | None = None,
    limit: int = 20,
    min_match_score: int = 2,
) -> list[DirectionHit]:
    """按关键词给候选导师打分排序。

    min_match_score 默认 2：也就是至少要在「院系/代表论文」这类字段命中一次，
    只在简介正文里出现过关键词的不算——那多半是噪声（页面里恰好提到该词）。
    """
    wanted = {university.strip() for university in (universities or []) if university.strip()}
    scored: list[DirectionHit] = []
    for advisor in advisors:
        if wanted and advisor.university not in wanted:
            continue
        reasons = match_reasons(advisor, terms)
        match_score = sum(reason.weight for reason in reasons)
        if match_score < min_match_score:
            continue
        score = match_score + completeness(advisor)
        scored.append(
            DirectionHit(
                advisor=advisor,
                score=score,
                match_score=match_score,
                reasons=reasons,
                completeness=completeness(advisor),
            )
        )
    scored.sort(
        key=lambda hit: (
            -hit.match_score,
            -hit.completeness,
            hit.advisor.university,
            hit.advisor.name,
        )
    )
    return scored[: max(1, limit)]


def compare_rows(hits: list[DirectionHit]) -> list[dict]:
    """并排比较用的行：每行一位候选，列名固定，方便直接铺成表格。"""
    return [hit.to_row() for hit in hits]
