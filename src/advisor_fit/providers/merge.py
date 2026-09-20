"""跨库合并去重：把多个学术库返回的同一篇论文合成一条。

判定顺序（先确定，再模糊）：

1. **DOI 相同** → 一定同一篇（DOI 归一化后比较）。
2. **标题归一化后相同**（去标点、去空格、统一大小写与全半角）→ 同一篇，
   但要求年份相容（相同、任一为空、或相差 ≤ 1 年，容忍在线/正式发表年份差异）。
3. 2 里年份明显冲突 → **不合并**，两条都保留，交给人工判断（宁多勿错）。

合并时"取更全的字段"，不覆盖已知值：

- 摘要取更长的、引用数取更大的、作者取更完整的、研究方向取并集；
- `sources` 记录所有贡献过的库，用于展示"被 N 个库收录"。
"""

from __future__ import annotations

import re
import unicodedata

from advisor_fit.providers.academic import Work

_PUNCT_AND_SPACE = re.compile(r"[\s\-–—_.,;:!?，。；：！？、·'\"“”‘’()（）\[\]【】<>《》/\\|+&]+")
_HTML_TAG = re.compile(r"<[^>]+>")
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:")


def normalize_doi(doi: str | None) -> str:
    """把各种写法的 DOI 归一化成小写裸串；空值返回空串。"""
    if not doi:
        return ""
    text = str(doi).strip().lower()
    for prefix in _DOI_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip()


def normalize_title(title: str) -> str:
    """标题归一化：全半角统一、去 HTML 标签、去标点与空白、转小写。"""
    text = unicodedata.normalize("NFKC", title or "")
    text = _HTML_TAG.sub("", text)
    return _PUNCT_AND_SPACE.sub("", text.lower())


def _merge_pair(base: Work, extra: Work) -> Work:
    """把 extra 合并进 base：只补空、取更全，绝不丢信息。"""
    title = base.title if len(base.title or "") >= len(extra.title or "") else extra.title
    abstract = (
        base.abstract if len(base.abstract or "") >= len(extra.abstract or "") else extra.abstract
    )
    authors = base.authors if len(base.authors or []) >= len(extra.authors or []) else extra.authors
    citations = [c for c in (base.citation_count, extra.citation_count) if c is not None]
    sources = list(dict.fromkeys([*base.sources, *extra.sources]))
    for work in (base, extra):
        if work.source_platform and work.source_platform not in sources:
            sources.append(work.source_platform)
    disciplines = list(dict.fromkeys([*base.disciplines, *extra.disciplines]))
    topics = list(dict.fromkeys([*base.topics, *extra.topics]))
    return base.model_copy(
        update={
            "title": title or base.title,
            "year": base.year if base.year is not None else extra.year,
            "doi": base.doi or extra.doi,
            "venue": base.venue or extra.venue,
            "topics": topics,
            "citation_count": max(citations) if citations else None,
            "abstract": abstract,
            "source_url": base.source_url or extra.source_url,
            "authors": authors,
            "institution": base.institution or extra.institution,
            "sources": sources,
            "disciplines": disciplines,
        }
    )


def _year_compatible(left: int | None, right: int | None) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= 1


def merge_works(works: list[Work]) -> list[Work]:
    """合并同一篇论文的多库记录，保留首次出现顺序。"""
    merged: list[Work] = []
    by_doi: dict[str, int] = {}
    by_title: dict[str, list[int]] = {}

    for work in works:
        if not work or not (work.title or "").strip():
            continue
        doi = normalize_doi(work.doi)
        title_key = normalize_title(work.title)

        if doi and doi in by_doi:
            index = by_doi[doi]
            merged[index] = _merge_pair(merged[index], work)
            continue

        target: int | None = None
        if title_key:
            for index in by_title.get(title_key, []):
                if _year_compatible(merged[index].year, work.year):
                    target = index
                    break

        if target is None:
            merged.append(work)
            index = len(merged) - 1
            if title_key:
                by_title.setdefault(title_key, []).append(index)
        else:
            index = target
            merged[index] = _merge_pair(merged[index], work)

        if doi:
            by_doi[doi] = index

    # 单来源的论文也补上 sources，展示时口径一致
    for work in merged:
        if not work.sources and work.source_platform:
            work.sources = [work.source_platform]
    return merged
