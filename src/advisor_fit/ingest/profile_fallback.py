"""导师信息的字段级补齐。

要解决的问题：导师官网的完整度差别极大，只填「姓名 + 学校」时，往往有一半字段拿不到。
以前的流程是"抓不到就报错、剩下全靠手填"，现在改成：

    manual（你填的） → 官网结构化信息（JSON-LD / meta）
      → 官网正文（AI 抽取） → 官网正文（规则提取，无 LLM 也能用）
      → 都没有就是 UNKNOWN，界面显示"未知"，**不阻塞后续流程**

每个字段都记下"值 + 状态 + 从哪来"，方便你核对，也守住"可追溯"这条红线。
"""

from __future__ import annotations

import json
import re
from typing import Any

from advisor_fit.models.provenance import FieldProvenance, FieldReport, FieldStatus

FIELD_LABELS: dict[str, str] = {
    "name": "姓名",
    "institution": "学校/单位",
    "department": "院系",
    "title": "职称",
    "email": "邮箱",
    "declared_interests": "研究方向",
}

# 只有这两个字段缺失才需要拦住用户；其余缺了照样往下走
REQUIRED_FIELDS: tuple[str, ...] = ("name", "institution")

SOURCE_MANUAL = "你手动填写"
SOURCE_STRUCTURED = "官网结构化信息"
SOURCE_AI = "官网正文（AI 抽取）"
SOURCE_RULES = "官网正文（规则提取）"
SOURCE_UNKNOWN = "未获取"

