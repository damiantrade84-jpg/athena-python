"""Pure helpers for converting top-down scores into persisted signals."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any


def json_safe_component(value: Any) -> Any:
    if is_dataclass(value):
        return json_safe_component(asdict(value))
    if isinstance(value, SimpleNamespace):
        return json_safe_component(vars(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): json_safe_component(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe_component(item) for item in value]
    return value
