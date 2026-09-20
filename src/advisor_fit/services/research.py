"""Agent 服务层：把「跑一次导师研究」从界面里解出来。

为什么单独一层：Harness 与 Agent 本来就不依赖 Streamlit，但"怎么组装检索源、
怎么管预算、怎么收轨迹"这套编排逻辑此前写在 `app.py` 里。抽出来之后，同一套能力
可以同时被 Streamlit 前端和 HTTP 接口使用——这才是"可复用 Harness"该有的样子，
而不是"换一个前端就要重写一遍流程"。

这一层只依赖 `advisor_fit` 内部模块，**不依赖 FastAPI 也不依赖 Streamlit**，
所以能脱离 Web 框架单测。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from advisor_fit.agents.research import ResearchResult, research_professor
from advisor_fit.config import settings
from advisor_fit.harness.budget import BudgetLimits, BudgetTracker
from advisor_fit.harness.trace import RunTrace
from advisor_fit.llm.provider import NullLLM, build_llm


@dataclass(frozen=True)
class ResearchRequest:
    """一次导师研究的输入。字段与 `research_professor` 的参数一一对应。"""

    name: str
    institution: str = ""
    english_name: str = ""
    source: str = "auto"          # auto | zh | en
    discipline: str = ""
    seed_titles: list[str] = field(default_factory=list)
    known_directions: list[str] = field(default_factory=list)


@dataclass
class ResearchOutcome:
    """一次导师研究的完整产出：候选论文 + 运行轨迹 + 来源说明。"""

    papers: list[dict[str, Any]] = field(default_factory=list)
    needs_confirmation: str = ""
    # 逐步日志：确认门控要带着它续跑，所以必须原样传回调用方
    steps: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    sources: str = ""
    discipline: str = ""
    degraded_reason: str = ""
    health: dict[str, Any] | None = None
    mode: str = "agent"

    @property
    def ok(self) -> bool:
        """这次运行有没有拿到可用结果。空结果不算失败——可能真的没有论文。"""
        return not self.degraded_reason or bool(self.papers)


def source_health(provider) -> dict[str, Any] | None:
    """记录这次检索的"来源健康度"，用来区分「没查成」和「查了但没有」。

    这两件事对用户的意义完全不同：
    - 所有来源都失败 → 系统侧问题，应当重试或换来源，**不能让人以为这位导师没有论文**；
    - 来源正常返回但没有结果 → 多半是姓名/机构对不上，应当换关键词或手动补录。
    """
    try:
        outcomes = list(provider.outcome().outcomes)
    except Exception:  # noqa: BLE001 - 取不到健康度不影响检索结果
        return None
    searched = [item for item in outcomes if item.searched]
    failed = [item for item in outcomes if item.status in ("blocked", "error")]
    return {
        "total": len(outcomes),
        "searched": len(searched),
        "failed": len(failed),
        "failed_labels": [item.label for item in failed][:3],
    }


def build_provider(budget: BudgetTracker):
    """按当前配置组装检索路由器（多源分流 + 预算记账）。"""
    from advisor_fit.providers.router import SearchRouter

    return SearchRouter(
        key_values={
            "wanfang_app_key": settings.wanfang_app_key,
            "aminer_api_key": settings.aminer_api_key,
        },
        budget=budget,
        contact_email=settings.contact_email,
    )


def describe_source_health(health: dict[str, Any] | None) -> str:
    """把健康度翻译成一句给用户看的话。"""
    if not health:
        return ""
    if health.get("searched", 0) == 0:
        labels = "、".join(health.get("failed_labels") or []) or "全部来源"
        return f"这次一个来源都没查成（{labels} 未返回）——不代表这位导师没有论文"
    return ""


def run_research(
    request: ResearchRequest,
    *,
    llm=None,
    provider=None,
    limits: BudgetLimits | None = None,
    max_steps: int = 6,
    resume_steps: list[dict[str, Any]] | None = None,
    granted_confirmations: list[str] | None = None,
    llm_factory: Callable[[], Any] | None = None,
    provider_factory: Callable[[BudgetTracker], Any] | None = None,
) -> ResearchOutcome:
    """跑一次导师研究，返回候选论文、轨迹与来源说明。

    可注入 `llm` / `provider` 便于测试；不注入时按配置构建（没有 Key 时自动
    退化为确定性规则路径，核心流程仍然可用）。

    `resume_steps` / `granted_confirmations` 用于**确认门控的续跑**：Agent 停下来
    问"这批论文是不是同名的人"之后，前端带着已产生的步骤和用户已授权的答复再调
    一次，循环会接着走而不是从头再来。
    """
    budget = BudgetTracker(limits) if limits is not None else BudgetTracker()
    if provider is None:
        factory = provider_factory or build_provider
        provider = factory(budget)
    if llm is None:
        factory_llm = llm_factory or build_llm
        llm = factory_llm()

    trace = RunTrace(task=f"检索导师「{request.name}」的候选论文")
    result: ResearchResult = research_professor(
        llm,
        provider,
        name=request.name,
        institution=request.institution or None,
        english_name=request.english_name or None,
        source=request.source if request.source in ("zh", "en") else "auto",
        discipline=request.discipline or None,
        seed_titles=request.seed_titles or None,
        known_directions=request.known_directions or None,
        max_steps=max_steps,
        resume_steps=resume_steps,
        granted_confirmations=granted_confirmations,
        budget=budget,
        trace=trace,
    )

    health = source_health(provider)
    return ResearchOutcome(
        papers=result.papers,
        needs_confirmation=result.needs_confirmation,
        steps=result.log,
        trace=trace.to_dict(),
        sources=provider.describe() if hasattr(provider, "describe") else "",
        discipline=provider.discipline_text() if hasattr(provider, "discipline_text") else "",
        degraded_reason=trace.degraded_reason,
        health=health,
        mode=trace.mode,
    )


def llm_configured() -> bool:
    """当前是否配了大模型。没配时走确定性规则路径，功能不缺失但少了决策能力。"""
    try:
        return not isinstance(build_llm(), NullLLM)
    except Exception:  # noqa: BLE001 - 配置坏掉时按"没配"处理
        return False
