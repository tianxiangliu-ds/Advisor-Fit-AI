"""Keep form values when Streamlit removes widgets on another workflow screen."""

from collections.abc import MutableMapping, Sequence
from typing import Any


def remember_widgets(state: MutableMapping[str, Any], keys: Sequence[str]) -> None:
    saved = state.setdefault("ui_saved_widgets", {})
    for key in keys:
        if key in state:
            saved[key] = state[key]


def restore_widgets(state: MutableMapping[str, Any], keys: Sequence[str]) -> None:
    saved = state.get("ui_saved_widgets", {})
    for key in keys:
        if key not in state and key in saved:
            state[key] = saved[key]


def forget_widgets(state: MutableMapping[str, Any], keys: Sequence[str]) -> None:
    saved = state.get("ui_saved_widgets", {})
    for key in keys:
        saved.pop(key, None)
