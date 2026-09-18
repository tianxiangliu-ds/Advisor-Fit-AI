"""作者身份消歧与人工确认门控。"""

from advisor_fit.resolution.author import (
    ResolutionCase,
    ResolutionResult,
    ResolutionStatus,
    evaluate_resolution,
    resolve_author,
)

__all__ = [
    "ResolutionCase",
    "ResolutionResult",
    "ResolutionStatus",
    "evaluate_resolution",
    "resolve_author",
]
