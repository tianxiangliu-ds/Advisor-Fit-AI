"""导师姓名候选的复核：规则先拦一遍，有大模型再兜一遍。

为什么需要它：高校师资页上「师生服务」「常用下载」「国内师资」「宣传片」
「高端培训」「全职教师」这类界面词，首字（师/常/国/宣/高/全）**本身就是中文姓氏**，
只靠"首字是姓氏"会把它们全部当成人名。

第一层（确定性规则，不需要大模型）：
- 必须是 2–4 个汉字；
- 只允许中文与间隔号；
- 首字必须是常见中文姓氏；
- **姓名里不能包含任何界面词根**（服务/下载/师资/宣传/培训/教师/学院/中心…）。
  真人姓名几乎不可能包含这些词，这一条能干掉绝大部分误判。

第二层（可选，配置 LLM_API_KEY 后生效）：
- 把候选名单连同页面上下文交给大模型，逐条判定「是导师姓名 / 不是」，
  模型不可用时自动跳过，不影响主流程。
"""

from __future__ import annotations

import re

from advisor_fit.ingest.cv import looks_like_chinese_name
from advisor_fit.llm.provider import LLMUnavailable

# 界面词根：姓名里出现这些片段，几乎必然不是人名。
# 这里刻意用"两字词根"而不是单字，避免误伤真名（如"高峰""陈述"不含这些词根）。
UI_WORD_ROOTS: tuple[str, ...] = (
    # 机构与栏目
    "服务", "下载", "师资", "宣传", "培训", "教师", "教授", "讲师", "助教", "导师",
    "学院", "学校", "大学", "中心", "简介", "列表", "更多", "首页", "新闻", "通知",
    "公告", "公示", "动态", "要闻", "简报", "报告", "招生", "招聘", "就业", "实习",
    "留学", "校友", "捐赠", "基金", "章程", "制度", "规定", "办法", "细则", "指南",
    "手册", "表格", "申请", "审批", "流程", "联系", "关于", "导航", "登录", "注册",
    "搜索", "检索", "地图", "帮助", "反馈", "意见", "建议", "投诉", "声明", "版权",
    "隐私", "网站", "微信", "微博", "二维码", "地址", "电话", "邮编", "留言",
    # 党务与行政
    "党建", "党群", "党委", "党支部", "工会", "团委", "统战", "纪检", "廉政", "领导",
    "行政", "教务", "科研", "财务", "人事", "后勤", "基建", "保卫", "审计", "监察",
    "办公", "部门", "机构", "设置", "概况", "沿革",
    # 教学科研
    "教学", "学生", "研究生", "本科生", "博士", "硕士", "学位", "答辩", "毕业",
    "入学", "奖学", "助学", "课程", "专业", "培养", "人才", "队伍", "名录", "团队",
    "课题组", "实验室", "研究所", "研究院", "基地", "平台", "项目", "课题", "经费",
    "成果", "奖励", "荣誉", "表彰", "论文", "专利", "著作", "学科", "方向", "领域",
    # 交流与其它
    "交流", "合作", "国际", "国内", "访问", "会议", "讲座", "论坛", "沙龙", "活动",
    "专题", "视频", "图片", "文化", "体育", "医疗", "安全", "保密", "档案", "图书",
    "数据", "系统", "门户", "信息", "媒体", "出版", "期刊", "编辑部",
    # 人事状态
    "全职", "兼职", "退休", "在职", "在职教师", "荣休", "博士后",
    # 实测漏网
    "入口", "大厅", "职责", "人员", "科学", "学报", "期刊", "编辑部", "委员会",
)

