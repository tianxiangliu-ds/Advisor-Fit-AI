"""测试共享 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def cv_text() -> str:
    return (FIXTURES_DIR / "cv_text.txt").read_text(encoding="utf-8")
