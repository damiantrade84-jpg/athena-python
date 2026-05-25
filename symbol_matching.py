"""Small symbol matching helpers for execution-safety checks."""

from __future__ import annotations

from typing import Any


def symbol_match_keys(value: Any) -> set[str]:
    text = str(value or "").strip().upper()
    if not text:
        return set()
    variants = {text}
    if ":" in text:
        left, _right = text.rsplit(":", 1)
        left_compact = "".join(ch for ch in left.replace("=X", "") if ch.isalnum())
        right_compact = "".join(ch for ch in _right.replace("=X", "") if ch.isalnum())
        if right_compact and left_compact.endswith(right_compact):
            variants.add(left)
        elif right_compact:
            variants.add(_right)

    keys: set[str] = set()
    for variant in variants:
        if not variant:
            continue
        no_yahoo_fx_suffix = variant.replace("=X", "")
        compact = "".join(ch for ch in no_yahoo_fx_suffix if ch.isalnum())
        if no_yahoo_fx_suffix:
            keys.add(no_yahoo_fx_suffix)
        if compact:
            keys.add(compact)
    return keys


def symbols_match(a: Any, b: Any) -> bool:
    return bool(symbol_match_keys(a) & symbol_match_keys(b))
