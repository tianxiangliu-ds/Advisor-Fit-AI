"""导师名册：从社区开源数据里取出「学校 / 学院 / 导师姓名」，并把评价单独存放。

设计口径（按项目负责人确认）：
- **名册**回答「这个学院有哪些导师」：导师表按 (学校, 学院, 姓名) 去重，不重不漏；
- **评价**回答「学生怎么说」：同一位导师可以有多条评价，单独一张表存，不丢内容；
- 姓名里的括号备注（如「周裕(深圳,千人计划)」）拆到独立的 `note` 列，
  它是有用信息，不是脏数据；
- 同一位导师出现在多个学院是正常现象（挂名/交叉任职），**两个学院各留一条**，
  不做跨学院合并。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from pydantic import BaseModel

# 姓名里的括号备注，例如 周裕(深圳,千人计划)、金小刚(AI)
_NOTE_RE = re.compile(r"[（(]([^）)]*)[）)]")
# 社区数据里混进来的标题前缀，例如 "Re: 郑荣濠"
_PREFIX_RE = re.compile(r"^\s*(?:re|回复|答复)\s*[:：]\s*", re.IGNORECASE)


class RosterEntry(BaseModel):
    """名册里的一位导师（同一位导师在同一学院只出现一次）。"""

    university: str
    department: str = ""
    supervisor: str
    note: str = ""
    school_cate: str = ""


class SupervisorReview(BaseModel):
    """一条学生评价。同一位导师可以有多条。"""

    university: str
    department: str = ""
    supervisor: str
    rate: float | None = None
    summary: str = ""
    detail: str = ""


class RosterPayload(BaseModel):
    entries: list[RosterEntry] = []
    reviews: list[SupervisorReview] = []


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def split_note(supervisor: str) -> tuple[str, str]:
    """把姓名与括号备注拆开：「周裕(深圳,千人计划)」→（「周裕」,「深圳,千人计划」）。"""
    raw = _clean(supervisor)
    notes = [item.strip() for item in _NOTE_RE.findall(raw) if item.strip()]
    name = _NOTE_RE.sub("", raw).strip()
    name = _PREFIX_RE.sub("", name).strip()
    return name or raw, "；".join(notes)


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _iter_items(raw: Any) -> list[dict]:
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
    return [item for item in raw if isinstance(item, dict)]


def parse_roster_payload(raw: Any) -> RosterPayload:
    """把社区数据解析成「名册（去重）+ 评价（保留全部）」。"""
    entries: list[RosterEntry] = []
    reviews: list[SupervisorReview] = []
    seen: set[tuple[str, str, str]] = set()

    for item in _iter_items(raw):
        university = _clean(item.get("university"))
        department = _clean(item.get("department"))
        name, note = split_note(item.get("supervisor"))
        if not university or not name:
            continue

        key = (university, department, name)
        if key not in seen:
            seen.add(key)
            entries.append(
                RosterEntry(
                    university=university,
                    department=department,
                    supervisor=name,
                    note=note,
                    school_cate=_clean(item.get("school_cate")),
                )
            )

        detail = _clean(item.get("description"))
        summary = _clean(item.get("desc"))
        rate = _to_float(item.get("rate"))
        if detail or summary or rate is not None:
            reviews.append(
                SupervisorReview(
                    university=university,
                    department=department,
                    supervisor=name,
                    rate=rate,
                    summary=summary,
                    detail=detail,
                )
            )

    return RosterPayload(entries=entries, reviews=reviews)


def parse_roster(raw: Any) -> list[RosterEntry]:
    """只要名册（去重后的导师列表）。"""
    return parse_roster_payload(raw).entries


def roster_summary(entries: list[RosterEntry]) -> dict[str, int]:
    return {
        "entries": len(entries),
        "universities": len({item.university for item in entries}),
        "departments": len({(item.university, item.department) for item in entries}),
    }


def top_universities(entries: list[RosterEntry], limit: int = 10) -> list[tuple[str, int]]:
    counter = Counter(item.university for item in entries)
    return counter.most_common(limit)
