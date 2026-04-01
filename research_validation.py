"""Explicit, auditable research validation modes for backtests.

Temporal gates (embargo / walk-forward) and reporting live here so the default
``standard`` backtest path stays obvious and unchanged in behavior unless a mode
is explicitly selected.

All helpers are deterministic (no randomness). Unknown ``validation_mode``
values fall back to ``standard`` with a note.
"""

from __future__ import annotations

from typing import Any

ALLOWED_VALIDATION_MODES: tuple[str, ...] = (
    "standard",
    "embargoed",
    "walk_forward",
    "walk_forward_cv",
    "live_parity",
)


def normalize_validation_mode(mode: str | None) -> tuple[str, str | None]:
    """Return (canonical_mode, warning_or_none).

    ``live_parity`` is preserved for reporting; temporal splitting uses
    ``standard`` (see ``temporal_validation_mode``).
    """
    raw = (mode or "standard").strip().lower()
    if raw in ALLOWED_VALIDATION_MODES:
        return raw, None
    return "standard", f"unknown_validation_mode:{raw!r}_using_standard"


def temporal_validation_mode(canonical_mode: str) -> str:
    """Mode used for purge / walk-forward / OOS labeling (not execution stress)."""
    if canonical_mode == "live_parity":
        return "standard"
    return canonical_mode


