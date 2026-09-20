"""Agent 工具箱的契约与实现测试。

工具契约（名字 / 说明 / 参数 schema / 权限）是 Harness 的一部分：写错了模型就会
调错、或者看到一个"存在但调不通"的工具。这里把契约钉死，并测两个新工具的行为。
"""

from __future__ import annotations

import pytest

from advisor_fit.agents.tools import (
    TOOL_SPECS,
    build_registry,
    make_affiliation_tool,
    make_homepage_tool,
    tool_names,
)

ALLOWED_PERMISSIONS = {"read", "network", "write"}


# -- 契约 ----------------------------------------------------------------------


def test_every_tool_declares_a_usable_contract():
    assert tool_names(), "工具箱不能为空"
    for name, spec in TOOL_SPECS.items():
        assert spec.name == name
        assert spec.description.strip(), f"{name} 缺少给模型看的说明"
        assert spec.permission in ALLOWED_PERMISSIONS, f"{name} 权限值不合法"
        assert spec.timeout_seconds > 0, f"{name} 必须有超时，否则会挂住整轮"


def test_tool_parameters_are_valid_json_schema_objects():
    for name, spec in TOOL_SPECS.items():
        params = spec.parameters
        assert params.get("type") == "object", f"{name} 的参数必须是 object"
        assert "properties" in params, f"{name} 必须声明 properties"
        required = params.get("required", [])
        for key in required:
            assert key in params["properties"], f"{name} 的必填参数 {key} 没在 properties 里"


def test_network_tools_declare_a_rate_limit():
    """会发外部请求的工具必须声明单轮上限，否则预算闸门管不住它。"""
    for name, spec in TOOL_SPECS.items():
        if spec.permission == "network":
            assert spec.rate_limit_per_run, f"{name} 是联网工具却没声明限流"


def test_build_registry_rejects_missing_implementation():
    with pytest.raises(ValueError, match="缺实现"):
        build_registry({"search_by_author": lambda **_: {}})


def test_build_registry_rejects_undeclared_implementation():
    complete = {name: (lambda **_: {}) for name in tool_names()}
    complete["i_am_not_declared"] = lambda **_: {}
    with pytest.raises(ValueError, match="多出未声明"):
        build_registry(complete)


def test_build_registry_registers_every_declared_tool():
    registry = build_registry({name: (lambda **_: {}) for name in tool_names()})

    assert sorted(registry.names()) == sorted(tool_names())
    assert registry.external_tool_names() == {
        name for name, spec in TOOL_SPECS.items() if spec.permission == "network"
    }


# -- 读导师主页 ----------------------------------------------------------------

HOMEPAGE_HTML = """<html><head><title>王伟 - 示例大学信息管理学院</title></head>
<body>
<p>职称：教授</p>
<p>邮箱：wangwei@example.edu.cn</p>
<p>研究方向：知识图谱、数字人文</p>
</body></html>"""


def test_homepage_tool_extracts_profile_fields():
    tool = make_homepage_tool(fetcher=lambda url: HOMEPAGE_HTML)

    result = tool(url="https://example.edu.cn/wangwei")

    assert result["email"] == "wangwei@example.edu.cn"
    assert result["title_text"] == "教授"
    assert result["declared_interests"] == "知识图谱、数字人文"
    assert "示例大学信息管理学院" in result["title"]
    assert result["text_chars"] > 0


def test_homepage_tool_rejects_non_http_url():
    tool = make_homepage_tool(fetcher=lambda url: HOMEPAGE_HTML)

    assert "error" in tool(url="file:///etc/passwd")
    assert "error" in tool(url="")


def test_homepage_tool_reports_fetch_failure_instead_of_raising():
    """工具失败要作为结果回传给模型，而不是把整轮打断。"""

    def boom(url: str) -> str:
        raise TimeoutError("连接超时")

    tool = make_homepage_tool(fetcher=boom)

    result = tool(url="https://example.edu.cn/slow")

    assert "error" in result
    assert "TimeoutError" in result["error"]


# -- 核对候选论文机构 ----------------------------------------------------------


def _papers(*institutions: str) -> list[dict]:
    return [
        {"title": f"论文{i}", "institution": inst}
        for i, inst in enumerate(institutions)
    ]


def test_affiliation_tool_counts_match_conflict_and_unknown():
    papers = _papers("示例大学", "示例大学", "别的大学", "")
    tool = make_affiliation_tool(lambda: papers, institution="示例大学")

    result = tool()

    assert result["total"] == 4
    assert result["match"] == 2
    assert result["conflict"] == 1
    assert result["unknown"] == 1


def test_affiliation_tool_asks_for_review_when_conflicts_are_many():
    papers = _papers("别的大学", "另一所大学", "示例大学")
    tool = make_affiliation_tool(lambda: papers, institution="示例大学")

    result = tool()

    assert result["conflict"] == 2
    assert "人工确认" in result["verdict"]
    assert result["conflicting_titles"]


def test_affiliation_tool_reads_the_live_candidate_set():
    """候选集是每轮变的，工具必须在调用时取，不能把第一轮的结果缓存住。"""
    papers: list[dict] = []
    tool = make_affiliation_tool(lambda: papers, institution="示例大学")

    assert tool()["total"] == 0

    papers.extend(_papers("示例大学", "示例大学"))

    assert tool()["total"] == 2
    assert tool()["match"] == 2


def test_affiliation_tool_is_honest_when_institutions_are_missing():
    tool = make_affiliation_tool(lambda: _papers("", ""), institution="示例大学")

    result = tool()

    assert result["unknown"] == 2
    assert "无法判断" in result["verdict"]


def test_affiliation_tool_without_target_institution_cannot_judge():
    """没填目标学校时不该硬判"一致"，只能标未知。"""
    tool = make_affiliation_tool(lambda: _papers("示例大学"), institution="")

    result = tool()

    assert result["unknown"] == 1
    assert result["match"] == 0