# 已知的真实姓名，**必须永远在库里活着**。
#
# 这是事故驱动的清单，每一条都来自一次真实误删，不是凭空列的：
# - 郭新闻/李文化/方向忠：含"新闻/文化/方向"字样，曾被激进词表删除
# - 闫永达/玄玉波/初剑峰：罕见姓氏，曾被"首字必须是常见姓氏"的规则删除
# - 黄军/李杰：曾被"职称后缀"处理误伤
# - 张学工：清华大学真实教授，被加进安全词表的"学工"误删（2026-09）
# - 何鹏/吴恺/李侦/杨虹/温权/谭璇：采集时姓名与表头"职称"粘成"XX职称"，
#   当时后缀表没有"职称"，整行被当界面词删掉（2026-09）
#
# 评测里的 `library.must_survive_missing` 会盯这张表：少一个就判回归。
MUST_SURVIVE_NAMES: tuple[str, ...] = (
    "郭新闻", "李文化", "方向忠", "闫永达", "玄玉波", "初剑峰", "黄军", "李杰",
    "张学工", "何鹏", "吴恺", "李侦", "杨虹", "温权", "谭璇", "余超", "杨军", "张萌",
)

# 可以"放心自动删"的词根：这些片段几乎不可能出现在真人姓名里。
# 注意：像「新闻」「文化」「方向」「科学」「信息」「国际」这类**故意不放进来**——
# 「郭新闻」「李文化」「方向忠」都是真实存在的教授姓名，按这些词根删人会误伤。
SAFE_UI_ROOTS: tuple[str, ...] = (
    "服务", "下载", "师资", "宣传", "培训", "教师", "教授", "讲师", "导师", "学院",
    "学校", "大学", "中心", "简介", "列表", "更多", "首页", "通知", "公告", "公示",
    "动态", "要闻", "简报", "报告", "招生", "招聘", "就业", "校友", "捐赠", "章程",
    "制度", "规定", "办法", "细则", "指南", "手册", "表格", "申请", "审批", "流程",
    "联系", "关于", "导航", "登录", "注册", "搜索", "检索", "地图", "帮助", "反馈",
    "意见", "建议", "投诉", "声明", "版权", "隐私", "网站", "微信", "微博", "地址",
    "电话", "邮编", "留言", "党建", "党群", "党委", "党支部", "工会", "团委", "统战",
    "纪检", "廉政", "领导", "行政", "教务", "财务", "人事", "后勤", "基建", "保卫",
    "审计", "监察", "办公", "部门", "机构", "设置", "概况", "沿革", "教学", "学生",
    "研究生", "本科生", "博士", "硕士", "学位", "答辩", "毕业", "入学", "奖学",
    "助学", "课程", "专业", "培养", "人才", "队伍", "名录", "团队", "课题组",
    "实验室", "研究所", "研究院", "基地", "平台", "项目", "课题", "经费", "成果",
    "奖励", "荣誉", "表彰", "论文", "专利", "著作", "学科", "课题", "交流", "合作",
    "访问", "会议", "讲座", "论坛", "沙龙", "活动", "专题", "视频", "图片", "体育",
    "医疗", "安全", "保密", "档案", "图书", "数据", "系统", "门户", "媒体", "出版",
    "期刊", "编辑部", "全职", "兼职", "退休", "在职", "荣休", "博士后", "入口",
    "大厅", "职责", "人员", "学报", "委员会", "报考", "复试", "调剂", "推免",
    # 实测漏网（2026-09 抓西安交大等校时发现这些被当成导师姓名收了进来）。
    # 只收"绝不可能出现在真人姓名里"的词根。
    #
    # ⚠️ 反面教训：这里**不能收"学工"**。它看起来能拦「学工资料」，但「张学工」
    # 是清华大学的真实教授，收进来就会在采集/清洗时把人删掉。
    # 拦「学工资料」交给下面更安全的"资料"就够了。
    "院友", "教工", "旧版", "政策", "法规", "审核", "评估", "团学",
    "采购", "招标", "求知", "授业", "姓名", "院士", "中文", "园地", "留影",
    "风采", "家园", "主页", "怀念", "资料", "校园",
    # 「研究方向」要拒掉，但「方向忠」是真名，所以只放"研究"不放"方向"
    "研究",
)

_ALLOWED_NAME_RE = re.compile(r"^[一-龥]{2,4}$")
# 外籍学者姓名：允许拉丁字母、空格、点、连字符、间隔号
_LATIN_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z.\-' ]{2,40}$")


def normalize_name(text: str) -> str:
    """去掉姓名里的所有空白（社区数据里存在「薛 渊」这种带空格的写法）。"""
    return re.sub(r"\s+", "", text or "").strip()


