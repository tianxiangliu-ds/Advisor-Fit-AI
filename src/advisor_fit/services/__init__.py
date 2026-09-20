"""服务层：与界面、Web 框架都无关的用例编排。

`advisor_fit.services.research` 提供"跑一次导师研究"的完整流程。它不导入
Streamlit，也不导入 FastAPI——前端（页面 / HTTP 接口 / 命令行 / 批处理）都调用
它，而不是各自重写一遍流程。这才是"Harness 与界面解耦"的实际含义。
"""

from advisor_fit.services.research import (
    ResearchOutcome,
    ResearchRequest,
    build_provider,
    describe_source_health,
    llm_configured,
    run_research,
    source_health,
)

__all__ = [
    "ResearchOutcome",
    "ResearchRequest",
    "build_provider",
    "describe_source_health",
    "llm_configured",
    "run_research",
    "source_health",
]