def backtest_bar_validation_state(
    i: int,
    *,
    min_bars: int,
    total_bars: int,
    temporal_mode: str,
    purge_gap: int,
    folds: int,
) -> dict[str, Any]:
    """Per-bar gate for sequential Engine A/B backtest loops.

    Returns:
        skip: skip simulating a trade on this bar (embargo / WF purge band)
        oos_label: tag trades taken on this bar with oos=True
        wf_fold: walk-forward fold index when applicable
        oos_boundary_index: boundary index used for the oos_label (auditable)
    """
    pg = max(0, int(purge_gap))
    fd = max(1, int(folds))
    base_oos = min_bars + int((total_bars - min_bars) * 0.7)

    if i < min_bars:
        return {
            "skip": False,
            "oos_label": False,
            "wf_fold": None,
            "oos_boundary_index": base_oos,
        }

    tm = temporal_mode
    if tm == "standard":
        return {
            "skip": False,
            "oos_label": i >= base_oos,
            "wf_fold": None,
            "oos_boundary_index": base_oos,
        }

    if tm == "embargoed":
        purge_start = max(min_bars, base_oos - pg)
        if purge_start <= i < base_oos:
            return {
                "skip": True,
                "oos_label": False,
                "wf_fold": None,
                "oos_boundary_index": base_oos,
            }
        return {
            "skip": False,
            "oos_label": i >= base_oos,
            "wf_fold": None,
            "oos_boundary_index": base_oos,
        }

    if tm in ("walk_forward", "walk_forward_cv"):
        fold_size = max(1, int((total_bars - min_bars) / fd))
        current_fold = min(fd - 1, max(0, (i - min_bars) // fold_size))
        fold_oos_start = min_bars + current_fold * fold_size + int(fold_size * 0.7)
        purge_fold_start = max(min_bars, fold_oos_start - pg)
        if purge_fold_start <= i < fold_oos_start:
            return {
                "skip": True,
                "oos_label": False,
                "wf_fold": current_fold,
                "oos_boundary_index": fold_oos_start,
            }
        return {
            "skip": False,
            "oos_label": i >= fold_oos_start,
            "wf_fold": current_fold,
            "oos_boundary_index": fold_oos_start,
        }

    return {
        "skip": False,
        "oos_label": i >= base_oos,
        "wf_fold": None,
        "oos_boundary_index": base_oos,
    }


def _result_r(t: dict[str, Any]) -> float:
    try:
        v = t.get("resultR", t.get("r_multiple", 0))
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _expectancy(trades: list[dict[str, Any]]) -> float | None:
    if not trades:
        return None
    s = sum(_result_r(t) for t in trades)
    return round(s / len(trades), 6)


def _win_rate_pct(trades: list[dict[str, Any]]) -> float | None:
    if not trades:
        return None
    w = sum(1 for t in trades if _result_r(t) > 0)
    return round(100.0 * w / len(trades), 2)


def _sqn(trades: list[dict[str, Any]]) -> float | None:
    if len(trades) < 2:
        return None
    rv = [_result_r(t) for t in trades]
    avg = sum(rv) / len(rv)
    var = sum((r - avg) ** 2 for r in rv) / (len(rv) - 1)
    if var <= 0 or avg == 0:
        return 0.0
    from math import sqrt

    sqn_v = (avg / sqrt(var)) * (len(rv) ** 0.5)
    return round(max(-10.0, min(10.0, sqn_v)), 4)


def _ratio(oos_val: float | None, is_val: float | None) -> float | None:
    if oos_val is None or is_val is None:
        return None
    if is_val == 0:
        return None
    return round(oos_val / is_val, 6)


def summarize_walk_forward_folds(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-fold stats when trades include ``wf_fold``."""
    buckets: dict[int, list[dict[str, Any]]] = {}
    for t in trades or []:
        f = t.get("wf_fold")
        if f is None:
            continue
        try:
            fi = int(f)
        except (TypeError, ValueError):
            continue
        buckets.setdefault(fi, []).append(t)
    out = []
    for fi in sorted(buckets.keys()):
        bt = buckets[fi]
        out.append(
            {
                "fold": fi,
                "trades": len(bt),
                "expectancyR": _expectancy(bt),
                "winRatePct": _win_rate_pct(bt),
                "sqn": _sqn(bt),
            }
        )
    return out


def regime_breakdown_with_oos(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Regime × (IS/OOS) performance for fragility visibility."""
    regimes: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for t in trades or []:
        label = str(t.get("regime") or "UNKNOWN").upper()
        if label not in regimes:
            regimes[label] = {"is": [], "oos": []}
        bucket = "oos" if t.get("oos") else "is"
        regimes[label][bucket].append(t)

    out: dict[str, Any] = {}
    for reg, parts in sorted(regimes.items()):
        is_t = parts["is"]
        oos_t = parts["oos"]
        is_e = _expectancy(is_t)
        oos_e = _expectancy(oos_t)
        out[reg] = {
            "inSample": {
                "trades": len(is_t),
                "expectancyR": is_e,
                "winRatePct": _win_rate_pct(is_t),
                "sqn": _sqn(is_t),
            },
            "outOfSample": {
                "trades": len(oos_t),
                "expectancyR": oos_e,
                "winRatePct": _win_rate_pct(oos_t),
                "sqn": _sqn(oos_t),
            },
            "oosExpectancyRatioVsIS": _ratio(oos_e, is_e),
            "fragile": (
                len(is_t) < 5
                or len(oos_t) < 3
                or (is_e is not None and is_e > 0 and oos_e is not None and oos_e < 0)
                or (oos_e is not None and is_e is not None and is_e > 0.1 and oos_e < is_e * 0.5)
            ),
        }
    return out


def build_validation_report(
    trades: list[dict[str, Any]],
    *,
    validation_mode: str,
    temporal_mode: str,
    purge_gap: int,
    folds: int,
    mode_warning: str | None = None,
    wf_split: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single payload: how this run was validated and where performance is fragile."""
    is_t = [t for t in (trades or []) if not t.get("oos")]
    oos_t = [t for t in (trades or []) if t.get("oos")]

    is_e = _expectancy(is_t)
    oos_e = _expectancy(oos_t)
    is_wr = _win_rate_pct(is_t)
    oos_wr = _win_rate_pct(oos_t)
    is_sqn = _sqn(is_t)
    oos_sqn = _sqn(oos_t)

    leakage_defended = temporal_mode in ("embargoed", "walk_forward", "walk_forward_cv")
    live_parity_execution = validation_mode == "live_parity"

    if leakage_defended and live_parity_execution:
        run_kind = "LEAKAGE_DEFENDED_PLUS_LIVE_PARITY_STRESS"
    elif leakage_defended:
        run_kind = "LEAKAGE_DEFENDED_VALIDATION"
    elif live_parity_execution:
        run_kind = "LIVE_PARITY_EXECUTION_STRESS"
    else:
        run_kind = "ORDINARY_BACKTEST"

    notes = []
    if mode_warning:
        notes.append(mode_warning)
    if not leakage_defended:
        notes.append(
            "temporal_mode_is_standard: IS/OOS is a fixed 70/30 chronological label "
            "without an embargo between IS and OOS unless you select embargoed/walk_forward*"
        )
    if live_parity_execution:
        notes.append(
            "live_parity: higher slippage multiplier and live volume threshold — "
            "stress test vs default backtest execution assumptions"
        )

    wf = dict(wf_split or {})
    fold_summary = summarize_walk_forward_folds(trades or [])
    include_folds = (
        temporal_mode in ("walk_forward", "walk_forward_cv") and bool(fold_summary)
    )

    degradation = {
        "inSample": {
            "trades": len(is_t),
            "expectancyR": is_e,
            "winRatePct": is_wr,
            "sqn": is_sqn,
        },
        "outOfSample": {
            "trades": len(oos_t),
            "expectancyR": oos_e,
            "winRatePct": oos_wr,
            "sqn": oos_sqn,
        },
        "oosToIsExpectancyRatio": _ratio(oos_e, is_e),
        "oosToIsSqnRatio": _ratio(oos_sqn, is_sqn),
        "oosToIsWinRateRatio": _ratio(
            oos_wr / 100.0 if oos_wr is not None else None,
            is_wr / 100.0 if is_wr is not None else None,
        ),
        "severeOosDegradation": bool(
            is_e is not None
            and oos_e is not None
            and is_e > 0.05
            and oos_e < is_e * 0.5
            and len(oos_t) >= 3
        ),
    }

    return {
        "validationMode": validation_mode,
        "temporalModeUsed": temporal_mode,
        "runClassification": run_kind,
        "isLeakageDefendedTemporal": leakage_defended,
        "isLiveParityExecutionStress": live_parity_execution,
        "parameters": {
            "purgeGapBars": int(purge_gap),
            "walkForwardFolds": int(folds),
        },
        "interpretationNotes": notes,
        "isOosDegradation": degradation,
        "regimeSegmentation": regime_breakdown_with_oos(trades or []),
        "walkForwardFoldSummary": fold_summary if include_folds else [],
        "walkForwardFoldSummaryIncluded": include_folds,
        "wfSplitEcho": wf,
    }


def volume_threshold_for_backtest(
    pair_profile_volume: Any,
    *,
    validation_mode: str,
    default_live: float,
    default_bt: float,
) -> float:
    """Volume gate for calc_confluence: explicit live parity uses live default unless profile overrides."""
    if pair_profile_volume is not None:
        try:
            return float(pair_profile_volume)
        except (TypeError, ValueError):
            pass
    if validation_mode == "live_parity":
        return float(default_live)
    return float(default_bt)
