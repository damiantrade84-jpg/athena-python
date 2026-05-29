"""Pure exit-mode policy for Engine A trades.

Single source of truth for: resolving the effective exit mode (per-trade ->
per-group -> global), clamping SL/TP to a per-group advisable-pip band, and
the management predicates consumers branch on. Imports nothing from the
project (notably NOT config.py, whose import aborts on the real-orders gate)
and does no I/O — callers inject config maps, prices, and pip_size.
"""

from __future__ import annotations

EXIT_MODE_STATIC = "traditional_static"
EXIT_MODE_ADAPTIVE = "adaptive_trail"
EXIT_MODE_MANUAL = "manual"
EXIT_MODE_TIME = "time_based"

VALID_EXIT_MODES = frozenset(
    {EXIT_MODE_STATIC, EXIT_MODE_ADAPTIVE, EXIT_MODE_MANUAL, EXIT_MODE_TIME}
)

# Ultimate fallback when neither per-trade, per-group, nor global config yields a
# recognized mode. traditional_static matches the user-authorized Engine-A default
# (see spec). Consumers still pass the config global default explicitly.
DEFAULT_EXIT_MODE = EXIT_MODE_STATIC


def normalize_mode(mode: str | None) -> str | None:
    """Return the canonical exit-mode string, or None if unrecognized."""
    if not mode:
        return None
    m = str(mode).strip().lower()
    return m if m in VALID_EXIT_MODES else None


def group_default_for(group_key: str | None, group_map: dict | None) -> str | None:
    """Look up a group's default exit mode from a config map; normalized or None."""
    if not group_key or not isinstance(group_map, dict):
        return None
    return normalize_mode(group_map.get(group_key))


def resolve_exit_mode(
    per_trade: str | None = None,
    group_default: str | None = None,
    global_default: str | None = DEFAULT_EXIT_MODE,
) -> str:
    """Effective mode = per-trade override -> per-group default -> global default.

    Unrecognized values at any level are skipped (fall through). Always returns a
    valid mode; final fallback is DEFAULT_EXIT_MODE.
    """
    for candidate in (per_trade, group_default, global_default):
        norm = normalize_mode(candidate)
        if norm is not None:
            return norm
    return DEFAULT_EXIT_MODE
