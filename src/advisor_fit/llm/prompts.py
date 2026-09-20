"""Prompt 注册表：所有 LLM 指令集中在这里并带版本号，便于评测回归。

规则：
- 任何模块都不得再内联写 LLM 指令字符串；用 `prompt_text("<key>")` 取。
- 修改指令必须同步提升该条 prompt 的版本号，并在评测里回归。
"""

from __future__ import annotations

from pydantic import BaseModel

# 全量指令集的版本；单条 prompt 可单独提升版本号覆盖它
PROMPT_VERSION = "2026.09.1"


class Prompt(BaseModel):
    key: str
    version: str
    text: str


_REGISTRY: dict[str, Prompt] = {}


def register(key: str, text: str, *, version: str | None = None) -> Prompt:
    prompt = Prompt(key=key, version=version or PROMPT_VERSION, text=text.strip())
    _REGISTRY[key] = prompt
    return prompt


def get(key: str) -> Prompt:
    return _REGISTRY[key]


def prompt_text(key: str) -> str:
    return _REGISTRY[key].text


def all_prompts() -> list[Prompt]:
    return sorted(_REGISTRY.values(), key=lambda prompt: prompt.key)


# --- Agent 决策 ---------------------------------------------------------------

register(
    "agent_decide",
    "你是导师研究 Agent 的决策层。根据任务和已执行步骤，决定下一步："
    "调用一个可用工具（action=tool，给出 tool 名与 args）、"
    "请求人工确认（action=confirm，给出 message 说明要确认什么）、"
    "或结束（action=done，给出 message）。只能调用 payload 中列出的工具。",
)

# --- 作者消歧 -----------------------------------------------------------------

register(
    "disambiguation",
    "判断每篇候选论文是否属于目标导师本人（而非同名作者）。"
    "依据：论文机构是否匹配、合作作者是否稳定、研究主题是否连续。"
    "若 payload 提供了该导师的 known_directions（已知研究方向），优先用它判断主题连续性。"
    "belongs 取 true 表示属于该导师，false 表示同名他人。"
    "若论文机构与目标学校明显不同，且研究领域/主题也明显不同（如医学 vs 计算机），"
    "应 belongs=false，不要因为姓名相同就保留。"
    "若论文机构不同、但研究主题或合作作者与该导师方向明显连续（疑似曾任职/调动），"
    "belongs 取 true 且 needs_review 取 true，reason 用中文说明「机构不同、疑似调动」。"
    "机构为空或信息不足以判断时，belongs 取 true 保留给用户人工确认。"
    "index 必须与 payload 中每篇论文的 index 一一对应，不要遗漏。",
)

# --- 结构化抽取 ---------------------------------------------------------------

register(
    "cv_extract",
    "从简历文本中抽取学生的姓名、事实与结构化经历。"
    "name 填学生真实姓名（通常在简历顶部）；没有明确写出则留空。"
    "只抽取文本中明确写出的内容，不得推断、补全或猜测。"
    "facts 的 field 只能是以下之一：skill, degree, institution, interest, project, publication；"
    "value 填原文或最简表述。"
    "education 填教育经历（学历/学校/专业/起止时间，逐年一条）；"
    "projects 填项目/科研经历（名称/简述/角色）；"
    "publications 填论文或专利（标题/出处/年份）。",
)

register(
    "homepage_extract",
    "从导师个人主页文本中提取结构化信息。"
    "name=姓名；institution=学校/单位；department=院系；title=职称（教授/副教授等）；"
    "email=邮箱；declared_interests=研究方向或研究领域（列表）；"
    "publications=代表论文或代表性成果的标题（列表，只提取明确的论文/著作标题）。"
    "找不到的字段留空字符串或空列表，不得编造。",
)

register(
    "faculty_list",
    "从高校学院「师资/教师/导师」列表页的链接中，找出每位教师的姓名与其个人主页链接。"
    "忽略导航栏、新闻、招生、学生、行政等非教师链接。"
    "name 填教师中文姓名（2-4 个汉字），href 填完整链接。",
)

register(
    "faculty_page",
    "从高校教师个人主页文本中抽取结构化信息。"
    "title=职称（教授/副教授/讲师等）；email=邮箱；"
    "research_areas=研究领域（大类，列表）；research_directions=研究方向（具体，列表）；"
    "publications=代表论文/著作标题（列表）。"
    "只抽取文本中明确写出的内容，找不到留空，不得编造。",
)

# --- 分析与推理 ---------------------------------------------------------------

register(
    "deep_analysis",
    "基于学生画像和导师证据，生成深度匹配分析，分五个部分："
    "research_intersection（研究交集）、method_match（方法能力匹配）、"
    "background_gaps（背景缺口）、recommended_papers（最值得读的论文）、"
    "knowledge_to_supplement（联系前应补的知识）。"
    "每个分析点 text 用中文，fact_ids 只引用 student_facts 里的 id，"
    "evidence_ids 只引用 professor 证据里的 id。不得编造事实。",
)

register(
    "direction_summary",
    "根据导师近年论文的标题、摘要和关键词，归纳其研究方向。"
    "summary 用 2-3 句中文概述研究方向；topics 列出 3-5 个具体研究主题；"
    "evidence_ids 只引用 payload 中论文的 source_ids。不得编造。",
)

register(
    "claims",
    "根据 payload 中提供的、作者归属已确认的论文证据，归纳导师近年的研究方向趋势。"
    "每个 claim 只能引用 payload 中出现的 evidence id。"
    "不得输出招生状态、团队规模等任何未提供证据的字段。",
)

# --- 邮件草稿 -----------------------------------------------------------------

register(
    "drafting",
    "写一封真诚、具体、个性化的中文套磁邮件正文（250–450 字），用于联系导师。"
    "必须基于 payload 提供的学生经历与导师研究，把「学生具体经历」与「导师具体研究」"
    "自然地连接起来——例如点出导师某篇论文/某个方向与你的项目或技能如何契合；"
    "不要空泛罗列、不要照搬原文措辞。"
    "结构：称呼与自我介绍（1–2 句）→ 与导师研究的具体连接（2–4 句，引用真实论文/方向）"
    "→ 你的相关经历与能力（2–3 句）→ 明确询问（1 句，如招生名额或能否进一步交流）→ 落款。"
    "每句显式标注 sentence_type："
    "PROFESSOR_FACT（涉及导师事实，evidence_ids 只引用 payload 中论文的 source_ids "
    "或主题的 evidence_ids）；"
    "STUDENT_FACT（涉及学生经历，fact_ids 只引用 payload 提供的 fact id）；"
    "GENERIC（问候、意图、收尾）。"
    "禁止使用拜读、久仰、震撼等未经证实的恭维；不要声称已阅读某篇论文。"
    "语气真诚克制，体现对导师研究的真实理解，而非模板套话。",
)