_LD_JSON_RE = re.compile(
    r"""<script[^>]*type=["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
_META_RE = re.compile(r"<meta\s+([^>]+?)/?>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([a-zA-Z:-]+)\s*=\s*["']([^"']*)["']""")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DEPARTMENT_RE = re.compile(r"[一-龥A-Za-z]{2,}(?:学院|研究院|研究中心|实验室|学系|系)")
_TITLE_WORDS = (
    "助理教授",
    "特聘教授",
    "副研究员",
    "副教授",
    "研究员",
    "讲师",
    "教授",
    "博士后",
)
_INTEREST_MARKERS = ("研究方向", "研究领域", "主要研究", "研究兴趣", "科研方向")
_SCHOOL_EMAIL_SUFFIXES = (
    ".edu.cn",
    ".edu",
    ".ac.cn",
    ".ac.uk",
    ".ac.jp",
    ".edu.hk",
    ".edu.tw",
    ".edu.mo",
)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list | tuple | set):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _first_str(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = _first_str(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in ("name", "@value", "value"):
            if key in value:
                text = _first_str(value[key])
                if text:
                    return text
        return ""
    return _clean(value)


def looks_like_school_email(email: str) -> bool:
    """邮箱域名是否像高校邮箱。不像就降到「待核对」，不当作已确认。"""
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    return any(domain.endswith(suffix) for suffix in _SCHOOL_EMAIL_SUFFIXES)


# -- 来源一：官网结构化信息（JSON-LD / meta）-----------------------------------


def _iter_person_nodes(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _iter_person_nodes(item)
    elif isinstance(node, dict):
        types = node.get("@type")
        is_person = types == "Person" or (isinstance(types, list) and "Person" in types)
        if is_person:
            yield node
        for key in ("@graph", "author", "mainEntity"):
            if key in node:
                yield from _iter_person_nodes(node[key])


def _meta_map(html: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for tag in _META_RE.findall(html):
        attrs = {key.lower(): value for key, value in _ATTR_RE.findall(tag)}
        name = (attrs.get("name") or attrs.get("property") or "").lower()
        content = attrs.get("content", "").strip()
        if name and content and name not in meta:
            meta[name] = content
    return meta


def extract_structured_fields(html: str) -> dict[str, str]:
    """只读官方页面里的结构化标记（JSON-LD 的 Person、meta 标签），不做猜测。"""
    fields: dict[str, str] = {}

    def _set(key: str, value: Any) -> None:
        text = _clean(value)
        if text and not fields.get(key):
            fields[key] = text

    for match in _LD_JSON_RE.finditer(html):
        try:
            data = json.loads(match.group(1).strip())
        except (ValueError, TypeError):
            continue
        for person in _iter_person_nodes(data):
            _set("name", person.get("name"))
            _set("title", person.get("jobTitle"))
            _set(
                "email",
                _clean(person.get("email")).replace("mailto:", ""),
            )
            affiliation = person.get("affiliation") or person.get("worksFor")
            _set("institution", _first_str(affiliation))
            if isinstance(affiliation, dict):
                _set("department", _first_str(affiliation.get("department")))
            _set("declared_interests", person.get("knowsAbout"))

    meta = _meta_map(html)
    _set("name", meta.get("author"))
    if not fields.get("name"):
        title = meta.get("og:title") or meta.get("title") or ""
        _set("name", _name_from_title(title))
    _set("declared_interests", meta.get("keywords"))

    return fields


def _name_from_title(title: str) -> str:
    """从「陆伟 - 武汉大学信息管理学院」这类标题里取姓名。"""
    for separator in ("-", "|", "_", "—", "–"):
        if separator in title:
            head = title.split(separator, 1)[0].strip()
            if 2 <= len(head) <= 6:
                return head
    return ""


# -- 来源二：官网正文（规则提取，无 LLM 也能用）--------------------------------


def _department_from_text(text: str) -> str:
    """从正文里找院系。若匹配到的是「XX大学XX学院」，剥掉学校前缀只留院系。"""
    match = _DEPARTMENT_RE.search(text)
    if match is None:
        return ""
    name = match.group(0)
    stripped = re.sub(r"^.{2,}?(?:大学|学校)", "", name)
    return stripped or name


def extract_page_fields(text: str) -> dict[str, str]:
    """不依赖 LLM 的字段提取：邮箱、院系、职称、研究方向。"""
    fields: dict[str, str] = {}

    emails = _EMAIL_RE.findall(text)
    if emails:
        fields["email"] = emails[0]

    department = _department_from_text(text)
    if department:
        fields["department"] = department

    ordered_titles = sorted(_TITLE_WORDS, key=len, reverse=True)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "declared_interests" not in fields:
            for marker in _INTEREST_MARKERS:
                if line.startswith(marker):
                    value = line[len(marker) :].lstrip("：: 　").strip()
                    if value:
                        fields["declared_interests"] = value
                    break
        if "title" not in fields and len(line) <= 40:
            for word in ordered_titles:
                if word in line:
                    fields["title"] = word
                    break

    return {key: value for key, value in fields.items() if value}


def _profile_to_fields(profile: Any) -> dict[str, str]:
    """把主页解析结果（HomepageProfile）转成字段字典。"""
    return {
        "name": _clean(getattr(profile, "name", "")),
        "institution": _clean(getattr(profile, "institution", "")),
        "department": _clean(getattr(profile, "department", "")),
        "title": _clean(getattr(profile, "title", "")),
        "email": _clean(getattr(profile, "email", "")),
        "declared_interests": _clean(getattr(profile, "declared_interests", "")),
    }


# -- 合并 ---------------------------------------------------------------------


def build_field_report(
    *,
    manual: dict[str, Any] | None = None,
    homepage_html: str = "",
    page_text: str = "",
    llm_profile: Any = None,
    source_url: str | None = None,
) -> FieldReport:
    """按来源优先级逐字段补齐；缺的字段标 UNKNOWN，**不阻断流程**。"""
    manual_fields = {key: _clean(value) for key, value in (manual or {}).items()}
    structured = extract_structured_fields(homepage_html) if homepage_html else {}
    ai_fields = _profile_to_fields(llm_profile) if llm_profile is not None else {}
    rule_fields = extract_page_fields(page_text) if page_text else {}

    candidates: tuple[tuple[str, dict[str, str], FieldStatus], ...] = (
        (SOURCE_MANUAL, manual_fields, FieldStatus.CONFIRMED),
        (SOURCE_STRUCTURED, structured, FieldStatus.CONFIRMED),
        (SOURCE_AI, ai_fields, FieldStatus.CONFIRMED),
        (SOURCE_RULES, rule_fields, FieldStatus.CONFIRMED),
    )

    fields: dict[str, FieldProvenance] = {}
    for field, label in FIELD_LABELS.items():
        entry = FieldProvenance(field=field, label=label, source=SOURCE_UNKNOWN)
        for source_label, values, status in candidates:
            value = values.get(field, "")
            if value:
                entry = FieldProvenance(
                    field=field,
                    label=label,
                    value=value,
                    status=status,
                    source=source_label,
                    source_url=source_url,
                )
                break
        fields[field] = entry

    notes: list[str] = []
    email = fields["email"]
    if email.known and not looks_like_school_email(email.value):
        fields["email"] = email.model_copy(
            update={
                "status": FieldStatus.INFERRED,
                "note": "邮箱域名不像学校邮箱，请核对是否为导师本人邮箱",
            }
        )
        notes.append("邮箱标为「待核对」：域名不像学校邮箱")

    return FieldReport(fields=fields, source_url=source_url, notes=notes)
