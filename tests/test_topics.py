"""Task 7：时间敏感主题趋势测试。"""

from __future__ import annotations

from advisor_fit.analysis.topics import infer_trend
from advisor_fit.providers.academic import Work

_counter = 0


def _work(year: int, topics: list[str]) -> Work:
    global _counter
    _counter += 1
    return Work(id=f"W{_counter}", title="T", year=year, topics=topics)


def test_single_paper_never_becomes_research_shift():
    trend = infer_trend([_work(year=2026, topics=["Agentic RAG"])], current_year=2026)
    assert trend.status == "INSUFFICIENT_EVIDENCE"


def test_trend_requires_three_works_across_two_years():
    works = [_work(2026, ["RAG"]), _work(2025, ["RAG"]), _work(2025, ["RAG"])]
    trend = infer_trend(works, current_year=2026)
    assert trend.status in {"EMERGING", "SUSTAINED"}
    assert len(trend.evidence_ids) == 3


def test_two_works_single_year_is_insufficient():
    works = [_work(2026, ["RAG"]), _work(2026, ["RAG"])]
    trend = infer_trend(works, current_year=2026)
    assert trend.status == "INSUFFICIENT_EVIDENCE"