def looks_like_foreign_name(text: str) -> bool:
    """外籍学者姓名，如「Kok-Meng Lee」「John M. Pfotenhauer」「AndersLindquist」。"""
    candidate = (text or "").strip()
    if not candidate or not re.search(r"[A-Za-z]{2,}", candidate):
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z.\-' ]{1,40}", candidate):
        return False
    # 全大写短串（CYC、ZYT）多为缩写，不算姓名
    letters_only = re.sub(r"[^A-Za-z]", "", candidate)
    if letters_only.isupper() and len(letters_only) <= 4:
        return False
    return bool(re.search(r"[a-z]", candidate))


# 姓名后面常被粘上的职称。注意：这里**不放「导师」「老师」「博导」「硕导」**——
# 它们是栏目词而不是职称后缀，放进来会把「全部导师」剥成「全部」从而逃过过滤。
TITLE_SUFFIXES: tuple[str, ...] = (
    "特聘副研究员", "助理研究员", "特聘研究员", "副研究员", "助理教授", "特聘教授",
    "副教授", "研究员", "博士后", "讲师", "教授", "老师",
    # 「职称」本身不是职称，但采集时会把"姓名"和表头"职称"粘在一起（如何鹏职称）。
    # 不剥掉它，这一行就会被当成界面词整行删除——**本该改名却删了人**。
    "职称",
)
# 姓名前面常被粘上的单位/机构
_INSTITUTION_PREFIX_RE = re.compile(
    r"^[一-龥A-Za-z]{0,8}?(?:学院|研究院|研究所|中心|学系|工研院|医院|实验室)"
)


_EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 卡片文本里用于切出姓名边界的机构词
_INSTITUTION_MARKERS: tuple[str, ...] = (
    "大学", "学院", "学系", "研究院", "研究所", "研究中心", "实验室", "医院", "学部",
)
# 高校名常见的地名/校名前缀：姓名后面通常紧跟这些词，用它来确认"名字切到哪儿"
_INSTITUTION_STARTS: tuple[str, ...] = (
    "武汉", "北京", "清华", "上海", "南京", "浙江", "中国", "华中", "华东", "华南",
    "西南", "西北", "东北", "天津", "四川", "中山", "复旦", "南开", "同济", "西安",
    "兰州", "吉林", "山东", "厦门", "湖南", "中南", "哈尔滨", "大连", "重庆", "深圳",
    "苏州", "郑州", "暨南", "东南", "广州", "成都", "合肥", "长沙", "青岛", "宁波",
    "大学", "学院", "研究院", "研究所", "学系", "中心", "实验室", "医院", "学部",
    "附属", "第一", "第二", "第三", "国家",
)


def parse_card_link(text: str) -> dict | None:
    """解析"整张卡片就是一个链接"的格式。

    很多高校师资页把整个人物卡片包在一个 <a> 里，链接文字是
    「姓名 + 单位 + 职称 + 邮箱」连成的一长串，例如：
        龚韵武汉大学空间科学与技术系副主任，教授yun.gong@whu.edu.cn
        陈燕鸣武汉大学动力与机械学院副教授chenyanming@whu.edu.cn
        程磊教授Lei.Cheng@whu.edu.cn
        丁浩武汉大学地球与空间科学技术学院，副院长，教授dhaosgg@sgg.whu.edu.cn

    做法：剥掉邮箱与职称，再"从前往后试姓名长度"——只有当剩余部分
    确实以地名/校名/机构词开头时，才认定前面的就是姓名。
    """
    raw = normalize_name(text)
    if len(raw) < 2:
        return None

    email = ""
    found = _EMAIL_IN_TEXT_RE.search(raw)
    if found:
        email = found.group(0)
        raw = raw.replace(email, "")

    title = ""
    for word in TITLE_SUFFIXES:
        if word in raw:
            title = word
            raw = raw.replace(word, "")
            break

    head = ""
    for length in (3, 2):
        if len(raw) <= length:
            continue
        candidate = raw[:length]
        rest = raw[length:]
        if not looks_like_person_name(candidate):
            continue
        if any(rest.startswith(prefix) for prefix in _INSTITUTION_STARTS):
            head = candidate
            break

    if not head:
        # 没有单位信息（如「程磊」这种只剩姓名的），整段就是姓名
        stripped = re.split(r"[，,、。；;：:(（]", raw)[0].strip()
        if looks_like_person_name(stripped):
            head = stripped
        elif looks_like_foreign_name(stripped):
            head = stripped

    if not head:
        return None
    return {"name": head, "title": title, "email": email, "directions": ""}


