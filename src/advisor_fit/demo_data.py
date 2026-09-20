"""演示数据：让任何人在不接触真实数据的前提下看到完整界面。

两个用途：

1. **在线 Demo**：公开的无账号演示站必须是"零真实数据"的。部署时自动生成一份
   虚构数据，访问者看到的是完整功能，而不是空页面，也不会看到任何人的简历；
2. **本地试用**：新用户想先看看长什么样，不用先把导师库建起来。

数据全部是编造的：学校叫「示例大学」、导师叫「示例导师」、论文标题带「（示例）」。
**绝不使用真实人物或真实论文信息**，这一点是硬约束（公开 Demo 尤其重要）。

`ensure_demo_data()` 是幂等的：已经生成过就直接返回，不会覆盖或重复写入。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEMO_UNIVERSITIES: tuple[str, ...] = ("示例大学", "样例理工大学", "示范师范大学")

# (姓名, 学校, 院系, 职称, 研究方向, 代表论文)
DEMO_ADVISORS: tuple[tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("示例导师", "示例大学", "信息管理学院", "教授", ("知识图谱", "数字人文"),
     ("知识图谱构建方法（示例）",)),
    ("示例导师乙", "示例大学", "计算机学院", "副教授", ("机器学习", "知识图谱"), ()),
    ("示例导师丙", "示例大学", "文学院", "教授", ("数字人文", "文化遗产"), ()),
    ("示例导师丁", "样例理工大学", "计算机科学与技术学院", "教授", ("机器学习", "计算机视觉"), ()),
    ("示例导师戊", "样例理工大学", "自动化学院", "讲师", ("机器人", "控制工程"), ()),
    ("示例导师己", "示范师范大学", "教育学部", "教授", ("教育技术", "知识图谱"), ()),
    ("示例导师庚", "示范师范大学", "历史学院", "副教授", ("文化遗产", "博物馆学"), ()),
    ("示例导师辛", "样例理工大学", "地球科学学院", "教授", ("海洋地质",), ()),
    ("示例导师壬", "示例大学", "经济学院", "教授", ("数字经济", "产业经济"), ()),
    ("示例导师癸", "示范师范大学", "心理学院", "副教授", ("认知科学", "教育心理"), ()),
)

# (字段, 内容)
DEMO_STUDENT_FACTS: tuple[tuple[str, str], ...] = (
    ("degree", "本科四年级"),
    ("institution", "示例大学 信息管理专业"),
    ("skill", "Python / PyTorch"),
    ("skill", "知识图谱构建"),
    ("interest", "数字人文"),
    ("project", "本科毕业设计：面向地方志的知识图谱构建"),
)


@dataclass(frozen=True)
class DemoProfessor:
    name: str
    institution: str
    department: str
    title: str
    directions: tuple[str, ...]
    papers: tuple[tuple[str, int, str, str, tuple[str, ...]], ...]  # 标题/年份/摘要/链接/关键词
    email: str = ""
    homepage: str = ""


DEMO_PROFESSORS: tuple[DemoProfessor, ...] = (
    DemoProfessor(
        name="示例导师",
        institution="示例大学",
        department="信息管理学院",
        title="教授",
        directions=("知识图谱", "数字人文"),
        email="demo1@example.edu.cn",
        homepage="https://example.edu.cn/sim",
        papers=(
            (
                "知识图谱构建方法（示例）",
                2024,
                "本文综述了知识图谱的构建流程与常见方法。（示例摘要，非真实论文）",
                "https://example.org/paper/1",
                ("知识图谱", "数字人文"),
            ),
            (
                "面向地方志的实体抽取（示例）",
                2023,
                "本文讨论地方志文本中的实体抽取方法。（示例摘要，非真实论文）",
                "https://example.org/paper/2",
                ("实体抽取", "数字人文"),
            ),
        ),
    ),
    DemoProfessor(
        name="示例导师乙",
        institution="示例大学",
        department="计算机学院",
        title="副教授",
        directions=("机器学习", "知识图谱"),
        papers=(
            (
                "图神经网络在推荐系统中的应用（示例）",
                2025,
                "本文比较了几种图神经网络在推荐场景的表现。（示例摘要）",
                "https://example.org/paper/3",
                ("机器学习", "图神经网络"),
            ),
        ),
    ),
)


def is_seeded(data_dir: Path | str) -> bool:
    """演示数据是否已经生成过（只看有没有库文件，不打开数据库）。"""
    directory = Path(data_dir)
    return (directory / "advisors.db").is_file() and (directory / "app.db").is_file()


def seed_advisors(data_dir: Path | str) -> int:
    """写入虚构的导师库，返回条数。"""
    from advisor_fit.models.advisor import Advisor
    from advisor_fit.storage.advisor_repo import AdvisorRepository

    repo = AdvisorRepository(Path(data_dir) / "advisors.db")
    for name, university, department, title, directions, publications in DEMO_ADVISORS:
        repo.upsert(
            Advisor(
                university=university,
                department=department,
                name=name,
                title=title,
                email="demo1@example.edu.cn" if title == "教授" else "demo2@example.edu.cn",
                research_directions=list(directions),
                research_areas=list(directions[:1]),
                publications=list(publications),
                homepage_url=f"https://example.edu.cn/{department}",
                profile_text=(
                    f"{name}，{university}{department}{title}，主要研究方向："
                    + "、".join(directions)
                    + "。本条为演示数据。"
                ),
                sources=["official"],
            ),
            source="official",
        )
    return repo.count()


def demo_agent_trace(professor_name: str = "示例导师") -> dict:
    """一份**示例** Agent 运行轨迹（虚构）。

    为什么演示数据必须带轨迹：轨迹区是"Agent 可见"的核心展示，但它只在真正跑过
    一次检索后才产生。演示数据如果不带，公开 Demo 的访问者就完全看不到 Agent
    做过什么——而这恰恰是这个项目最该被看到的部分。

    这里手写的步骤对应一次典型运行：模型先查作者 → 核对机构 → 按标题补查 →
    发现机构冲突请求人工确认 → 结束。**不是真实运行日志**，只是形状一致的示例。
    """
    return {
        "task": f"检索导师「{professor_name}」的候选论文（示例轨迹，非真实运行）",
        "mode": "agent",
        # 走到"请求人工确认"就停了——与下面最后一步的 confirmation 对应
        "stages": ["seeding", "searching", "awaiting_confirmation"],
        "tools": [
            {"name": "search_by_author", "description": "按姓名+学校在多个学术库中检索候选论文",
             "parameters": {"type": "object",
                            "properties": {"name": {"type": "string"},
                                           "institution": {"type": ["string", "null"]},
                                           "source": {"type": "string"}}},
             "permission": "network"},
            {"name": "search_by_title",
             "description": "按论文标题检索（多个学术库 + Crossref 兜底），作者名查不到时用它兜底",
             "parameters": {"type": "object",
                            "properties": {"title": {"type": "string"}}},
             "permission": "network"},
            {"name": "fetch_professor_homepage",
             "description": "打开导师个人主页，抽取职称/院系/邮箱/公开写出的研究方向",
             "parameters": {"type": "object",
                            "properties": {"url": {"type": "string"}}},
             "permission": "network"},
            {"name": "check_paper_affiliations",
             "description": "检查当前候选论文的机构与目标学校是否一致，判断是否疑似同名作者",
             "parameters": {"type": "object", "properties": {}},
             "permission": "read"},
        ],
        "steps": [
            {"index": 0, "kind": "llm_decision", "name": "decide", "status": "ok",
             "duration_ms": 812, "summary": "tool"},
            {"index": 1, "kind": "tool_call", "name": "search_by_author",
             "args_digest": "institution=示例大学&name=示例导师&source=auto",
             "status": "ok", "duration_ms": 1436, "summary": "papers=25"},
            {"index": 2, "kind": "llm_decision", "name": "decide", "status": "ok",
             "duration_ms": 690, "summary": "tool"},
            {"index": 3, "kind": "tool_call", "name": "check_paper_affiliations",
             "args_digest": "", "status": "ok", "duration_ms": 3, "summary": "total=25"},
            {"index": 4, "kind": "llm_decision", "name": "decide", "status": "ok",
             "duration_ms": 704, "summary": "tool"},
            {"index": 5, "kind": "tool_call", "name": "search_by_title",
             "args_digest": "title=知识图谱构建方法（示例）",
             "status": "ok", "duration_ms": 1120, "summary": "papers=3"},
            {"index": 6, "kind": "confirmation", "name": "requested", "status": "ok",
             "duration_ms": 0, "summary": "发现 4 篇论文机构不一致，请确认是否保留"},
            {"index": 7, "kind": "llm_decision", "name": "decide", "status": "ok",
             "duration_ms": 704, "summary": "done"},
            {"index": 8, "kind": "done", "name": "done", "status": "ok",
             "duration_ms": 0, "summary": "已汇总候选论文，等待人工确认归属"},
        ],
        "budget": {"steps": 9, "llm_calls": 4, "tokens": 8120, "tokens_in": 6400,
                   "tokens_out": 1720, "external_calls": 2,
                   "per_source": {"search_by_author": 1, "search_by_title": 1},
                   "elapsed_seconds": 12.4},
        "degraded_reason": "",
    }


def seed_runs(data_dir: Path | str) -> list[str]:
    """跑两次「人工证据输入」全流程，生成已完成的研究记录（离线、不调用任何 API）。"""
    from advisor_fit.ingest.manual_professor import ManualPaperInput, ManualProfessorInput
    from advisor_fit.llm.provider import NullLLM
    from advisor_fit.manual_pipeline import run_manual_pipeline
    from advisor_fit.models.common import FactStatus
    from advisor_fit.models.student import StudentFact, StudentProfile
    from advisor_fit.storage.repository import Repository

    repo = Repository(Path(data_dir) / "app.db")
    facts = [
        StudentFact(id=f"f{index}", field=field, value=value, status=FactStatus.FACT)
        for index, (field, value) in enumerate(DEMO_STUDENT_FACTS, start=1)
    ]
    student = StudentProfile(student_id="demo_student", name="示例同学", facts=facts)
    confirmed = {fact.id for fact in facts}

    run_ids: list[str] = []
    for professor in DEMO_PROFESSORS:
        result = run_manual_pipeline(
            student=student,
            confirmed_fact_ids=confirmed,
            professor_input=ManualProfessorInput(
                name=professor.name,
                institution=professor.institution,
                department=professor.department,
                title=professor.title,
                email=professor.email or None,
                homepage_url=professor.homepage or None,
                declared_interests=list(professor.directions),
                identity_confirmed=True,
                papers=[
                    ManualPaperInput(
                        title=title,
                        year=year,
                        abstract=abstract,
                        source_url=url,
                        source_platform="DOI/出版社",
                        keywords=list(keywords),
                        user_confirmed=True,
                    )
                    for title, year, abstract, url, keywords in professor.papers
                ],
            ),
            llm=NullLLM(),
            repository=repo,
            paper_read_confirmed=False,
        )
        repo.set_run_name(result.run_id, f"{professor.name} · {professor.institution}")
        # 每条记录都配一份示例轨迹：公开 Demo 才看得到 Agent 运行轨迹区，
        # 也让演示不依赖"先点开哪条记录"。
        repo.save_trace(result.run_id, demo_agent_trace(professor.name))
        run_ids.append(result.run_id)
    return run_ids


def ensure_demo_data(data_dir: Path | str, *, force: bool = False) -> dict:
    """确保演示数据存在（幂等）。返回一个说明用了什么的字典。"""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if is_seeded(directory) and not force:
        return {"created": False, "data_dir": str(directory), "advisors": 0, "runs": 0}
    advisors = seed_advisors(directory)
    runs = seed_runs(directory)
    return {
        "created": True,
        "data_dir": str(directory),
        "advisors": advisors,
        "runs": len(runs),
    }
