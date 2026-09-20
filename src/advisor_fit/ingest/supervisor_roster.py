"""导师名册：从社区开源数据里**只取**「学校 → 学院 → 导师姓名」三列。

为什么不取评价：用户评价差异极大，只能作辅证。这份数据真正有用的地方在于
它等于一份**免费的导师名单**——先知道"这个学院有哪些导师"，才谈得上去抓谁的资料。

所以解析时刻意丢弃 `rate`、`description` 等字段，只保留可公开核对的三列。
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from pydantic import BaseModel

# 这些字段一律丢弃：评价是主观内容，与本项目"事实优先"的口径冲突
_DROPPED_FIELDS = ("rate", "description", "comment", "school_cate", "评价", "评分")


class RosterEntry(BaseModel):
    university: str
    department: str = ""
    supervisor: str


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_roster(raw: Any) -> list[RosterEntry]:
    """把社区数据解析成名册条目；只保留学校/学院/姓名，丢弃评价与评分。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []

    if isinstance(raw, dict):
        for key in ("data", "items", "results", "comments"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]

    if not isinstance(raw, list):
        return []

    entries: list[RosterEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = RosterEntry(
            university=_clean(item.get("university")),
            department=_clean(item.get("department")),
            supervisor=_clean(item.get("supervisor")),
        )
        if not entry.university or not entry.supervisor:
            continue
        key = (entry.university, entry.department, entry.supervisor)
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries


def dropped_field_names() -> tuple[str, ...]:
    """供界面说明"我们不要哪些字段"用。"""
    return _DROPPED_FIELDS


def roster_summary(entries: list[RosterEntry]) -> dict[str, int]:
    return {
        "entries": len(entries),
        "universities": len({item.university for item in entries}),
        "departments": len({(item.university, item.department) for item in entries}),
    }


def top_universities(entries: list[RosterEntry], limit: int = 10) -> list[tuple[str, int]]:
    counter = Counter(item.university for item in entries)
    return counter.most_common(limit)
