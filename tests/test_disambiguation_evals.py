"""消歧评测 golden set 的结构校验（防回归：保证数据文件合法）。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _load_golden() -> list[dict]:
    path = ROOT / "evals" / "disambiguation_golden.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_golden_set_has_minimum_cases_and_valid_labels():
    cases = _load_golden()
    assert len(cases) >= 5
    for case in cases:
        assert case["name"]
        assert case.get("institution")
        for cand in case["candidates"]:
            assert cand["title"]
            assert cand["expected"] in ("match", "exclude", "review")
            assert "institution" in cand
            assert "authors" in cand