def normalize_person_name(text: str) -> str:
    """把姓名规整成规范写法。

    「董陇军副教授」→「董陇军」；「工研院胡耀武」→「胡耀武」；「薛 渊」→「薛渊」。
    规范化的意义：**保住人**，只是把粘在姓名上的职称/单位前缀剥掉，而不是删掉整条记录。

    注意：外籍姓名（如「matthias weidemüller」）**保持原样**，空格是姓名的一部分。
    """
    stripped = (text or "").strip()
    if looks_like_foreign_name(stripped):
        return re.sub(r"\s+", " ", stripped)

    name = normalize_name(stripped)
    # 剥掉职称后缀（长词优先，避免「教授」吃掉「副教授」）
    for suffix in TITLE_SUFFIXES:
        if name.endswith(suffix) and len(name) - len(suffix) >= 2:
            name = name[: -len(suffix)]
            break
    # 剥掉单位前缀
    match = _INSTITUTION_PREFIX_RE.match(name)
    if match and len(name) - match.end() >= 2:
        name = name[match.end() :]
    return name.strip()


def is_safe_to_auto_delete(text: str) -> bool:
    """可以放心自动删除的判定：只认"绝不可能出现在姓名里"的界面词根。

    与 has_ui_word 的区别：这里用的是收紧后的 SAFE_UI_ROOTS，
    避免把「郭新闻」「李文化」「方向忠」这类真名误删。
    **先做规范化再判断**——「董陇军副教授」规范化成「董陇军」后应当保留。
    """
    raw = normalize_name(text)
    if not raw:
        return True
    if looks_like_foreign_name(raw):
        return False
    # 先把职称后缀摘掉再看界面词根。
    # 这样「董陇军副教授」不会被误删（摘掉后是干净的姓名），
    # 而「计算机学院大部分老师」摘掉"老师"后仍含"学院"，照样会被删。
    stripped = raw
    for suffix in TITLE_SUFFIXES:
        if stripped.endswith(suffix) and len(stripped) - len(suffix) >= 2:
            stripped = stripped[: -len(suffix)]
            break
    for root in SAFE_UI_ROOTS:
        if root in stripped:
            return True
    name = normalize_person_name(raw)
    if not name:
        return True
    if not _ALLOWED_NAME_RE.match(name):
        return True
    for root in SAFE_UI_ROOTS:
        if root in name:
            return True
    return False

# 大模型复核用的提示词（也登记在 llm/prompts.py 里，便于版本管理）
VERIFY_INSTRUCTIONS = (
    "下面给你一份从高校师资页上抓到的候选名单，以及该页面的部分正文。"
    "请逐条判断每个候选词是不是**真实的中文导师姓名**。"
    "注意：像「师生服务」「常用下载」「国内师资」「宣传片」「高端培训」「全职教师」"
    "「学院简介」「研究方向」这类是网页界面词，不是人名，必须标为 false。"
    "只依据给定的候选名单与正文判断，不要编造。"
    "输出 keep 列表，只放你确认是人名的候选词，且必须与原文完全一致。"
)


class VerifyOutput:
    """占位类型：真正的 schema 由调用方传入（见 build_verify_schema）。"""


def build_verify_schema():
    """延迟导入 pydantic，避免本模块在无 pydantic 场景下报错。"""
    from pydantic import BaseModel

    class _VerifyOutput(BaseModel):
        keep: list[str] = []

    return _VerifyOutput


def has_ui_word(text: str) -> str:
    """返回命中的界面词根（空串表示没命中）。"""
    name = normalize_name(text)
    for root in UI_WORD_ROOTS:
        if root in name:
            return root
    return ""


