"""时间敏感的研究主题趋势推断。

规则（确定性，不依赖 LLM）：
- 时间权重：0–2 年 0.60、3 年前 0.25、4–5 年前 0.15；
- 少于 3 篇或只覆盖 1 个年份 → INSUFFICIENT_EVIDENCE；
- 单篇热点论文只算“近期出现”，绝不判为“已转向”。
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from advisor_fit.providers.academic import Work


class Trend(BaseModel):
    topic: str
    status: str = "INSUFFICIENT_EVIDENCE"  # EMERGING | SUSTAINED | DECLINING
    evidence_ids: list[str] = []
    window: str = ""


def _weight(age: int) -> float:
    if age <= 2:
        return 0.60
    if age == 3:
        return 0.25
    return 0.15


def _dominant_topic(works: list[Work]) -> str:
    counts: dict[str, int] = {}
    for work in works:
        for topic in work.topics or []:
            counts[topic] = counts.get(topic, 0) + 1
    return max(counts, key=counts.get) if counts else "未分类"


def infer_trend(works: list[Work], current_year: int, topic: str | None = None) -> Trend:
    dated = [w for w in works if w.year is not None]
    if topic is None:
        topic = _dominant_topic(dated)
    evidence_ids = [f"ev_{w.id}" for w in dated]
    years = {w.year for w in dated}

    if len(dated) < 3 or len(years) < 2:
        return Trend(topic=topic, status="INSUFFICIENT_EVIDENCE", evidence_ids=evidence_ids)

    total = 0.0
    recent = 0.0
    for work in dated:
        weight = _weight(current_year - work.year)
        total += weight
        if current_year - work.year <= 2:
            recent += weight

    ratio = recent / total if total else 0.0
    if ratio >= 0.6:
        status = "EMERGING"
    elif ratio >= 0.3:
        status = "SUSTAINED"
    else:
        status = "DECLINING"

    return Trend(
        topic=topic,
        status=status,
        evidence_ids=evidence_ids,
        window=f"{min(years)}-{max(years)}",
    )


def group_works_by_topic(works: list[Work]) -> dict[str, list[Work]]:
    groups: dict[str, list[Work]] = defaultdict(list)
    for work in works:
        for topic in work.topics or ["未分类"]:
            groups[topic].append(work)
    return dict(groups)
