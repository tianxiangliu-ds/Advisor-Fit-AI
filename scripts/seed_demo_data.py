"""生成一份**虚构的**演示数据，用于截图、在线 Demo 或第一次上手体验。

为什么要在项目里带这个脚本：

- 在线无账号 Demo 需要一个预置数据集，不能让访问者看到别人的真实简历；
- 开源项目的截图必须能被任何人复现，不能拿真实导师和真实论文当素材；
- 新用户想先"看看它长什么样"，跑一下就有东西可看。

数据全部是编造的：学校叫「示例大学」、导师叫「示例导师」、论文标题带「（示例）」。
**不会**使用任何真实人物或真实论文信息。

用法：

```powershell
# 生成到 .tmp/demo-data（默认，不碰你的 data/）
.\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py

# 指定输出目录
.\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py --out demo-data

# 然后用这份数据启动应用
$env:DATA_DIR=".tmp/demo-data"; .\\.venv\\Scripts\\python.exe -m streamlit run app.py
```
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:  # 允许直接运行脚本而无需安装
    sys.path.insert(0, str(ROOT / "src"))

DEMO_UNIVERSITIES = ("示例大学", "样例理工大学", "示范师范大学")

DEMO_ADVISORS = [
    ("示例导师", "示例大学", "信息管理学院", "教授", ["知识图谱", "数字人文"],
     ["知识图谱构建方法（示例）"]),
    ("示例导师乙", "示例大学", "计算机学院", "副教授", ["机器学习", "知识图谱"], []),
    ("示例导师丙", "示例大学", "文学院", "教授", ["数字人文", "文化遗产"], []),
    ("示例导师丁", "样例理工大学", "计算机科学与技术学院", "教授", ["机器学习", "计算机视觉"], []),
    ("示例导师戊", "样例理工大学", "自动化学院", "讲师", ["机器人", "控制工程"], []),
    ("示例导师己", "示范师范大学", "教育学部", "教授", ["教育技术", "知识图谱"], []),
    ("示例导师庚", "示范师范大学", "历史学院", "副教授", ["文化遗产", "博物馆学"], []),
    ("示例导师辛", "样例理工大学", "地球科学学院", "教授", ["海洋地质"], []),
    ("示例导师壬", "示例大学", "经济学院", "教授", ["数字经济", "产业经济"], []),
    ("示例导师癸", "示范师范大学", "心理学院", "副教授", ["认知科学", "教育心理"], []),
]


def seed_advisors(data_dir: Path) -> int:
    from advisor_fit.models.advisor import Advisor
    from advisor_fit.storage.advisor_repo import AdvisorRepository

    repo = AdvisorRepository(data_dir / "advisors.db")
    for name, university, department, title, directions, publications in DEMO_ADVISORS:
        repo.upsert(
            Advisor(
                university=university,
                department=department,
                name=name,
                title=title,
                email=f"demo{'1' if title == '教授' else '2'}@{'example.edu.cn'}",
                research_directions=directions,
                research_areas=directions[:1],
                publications=publications,
                homepage_url=f"https://example.edu.cn/{department}",
                profile_text=f"{name}，{university}{department}{title}，主要研究方向："
                + "、".join(directions)
                + "。本条为演示数据。",
                sources=["official"],
            ),
            source="official",
        )
    return repo.count()


def demo_agent_trace(professor_name: str = "示例导师") -> dict:
    """一份**示例** Agent 运行轨迹（虚构，用于让界面上的轨迹区有东西可看）。

    为什么要给演示数据配轨迹：轨迹区是"Agent 可见"的核心展示，但只有真正跑过一次
    检索才会产生。演示数据如果不带轨迹，别人拿到 Demo 就完全看不到 Agent 做过什么。

    这里手写的步骤对应一次典型运行：模型先查作者 → 再按标题补查 → 发现机构冲突
    请求人工确认 → 结束。**不是真实运行日志**，只是形状一致的示例。
    """
    return {
        "task": f"检索导师「{professor_name}」的候选论文（示例轨迹，非真实运行）",
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
             "args_digest": "", "status": "ok", "duration_ms": 3,
             "summary": "total=25"},
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


def seed_runs(data_dir: Path) -> list[str]:
    """跑两次"人工证据输入"全流程，生成两条已完成记录（离线、不调用任何 API）。"""
    from advisor_fit.ingest.manual_professor import ManualPaperInput, ManualProfessorInput
    from advisor_fit.llm.provider import NullLLM
    from advisor_fit.manual_pipeline import run_manual_pipeline
    from advisor_fit.models.common import FactStatus
    from advisor_fit.models.student import StudentFact, StudentProfile
    from advisor_fit.storage.repository import Repository

    repo = Repository(data_dir / "app.db")
    facts = [
        StudentFact(id="f1", field="degree", value="本科四年级", status=FactStatus.FACT),
        StudentFact(id="f2", field="institution", value="示例大学 信息管理专业",
                    status=FactStatus.FACT),
        StudentFact(id="f3", field="skill", value="Python / PyTorch", status=FactStatus.FACT),
        StudentFact(id="f4", field="skill", value="知识图谱构建", status=FactStatus.FACT),
        StudentFact(id="f5", field="interest", value="数字人文", status=FactStatus.FACT),
        StudentFact(id="f6", field="project", value="本科毕业设计：面向地方志的知识图谱构建",
                    status=FactStatus.FACT),
    ]
    student = StudentProfile(student_id="demo_student", name="示例同学", facts=facts)
    confirmed = {fact.id for fact in facts}

    professors = [
        ManualProfessorInput(
            name="示例导师",
            institution="示例大学",
            department="信息管理学院",
            title="教授",
            email="demo1@example.edu.cn",
            homepage_url="https://example.edu.cn/sim",
            declared_interests=["知识图谱", "数字人文"],
            identity_confirmed=True,
            papers=[
                ManualPaperInput(
                    title="知识图谱构建方法（示例）",
                    year=2024,
                    abstract="本文综述了知识图谱的构建流程与常见方法。（示例摘要，非真实论文）",
                    source_url="https://example.org/paper/1",
                    source_platform="DOI/出版社",
                    keywords=["知识图谱", "数字人文"],
                    user_confirmed=True,
                ),
                ManualPaperInput(
                    title="面向地方志的实体抽取（示例）",
                    year=2023,
                    abstract="本文讨论地方志文本中的实体抽取方法。（示例摘要，非真实论文）",
                    source_url="https://example.org/paper/2",
                    source_platform="DOI/出版社",
                    keywords=["实体抽取", "数字人文"],
                    user_confirmed=True,
                ),
            ],
        ),
        ManualProfessorInput(
            name="示例导师乙",
            institution="示例大学",
            department="计算机学院",
            title="副教授",
            declared_interests=["机器学习", "知识图谱"],
            identity_confirmed=True,
            papers=[
                ManualPaperInput(
                    title="图神经网络在推荐系统中的应用（示例）",
                    year=2025,
                    abstract="本文比较了几种图神经网络在推荐场景的表现。（示例摘要）",
                    source_url="https://example.org/paper/3",
                    source_platform="DOI/出版社",
                    keywords=["机器学习", "图神经网络"],
                    user_confirmed=True,
                )
            ],
        ),
    ]

    run_ids: list[str] = []
    for professor in professors:
        result = run_manual_pipeline(
            student=student,
            confirmed_fact_ids=confirmed,
            professor_input=professor,
            llm=NullLLM(),
            repository=repo,
            paper_read_confirmed=False,
        )
        repo.set_run_name(result.run_id, f"{professor.name} · {professor.institution}")
        # 每条记录都配一份示例轨迹：回看历史时能看到 Agent 做过什么，
        # 也让演示不依赖"先点哪条记录"。
        repo.save_trace(result.run_id, demo_agent_trace(professor.name))
        run_ids.append(result.run_id)
    return run_ids


def main() -> int:
    parser = argparse.ArgumentParser(description="生成虚构的演示数据")
    parser.add_argument("--out", default=str(ROOT / ".tmp" / "demo-data"), help="输出目录")
    args = parser.parse_args()

    data_dir = Path(args.out)
    data_dir.mkdir(parents=True, exist_ok=True)
    advisors = seed_advisors(data_dir)
    run_ids = seed_runs(data_dir)

    print(f"[完成] 演示数据已生成：{data_dir}")
    print(f"       导师库 {advisors} 位（全部虚构）；已完成研究记录 {len(run_ids)} 条")
    print("       启动方式：")
    print(f'       $env:DATA_DIR="{data_dir}"; .\\.venv\\Scripts\\python.exe'
          " -m streamlit run app.py")
    print("       提醒：这些数据全是编造的，不要当成真实导师资料使用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