def looks_like_person_name(text: str) -> bool:
    """确定性判断：这个短词像不像一位导师的姓名。

    判据刻意"宽进严出"里的"严出但不误伤"：
    - 必须是 2–4 个汉字；
    - **不能含任何"安全界面词根"**（「师生服务」「宣传片」「全职教师」都靠它）。

    这里**故意用收紧后的 SAFE_UI_ROOTS，而不是激进的 UI_WORD_ROOTS**：
    采集阶段丢掉的姓名事后无法找回，而多收进来的界面词可以靠大模型复核、
    跨学院导航词统计（`drop_site_navigation`）和事后清理脚本删掉。
    实测「郭新闻」「李文化」「方向忠」都是真实教授姓名，用激进词表会在采集时
    就把人丢掉——那正是本项目最不能犯的错。
    """
    name = normalize_name(text)
    if not _ALLOWED_NAME_RE.match(name):
        return False
    for root in SAFE_UI_ROOTS:
        if root in name:
            return False
    return True


def has_common_surname(text: str) -> bool:
    """首字是否在常见姓氏表里——只作为"可信度参考"，不作为过滤条件。"""
    return looks_like_chinese_name(normalize_name(text))


def filter_candidates(items: list[str]) -> tuple[list[str], list[str]]:
    """返回 (保留, 剔除) 两个列表。"""
    kept: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for raw in items:
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if looks_like_person_name(name):
            kept.append(name)
        else:
            dropped.append(name)
    return kept, dropped


def verify_with_llm(llm, names: list[str], page_text: str = "") -> list[str]:
    """让大模型复核候选人名；模型不可用时原样返回（不阻断流程）。"""
    if not names:
        return []
    schema = build_verify_schema()
    try:
        output = llm.generate(
            schema=schema,
            instructions=VERIFY_INSTRUCTIONS,
            payload={"candidates": names, "page_excerpt": page_text[:4000]},
        )
    except LLMUnavailable:
        return names
    except Exception:  # noqa: BLE001 - 复核失败不能影响爬取
        return names

    keep = getattr(output, "keep", None)
    if not isinstance(keep, list):
        return names
    allowed = {str(item).strip() for item in keep}
    return [name for name in names if name in allowed]


def is_llm_available(llm) -> bool:
    from advisor_fit.llm.provider import NullLLM

    return not isinstance(llm, NullLLM)


CLEAN_INSTRUCTIONS = (
    "下面是从高校导师库里挑出的**可疑条目**。请逐条判断哪些是**真实的人名**、"
    "哪些是网页界面词或栏目名。"
    "注意："
    "①「全部导师」「电信学院大部分导师」「某院放疗科」「CYC」这类不是人名；"
    "②「董陇军副教授」「刘志祥教授」这类是真名后面带了职称，**算人名**；"
    "③ 罕见姓氏（如闫永达、玄玉波）和**外籍学者姓名**（如 Kok-Meng Lee）都算人名；"
    "④「郭新闻」「李文化」「方向忠」是真实存在的教授姓名（虽然含'新闻/文化/方向'字样），"
    "**必须算人名**。"
    "只把确认是人名的条目放进 keep，且必须与原文完全一致；不确定的不要放进 keep。"
)


def classify_names_with_llm(
    llm, names: list[str], *, batch_size: int = 120, retries: int = 2
) -> set[str] | None:
    """批量判定哪些是真名。返回 keep 集合；完全不可用时返回 None（调用方自行兜底）。

    单批失败不会中断整轮（网络抖动很常见），只有一批都没成功才返回 None。
    """
    if not names:
        return set()
    if not is_llm_available(llm):
        return None
    schema = build_verify_schema()
    keep: set[str] = set()
    succeeded = 0
    attempted = 0
    for start in range(0, len(names), batch_size):
        batch = names[start : start + batch_size]
        attempted += 1
        for attempt in range(retries + 1):
            try:
                output = llm.generate(
                    schema=schema,
                    instructions=CLEAN_INSTRUCTIONS,
                    payload={"candidates": batch},
                )
            except Exception:  # noqa: BLE001 - 单批失败重试，仍失败就跳过这一批
                if attempt == retries:
                    break
                continue
            items = getattr(output, "keep", None)
            if isinstance(items, list):
                keep.update(str(item).strip() for item in items if str(item).strip())
                succeeded += 1
            break
    if attempted and succeeded == 0:
        return None
    return keep
