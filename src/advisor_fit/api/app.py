"""HTTP 接口层：把 Agent 能力用 FastAPI 对外暴露。

为什么值得单独做一层：Streamlit 只是**其中一种前端**。有了这层之后，
同一套 Harness / Agent / 工具契约既能被页面调用，也能被命令行、其他服务、
评测脚本调用——这才是"Harness 与界面解耦"的实际含义。

三个端点：

| 端点 | 作用 |
|---|---|
| `GET /health` | 存活与配置状态：版本、是否配了大模型、检索源说明 |
| `GET /tools` | **工具契约**：Agent 手里有哪些工具、参数 schema、权限与限流 |
| `POST /research` | 跑一次导师研究，返回候选论文 + 运行轨迹 + 来源说明 |

`/tools` 是刻意暴露的：工具清单是 Agent 能力的边界，能自省才好排查与对接。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from advisor_fit import __version__
from advisor_fit.agents.tools import TOOL_SPECS
from advisor_fit.services.research import (
    ResearchRequest,
    describe_source_health,
    llm_configured,
    run_research,
)


class ResearchBody(BaseModel):
    """一次导师研究的请求体。"""

    name: str = Field(min_length=1, description="导师姓名（必填）")
    institution: str = Field(default="", description="学校/单位")
    english_name: str = Field(default="", description="英文名，英文库检索时使用")
    source: str = Field(default="auto", description="auto | zh | en")
    discipline: str = Field(default="", description="学科方向，决定补查哪些专业库")
    seed_titles: list[str] = Field(default_factory=list, description="代表论文标题，按标题兜底")
    known_directions: list[str] = Field(default_factory=list, description="已知研究方向")


class ToolBody(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    permission: str
    rate_limit_per_run: int | None = None
    timeout_seconds: float
    cache_ttl_seconds: int


class ResearchResponse(BaseModel):
    papers: list[dict[str, Any]]
    needs_confirmation: str = ""
    trace: dict[str, Any]
    sources: str = ""
    discipline: str = ""
    degraded_reason: str = ""
    health: dict[str, Any] | None = None
    mode: str = "agent"
    note: str = Field(default="", description="给调用方的提醒，例如「结果需人工确认归属」")


def create_app(
    *,
    llm_factory: Callable[[], Any] | None = None,
    provider_factory: Callable[[Any], Any] | None = None,
) -> FastAPI:
    """构建 API 应用。可注入 llm / provider 工厂，便于测试时不联网、不花钱。"""
    app = FastAPI(
        title="AdvisorFit AI · Agent Harness API",
        version=__version__,
        description=(
            "面向导师研究匹配的证据驱动 Agent。"
            "所有结论都可追溯到来源；拿不准的地方返回 needs_confirmation 而不是猜测。"
        ),
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "llm_configured": llm_configured(),
            "mode": "agent" if llm_configured() else "rule",
            "tools": len(TOOL_SPECS),
            "note": (
                "未配置 LLM_API_KEY，将以确定性规则路径运行（功能可用，但没有模型决策）"
                if not llm_configured()
                else ""
            ),
        }

    @app.get("/tools", response_model=list[ToolBody])
    def tools() -> list[ToolBody]:
        """Agent 手里的工具清单——这是它能做的事的全部边界。"""
        return [
            ToolBody(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                permission=spec.permission,
                rate_limit_per_run=spec.rate_limit_per_run,
                timeout_seconds=spec.timeout_seconds,
                cache_ttl_seconds=spec.cache_ttl_seconds,
            )
            for spec in TOOL_SPECS.values()
        ]

    @app.post("/research", response_model=ResearchResponse)
    def research(body: ResearchBody) -> ResearchResponse:
        outcome = run_research(
            ResearchRequest(
                name=body.name.strip(),
                institution=body.institution.strip(),
                english_name=body.english_name.strip(),
                source=body.source,
                discipline=body.discipline,
                seed_titles=[t for t in body.seed_titles if t.strip()],
                known_directions=[d for d in body.known_directions if d.strip()],
            ),
            llm_factory=llm_factory,
            provider_factory=provider_factory,
        )

        if not outcome.papers and outcome.health and outcome.health.get("searched", 0) == 0:
            # 一个来源都没查成属于服务侧故障，用 503 让调用方知道"可以重试"，
            # 而不是把空结果当成"这位导师没有论文"。
            raise HTTPException(status_code=503, detail=describe_source_health(outcome.health))

        return ResearchResponse(
            papers=outcome.papers,
            needs_confirmation=outcome.needs_confirmation,
            trace=outcome.trace,
            sources=outcome.sources,
            discipline=outcome.discipline,
            degraded_reason=outcome.degraded_reason,
            health=outcome.health,
            mode=outcome.mode,
            note=(
                "论文归属需人工确认；本接口不做录取预测，也不判断是否在招生"
                if outcome.papers
                else ""
            ),
        )

    return app
