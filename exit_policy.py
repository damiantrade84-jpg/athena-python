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


def clamp_to_advisable_pip(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    pip_size: float,
    min_pip: float | None = None,
    max_pip: float | None = None,
) -> dict:
    """Clamp SL distance to a per-group advisable-pip band, RR-preserving.

    Returns {"sl", "tp1", "tp2", "clamped": bool}. No-op (clamped=False) when
    pip_size<=0, SL distance is 0, or neither bound is a positive number.
    direction: "LONG" or "SHORT". If max_pip < min_pip, max wins.
    """
    out = {"sl": sl, "tp1": tp1, "tp2": tp2, "clamped": False}
    if not pip_size or pip_size <= 0:
        return out
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return out

    lo = min_pip * pip_size if (min_pip is not None and min_pip > 0) else None
    hi = max_pip * pip_size if (max_pip is not None and max_pip > 0) else None
    if lo is None and hi is None:
        return out

    new_dist = sl_dist
    if lo is not None and new_dist < lo:
        new_dist = lo
    if hi is not None and new_dist > hi:  # max wins even if hi < lo
        new_dist = hi
    if new_dist == sl_dist:
        return out

    rr1 = abs(tp1 - entry) / sl_dist
    rr2 = abs(tp2 - entry) / sl_dist
    d = str(direction).upper()
    if d == "LONG":
        new_sl = entry - new_dist
        new_tp1 = entry + rr1 * new_dist
        new_tp2 = entry + rr2 * new_dist
    else:
        new_sl = entry + new_dist
        new_tp1 = entry - rr1 * new_dist
        new_tp2 = entry - rr2 * new_dist
    return {"sl": new_sl, "tp1": new_tp1, "tp2": new_tp2, "clamped": True}


def uses_trail_management(mode: str | None) -> bool:
    """True only for adaptive_trail — the only mode the chandelier/profit-protect runs for."""
    return normalize_mode(mode) == EXIT_MODE_ADAPTIVE


def uses_fixed_broker_tp(mode: str | None) -> bool:
    """True when the broker order carries a fixed TP and no trailing manages it."""
    return normalize_mode(mode) in (EXIT_MODE_STATIC, EXIT_MODE_MANUAL, EXIT_MODE_TIME)


def uses_timed_close(mode: str | None) -> bool:
    """True only for time_based — a timed close runs alongside the fixed SL/TP bracket."""
    return normalize_mode(mode) == EXIT_MODE_TIME
