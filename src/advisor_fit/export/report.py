"""导出 Markdown / JSON 报告（不含原始 CV 文本与 API Key）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def export_json(result: Any) -> str:
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)


def export_markdown(result: Any) -> str:
    lines: list[str] = []
    lines.append("# 导师研究报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now(UTC).isoformat()}")
    lines.append(f"- 运行 ID：{result.run_id}")

    # 身份决策
    lines.append("")
    lines.append("## 身份确认")
    resolution = result.resolution
    lines.append(f"- 状态：{resolution.status.value}")
    if resolution.selected_author_id:
        lines.append(f"- 选定作者：{resolution.selected_author_id}")
    if resolution.reasons:
        lines.append(f"- 依据：{', '.join(resolution.reasons)}")
    if resolution.conflicts:
        lines.append(f"- 冲突：{', '.join(resolution.conflicts)}")

    # 导师画像
    lines.append("")
    lines.append("## 导师画像")
    prof = result.professor
    for field in prof.iter_asserted_fields():
        lines.append(f"- {field.key}：{field.value}")
    if prof.declared_interests:
        lines.append(
            "- 官网声明方向：" + "、".join(t.topic for t in prof.declared_interests)
        )
    if prof.observed_recent_topics:
        lines.append(
            "- 近年论文观察主题："
            + "、".join(f"{t.topic}({t.trend})" for t in prof.observed_recent_topics)
        )
    lines.append(f"- 招生状态：{prof.recruiting.status.value}")

    # 匹配
    lines.append("")
    lines.append("## 匹配分析")
    lines.append(f"- 研究匹配：{result.match_report.research_fit.value}")
    lines.append(f"- 建议：{result.match_report.recommendation.value}")
    lines.append(f"- 机会信号：{result.match_report.opportunity_signal}")
    if result.match_report.strengths:
        lines.append("- 强项：" + "、".join(d.label for d in result.match_report.strengths))
    if result.match_report.gaps:
        lines.append("- 缺口：" + "、".join(result.match_report.gaps))
    if result.match_report.questions_to_ask:
        lines.append("- 待询问：" + "、".join(result.match_report.questions_to_ask))

    # 证据
    lines.append("")
    lines.append("## 证据来源")
    if result.evidences:
        for ev in result.evidences:
            lines.append(f"- [{ev.source_type}] {ev.title or ev.id}（{ev.source_url or ''}）")
    else:
        lines.append("- 无")

    # 邮件草稿
    lines.append("")
    lines.append("## 邮件草稿")
    if result.draft.subject:
        lines.append(f"主题：{result.draft.subject}")
    for sentence in result.draft.sentences:
        lines.append(f"- {sentence.text}")

    # 警告
    if result.warnings:
        lines.append("")
        lines.append("## 警告")
        for warning in result.warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines)
