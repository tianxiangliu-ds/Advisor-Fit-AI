"""Prompt 版本化：所有指令集中在注册表中，模块只引用不内联。"""

from __future__ import annotations

import pytest

from advisor_fit.llm import prompts as prompts_module

# 验收要求：同一套 Harness 能跑这四类任务，每类都必须有可版本化的指令
_REQUIRED_KEYS = (
    "agent_decide",
    "cv_extract",
    "homepage_extract",
    "disambiguation",
    "direction_summary",
    "deep_analysis",
    "claims",
    "drafting",
    "faculty_list",
    "faculty_page",
)


def test_required_prompts_are_registered():
    registered = {prompt.key for prompt in prompts_module.all_prompts()}

    assert set(_REQUIRED_KEYS) <= registered


def test_prompt_text_is_reachable_by_key():
    text = prompts_module.prompt_text("disambiguation")

    assert isinstance(text, str)
    assert "belongs" in text


def test_unknown_prompt_key_raises_keyerror():
    with pytest.raises(KeyError):
        prompts_module.prompt_text("no_such_prompt")


def test_every_prompt_carries_a_version_and_non_empty_text():
    for prompt in prompts_module.all_prompts():
        assert prompt.version, f"{prompt.key} 缺少版本号"
        assert prompt.text.strip(), f"{prompt.key} 内容为空"


def test_module_constants_are_sourced_from_the_registry():
    """回归守卫：模块里的 _INSTRUCTIONS 必须与注册表内容一致，防止有人再内联写死。"""
    from advisor_fit.agents.research import _DISAMBIG_INSTRUCTIONS
    from advisor_fit.harness.loop import _DECIDE_INSTRUCTIONS
    from advisor_fit.ingest.cv_llm import _EXTRACT_INSTRUCTIONS
    from advisor_fit.ingest.homepage import _HOMEPAGE_INSTRUCTIONS
    from advisor_fit.llm.analysis import _ANALYSIS_INSTRUCTIONS, _DIRECTION_INSTRUCTIONS
    from advisor_fit.llm.claims import _CLAIMS_INSTRUCTIONS
    from advisor_fit.llm.drafting import _DRAFT_INSTRUCTIONS

    assert _DECIDE_INSTRUCTIONS == prompts_module.prompt_text("agent_decide")
    assert _DISAMBIG_INSTRUCTIONS == prompts_module.prompt_text("disambiguation")
    assert _EXTRACT_INSTRUCTIONS == prompts_module.prompt_text("cv_extract")
    assert _HOMEPAGE_INSTRUCTIONS == prompts_module.prompt_text("homepage_extract")
    assert _ANALYSIS_INSTRUCTIONS == prompts_module.prompt_text("deep_analysis")
    assert _DIRECTION_INSTRUCTIONS == prompts_module.prompt_text("direction_summary")
    assert _CLAIMS_INSTRUCTIONS == prompts_module.prompt_text("claims")
    assert _DRAFT_INSTRUCTIONS == prompts_module.prompt_text("drafting")
