"""机构名比对：判断"论文上的单位"和"用户填写的学校"是不是同一家。

为什么要单独处理：中文库里写「武汉大学」，国际库（OpenAlex / Europe PMC）写
「Wuhan University」。如果按"字符串互不包含就冲突"的朴素规则判断，
中国导师的所有英文论文都会被标成「机构不符，疑似同名」——这是**假冲突**，
会让用户白核对一堆本来正确的论文。

三条规则：

1. **别名词典命中** → 同一家（覆盖国内主要高校的中英文写法）。
2. **中英混排无法直接比较**（一边纯英文、一边含中文）→ **返回"无法判断"**，
   不判冲突（宁可不判，也不错判，符合项目「没有证据就标未确认」的红线）。
3. 其余情况沿用原规则：互相包含视为同源，否则视为冲突。
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ALIAS_SPLIT = re.compile(r"[\s\-–—_,，、()（）·]+")

# 中文校名 -> 常见英文写法（用于跨库比对；只收主流高校，宁缺勿错）
UNIVERSITY_ALIASES: dict[str, tuple[str, ...]] = {
    "清华大学": ("tsinghua",),
    "北京大学": ("peking",),
    "复旦大学": ("fudan",),
    "上海交通大学": ("shanghai jiao tong",),
    "浙江大学": ("zhejiang university",),
    "南京大学": ("nanjing university",),
    "武汉大学": ("wuhan university",),
    "华中科技大学": ("huazhong university of science and technology", "hust"),
    "中山大学": ("sun yat-sen", "sun yatsen"),
    "四川大学": ("sichuan university",),
    "西安交通大学": ("xi'an jiaotong", "xian jiaotong"),
    "哈尔滨工业大学": ("harbin institute of technology",),
    "中国科学技术大学": ("university of science and technology of china", "ustc"),
    "北京航空航天大学": ("beihang",),
    "同济大学": ("tongji",),
    "南开大学": ("nankai",),
    "天津大学": ("tianjin university",),
    "厦门大学": ("xiamen university",),
    "山东大学": ("shandong university",),
    "吉林大学": ("jilin university",),
    "东南大学": ("southeast university",),
    "中国人民大学": ("renmin university",),
    "北京师范大学": ("beijing normal university",),
    "华东师范大学": ("east china normal university",),
    "中南大学": ("central south university",),
    "湖南大学": ("hunan university",),
    "重庆大学": ("chongqing university",),
    "大连理工大学": ("dalian university of technology",),
    "华南理工大学": ("south china university of technology",),
    "北京理工大学": ("beijing institute of technology",),
    "西北工业大学": ("northwestern polytechnical",),
    "电子科技大学": ("university of electronic science and technology of china", "uestc"),
    "兰州大学": ("lanzhou university",),
    "东北大学": ("northeastern university",),
    "中国农业大学": ("china agricultural university",),
    "上海大学": ("shanghai university",),
    "苏州大学": ("soochow",),
    "郑州大学": ("zhengzhou university",),
    "中国药科大学": ("china pharmaceutical university",),
}


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def _alias_hit(left: str, right: str) -> bool:
    """任一侧中文校名的英文写法出现在另一侧 → 同一家。"""
    left_norm = left.strip().lower()
    right_norm = right.strip().lower()
    for chinese, aliases in UNIVERSITY_ALIASES.items():
        if chinese in left and any(alias in right_norm for alias in aliases):
            return True
        if chinese in right and any(alias in left_norm for alias in aliases):
            return True
    return False


def comparable(left: str, right: str) -> bool:
    """两边是不是"能直接比"——中英混排时比不了，返回 False。"""
    if not left or not right:
        return False
    return has_cjk(left) == has_cjk(right)


def institutions_conflict(paper_institution: str, institution: str | None) -> bool:
    """论文机构与填写学校是否冲突（无法比较时一律返回 False，即"不判冲突"）。"""
    paper = (paper_institution or "").strip()
    target = (institution or "").strip()
    if not paper or not target:
        return False
    if _alias_hit(paper, target):
        return False
    if not comparable(paper, target):
        return False
    return target not in paper and paper not in target


def normalize_tokens(text: str) -> list[str]:
    """把机构名切成可用于粗比对的词（去掉标点，转小写）。"""
    return [token for token in _ALIAS_SPLIT.split((text or "").lower()) if token]


def institution_aliases(text: str) -> tuple[str, ...]:
    """给出一个机构名的所有可比对写法：中文校名会带上英文别名。

    用于把用户填的「武汉大学」和学术库里的「Wuhan University」对上。
    太短的英文别名（<4 个字符）容易误伤别的机构，一律不参与匹配。
    """
    raw = (text or "").strip()
    if not raw:
        return ()
    found: list[str] = [raw]
    for chinese, aliases in UNIVERSITY_ALIASES.items():
        if chinese in raw:
            found.extend(alias for alias in aliases if len(alias) >= 4)
    return tuple(dict.fromkeys(found))


def institution_matches(candidate: str, target: str) -> bool:
    """学术库返回的机构串里，是否出现了目标机构（含中英别名）。"""
    haystack = (candidate or "").lower()
    if not haystack:
        return False
    return any(alias.lower() in haystack for alias in institution_aliases(target))
