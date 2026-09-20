"""人名比对：判断一篇论文的作者名单里，有没有我们要找的那位导师。

为什么必须单独做这一步：OpenAlex 的 `raw_author_name.search` 其实是**分词模糊匹配**，
实测用「陆伟」去查，会返回「陆鑫」「陆长峰」「陆巧银」的论文。
所以任何"按名字捞回来"的结果，都要在这里过一遍严格的姓名核对。

支持这些真实出现过的写法：
- 中文全名：`陆伟`、`陆 伟` —— 从作者串里抽出中文段做**完全相等**比对
  （不能用"包含"，否则「陆伟」会命中「陆伟明」）
- 中英混排：`蒋春 Jiang Chun` —— 抽出中文段 `蒋春` 命中
- 英文正序/倒序/缩写：`Wei Zhang`、`Zhang, Wei`、`W. Zhang`
- 带变音符的转写：`Wei Lü` 与 `Wei Lu` 视为同一人
- 逗号拼接的多人串：`WANG Xiao, LI Wei, ZHANG Bei` —— 必须**同一个人**同时满足姓名各部分

拿不准时**返回 False**（宁可漏，也不把别人的论文塞进来当候选）。
"""

from __future__ import annotations

import re
import unicodedata

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_SPLIT = re.compile(r"[\s\-–—.,;:·'’]+")
_AUTHOR_SEPARATOR = re.compile(r"[,;，；、|/]+")
_WHITESPACE = re.compile(r"\s+")


def _strip_diacritics(text: str) -> str:
    """去掉变音符：Lü -> Lu，García -> Garcia（学术库的中文名转写很不统一）。"""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_person_name(name: str) -> str:
    """归一化人名：全半角统一、去变音符、去标点空格、转小写。"""
    text = _strip_diacritics(unicodedata.normalize("NFKC", name or ""))
    return _SPLIT.sub("", text).lower()


def name_tokens(name: str) -> list[str]:
    text = _strip_diacritics(unicodedata.normalize("NFKC", name or "")).lower()
    return [token for token in _SPLIT.split(text) if token]


def cjk_runs(text: str) -> list[str]:
    """抽出字符串里的中文姓名段（2 字以上连续汉字，允许中间有空格）。"""
    compact = _WHITESPACE.sub("", text or "")
    return _CJK_RUN.findall(compact)


def has_cjk(text: str) -> bool:
    return bool(cjk_runs(text))


def split_author_entry(entry: str) -> list[str]:
    """把一条作者记录拆成单人：有些库把多个人塞在一个字符串里。

    要区分两种完全不同的写法：
    - 书目惯例 `Zhang, Wei`（姓, 名）→ 是**一个人**，不能拆；
    - 拼接惯例 `WANG Xiao, LI Wei, ZHANG Bei` → 是**多个人**，必须拆开，
      否则「LI Wei」+「ZHANG Bei」会被误判成 Wei Zhang。

    判据：正好两段、且两段都只有一个单词时按"姓, 名"处理；其余一律拆开。
    """
    parts = [part.strip() for part in _AUTHOR_SEPARATOR.split(entry or "") if part.strip()]
    if not parts:
        return []
    if len(parts) == 2 and all(len(name_tokens(part)) <= 1 for part in parts):
        return [entry.strip()]
    return parts


def _latin_tokens_match(target_tokens: list[str], author_tokens: list[str]) -> bool:
    """英文名逐 token 比对，允许首字母缩写（wei -> w）。"""
    remaining = list(author_tokens)
    for token in target_tokens:
        hit = None
        for index, candidate in enumerate(remaining):
            if candidate == token or (len(candidate) == 1 and token.startswith(candidate)):
                hit = index
                break
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def _single_latin_author_matches(target: str, target_tokens: list[str], entry: str) -> bool:
    normalized_target = normalize_person_name(target)
    if normalize_person_name(entry) == normalized_target:
        return True
    # 复姓或带中缀的写法：目标整名出现在同一个人名里
    if len(normalized_target) >= 8 and normalized_target in normalize_person_name(entry):
        return True
    return _latin_tokens_match(target_tokens, name_tokens(entry))


def author_matches(target: str, authors: list[str]) -> bool:
    """作者名单里是否有目标姓名（严格核对，认不出就返回 False）。"""
    if not target or not authors:
        return False
    target_cjk = cjk_runs(target)
    if target_cjk:
        wanted = set(target_cjk)
        for entry in authors:
            if not entry:
                continue
            if wanted & set(cjk_runs(str(entry))):
                return True
        return False

    target_tokens = name_tokens(target)
    if not target_tokens:
        return False
    for entry in authors:
        if not entry:
            continue
        # 有的库把多个人拼成一条，必须拆开逐个比对，否则"LI Wei, ZHANG Bei"会误判成 Wei Zhang
        for single in split_author_entry(str(entry)):
            if _single_latin_author_matches(target, target_tokens, single):
                return True
    return False
