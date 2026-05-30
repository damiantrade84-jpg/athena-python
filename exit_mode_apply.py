"""Apply the resolved Engine A exit mode to a signal before execution.

Shared by execution.py (manual quick_execute + execute) and auto_trader.py
(autopilot) so every Engine A fill resolves the same exit mode and advisable-pip
clamp (audit finding H1: the mode must not be silently ignored on some paths).

Pure dependencies only: exit_policy (pure) + a lazily-imported score-group
resolver. Mutates the signal dict in place; returns the resolved mode or None.
"""

from __future__ import annotations

import exit_policy


def pip_size(symbol_info: dict | None, asset_class: str | None) -> float:
    """Derive pip size from broker symbol info. FX (3/5-digit) pip = 10*point;
    other classes use the raw point. Returns 0.0 when point is unavailable."""
    try:
        point = float((symbol_info or {}).get("point") or 0)
    except (TypeError, ValueError):
        return 0.0
    if point <= 0:
        return 0.0
    digits = (symbol_info or {}).get("digits")
    if str(asset_class or "").lower() in ("forex", "fx") and digits in (3, 5):
        return point * 10
    return point


def apply_engine_a_exit_mode(
    sig: dict,
    engine: str | None,
    symbol_info: dict | None,
    cfg: dict,
    level_override: dict | None = None,
    score_group_resolver=None,
) -> str | None:
    """Resolve + stamp the Engine A exit mode and (non-manual) apply the
    advisable-pip clamp. No-op unless ``engine`` is Engine A.

    Mutates ``sig``: sets ``sig['exit_mode']`` and, for non-manual modes that
    have a configured pip band, clamps ``sl``/``tp1``/``tp2`` (RR-preserving).
    Returns the resolved mode, or None when not Engine A.

    The caller passes the engine it already resolved. ``score_group_resolver``
    is injectable for tests; defaults to scoring.get_pair_score_group.
    """
    if str(engine or "").strip().lower() != "engine_a":
        return None

    if score_group_resolver is None:
        from scoring import get_pair_score_group
        score_group_resolver = get_pair_score_group
    # get_pair_score_group takes the pair/signal dict (reads score_group/display/type).
    score_group = score_group_resolver(sig)

    group_map = cfg.get("ENGINE_A_EXIT_MODE_BY_SCORE_GROUP") or {}
    per_trade = (level_override or {}).get("exit_mode") or sig.get("exit_mode")
    mode = exit_policy.resolve_exit_mode(
        per_trade=per_trade,
        group_default=exit_policy.group_default_for(score_group, group_map),
        global_default=cfg.get(
            "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT", exit_policy.DEFAULT_EXIT_MODE
        ),
    )
    sig["exit_mode"] = mode

    if mode == exit_policy.EXIT_MODE_MANUAL:
        return mode  # manual: user's exact levels win, no clamp (spec Open Q3)

    bounds = (cfg.get("ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP") or {}).get(score_group) or {}
    psize = pip_size(symbol_info, sig.get("type") or sig.get("asset_class"))
    try:
        entry = float(sig.get("price") or sig.get("livePrice") or 0)
    except (TypeError, ValueError):
        entry = 0.0
    if psize > 0 and entry > 0 and sig.get("sl") and sig.get("tp1"):
        clamped = exit_policy.clamp_to_advisable_pip(
            str(sig.get("direction") or "").upper(),
            entry,
            float(sig["sl"]),
            float(sig["tp1"]),
            float(sig.get("tp2") or sig["tp1"]),
            psize,
            bounds.get("min_pip"),
            bounds.get("max_pip"),
        )
        if clamped["clamped"]:
            sig["sl"] = clamped["sl"]
            sig["tp1"] = clamped["tp1"]
            sig["tp2"] = clamped["tp2"]
    return mode
