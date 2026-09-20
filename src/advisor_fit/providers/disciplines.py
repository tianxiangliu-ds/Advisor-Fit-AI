"""学科分类：决定"这位导师该去哪些学术库查"。

三条判断路径，可靠性从高到低：

1. 用户/画像显式指定（页面上选，或来自已确认的导师资料）；
2. **学术库自带标签**（OpenAlex 的 primary_topic.field）——这是有证据的判断；
3. 关键词规则（研究方向、院系名里出现「临床」「计算机」「金融」等）——本地兜底，零请求。

三条都不成立时归入 `general`（全学科通用），此时只查全学科主干库，
不猜学科、不写进报告，符合项目「没有证据就标 UNKNOWN」的红线。
"""

from __future__ import annotations

# 内部 key -> 面向用户的中文标签
DISCIPLINE_LABELS: dict[str, str] = {
    "cs": "计算机 / 人工智能",
    "medicine": "医学 / 生命科学",
    "physics": "物理 / 数学 / 天文",
    "chemistry": "化学 / 材料 / 化工",
    "engineering": "工程技术 / 能源",
    "economics": "经济 / 管理 / 金融",
    "humanities": "人文 / 社科",
    "agriculture": "农学 / 环境 / 地球科学",
    "general": "通用（未指定学科）",
}

# 关键词兜底：命中即判为该学科。词表可通过 config/sources.json 覆盖。
DEFAULT_DISCIPLINE_KEYWORDS: dict[str, list[str]] = {
    "cs": [
        "计算机", "软件", "人工智能", "机器学习", "深度学习", "神经网络", "算法",
        "数据挖掘", "大数据", "信息安全", "网络空间", "计算机视觉", "自然语言",
        "模式识别", "智能科学", "信息检索", "操作系统", "数据库", "区块链",
        "计算机科学", "自动化", "控制工程", "机器人",
    ],
    "medicine": [
        "医学", "临床", "医院", "肿瘤", "癌症", "病理", "药理", "免疫", "流行病",
        "公共卫生", "护理", "口腔", "药学", "中医", "生物医学", "生物信息",
        "基因组", "细胞", "神经科学", "生命科学", "生物化学", "分子生物",
        "传染病", "疫苗", "心血管", "内分泌",
    ],
    "physics": [
        "物理", "数学", "天文", "天体", "量子", "光学", "凝聚态", "统计", "力学",
        "粒子", "核科学", "理论物理",
    ],
    "chemistry": [
        "化学", "材料", "化工", "催化", "高分子", "纳米", "冶金", "金属",
        "有机合成", "分析化学",
    ],
    "engineering": [
        "工程", "机械", "电气", "电子", "通信", "土木", "建筑", "水利", "能源",
        "动力", "航空", "航天", "船舶", "交通", "仪器", "测绘", "矿业", "石油",
        "核工程", "安全工程",
    ],
    "economics": [
        "经济", "金融", "会计", "管理", "工商", "市场营销", "国际贸易", "财政",
        "统计学院", "保险", "审计", "商学院", "运营管理", "人力资源",
    ],
    "humanities": [
        "文学", "历史", "哲学", "语言", "外国语", "新闻", "传播", "法学", "法律",
        "社会学", "政治学", "教育", "心理", "艺术", "音乐", "美术", "考古",
        "人类学", "宗教", "文化", "马克思主义", "思政",
    ],
    "agriculture": [
        "农学", "农业", "作物", "园艺", "植物", "动物", "兽医", "林学", "水产",
        "土壤", "环境", "生态", "地理", "气象", "海洋", "食品", "资源",
    ],
}

# OpenAlex 的 26 个 field（primary_topic.field.display_name）→ 我们的学科 key
_OPENALEX_FIELD_MAP: dict[str, str] = {
    "computer science": "cs",
    "mathematics": "physics",
    "physics and astronomy": "physics",
    "medicine": "medicine",
    "nursing": "medicine",
    "dentistry": "medicine",
    "health professions": "medicine",
    "immunology and microbiology": "medicine",
    "neuroscience": "medicine",
    "pharmacology, toxicology and pharmaceutics": "medicine",
    "biochemistry, genetics and molecular biology": "medicine",
    "veterinary": "agriculture",
    "agricultural and biological sciences": "agriculture",
    "environmental science": "agriculture",
    "earth and planetary sciences": "agriculture",
    "chemistry": "chemistry",
    "chemical engineering": "chemistry",
    "materials science": "chemistry",
    "engineering": "engineering",
    "energy": "engineering",
    "economics, econometrics and finance": "economics",
    "business, management and accounting": "economics",
    "decision sciences": "economics",
    "arts and humanities": "humanities",
    "social sciences": "humanities",
    "psychology": "humanities",
}

# arXiv 的分类前缀 → 我们的学科 key（cs.* / math.* / physics.* 等）
_ARXIV_PREFIX_MAP: dict[str, str] = {
    "cs": "cs",
    "stat": "cs",
    "math": "physics",
    "physics": "physics",
    "astro-ph": "physics",
    "cond-mat": "physics",
    "quant-ph": "physics",
    "nlin": "physics",
    "chem": "chemistry",
    "eess": "engineering",
    "econ": "economics",
    "q-fin": "economics",
    "q-bio": "medicine",
}


def discipline_label(key: str | None) -> str:
    return DISCIPLINE_LABELS.get(key or "", DISCIPLINE_LABELS["general"])


def is_known_discipline(key: str | None) -> bool:
    return bool(key) and key in DISCIPLINE_LABELS and key != "general"


def guess_discipline(
    *texts: str | None, keywords: dict[str, list[str]] | None = None
) -> str | None:
    """按关键词猜学科；猜不出来返回 None（不硬猜）。"""
    table = keywords or DEFAULT_DISCIPLINE_KEYWORDS
    haystack = " ".join(text for text in texts if text)
    if not haystack:
        return None
    hits: dict[str, int] = {}
    for key, words in table.items():
        for word in words:
            if word and word in haystack:
                hits[key] = hits.get(key, 0) + 1
    if not hits:
        return None
    # 命中数相同的，按 DISCIPLINE_LABELS 的定义顺序取先出现的，保证结果稳定
    best = max(hits.values())
    for key in DISCIPLINE_LABELS:
        if hits.get(key) == best:
            return key
    return None


def discipline_from_openalex_field(field_name: str | None) -> str | None:
    if not field_name:
        return None
    return _OPENALEX_FIELD_MAP.get(field_name.strip().lower())


def discipline_from_arxiv_category(category: str | None) -> str | None:
    if not category:
        return None
    prefix = category.split(".")[0].strip().lower()
    return _ARXIV_PREFIX_MAP.get(prefix)


def dominant_discipline(keys: list[str], *, min_support: int = 1) -> str | None:
    """从一批论文的学科标签里取出现最多的那个（用于"从检索结果反推学科"）。

    min_support 是"至少要有几篇论文支持"：默认 1 篇即可，但路由器反推学科时会要求
    至少 3 篇，避免被个别噪声论文带偏、把检索分流到错误的专业库。
    """
    counts: dict[str, int] = {}
    for key in keys:
        if is_known_discipline(key):
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    best = max(counts.values())
    if best < min_support:
        return None
    for key in DISCIPLINE_LABELS:
        if counts.get(key) == best:
            return key
    return None
