"""Config-gated V2 filter ports applied inside Engine A V3 quant scoring."""

from __future__ import annotations

from typing import Any, Mapping


def _legacy_cfg() -> dict[str, Any]:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_LEGACY_FILTERS") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def legacy_filters_enabled() -> bool:
    return bool(_legacy_cfg().get("ENABLED", True))


def _flag(name: str, default: bool = True) -> bool:
    cfg = _legacy_cfg()
    if not cfg.get("ENABLED", True):
        return False
    return bool(cfg.get(name, default))


def resolve_v3_trend_state(adx_value: float | None, asset_type: str) -> str:
    """Mirror scoring._resolve_trend_state ADX buckets for V3 demotion."""
    if adx_value is None:
        return "UNKNOWN"
    try:
        from config import CONFIG

        _rng = CONFIG["RANGING"].get(asset_type, CONFIG["RANGING"]["commodity"])
        _trending_cls = CONFIG.get("TRENDING_ADX_CLASS") or {}
        _developing_cls = CONFIG.get("DEVELOPING_ADX_CLASS") or {}
        trending_adx = float(_trending_cls.get(asset_type, CONFIG.get("TRENDING_ADX", 35)))
        developing_adx = float(
            _developing_cls.get(asset_type, CONFIG.get("DEVELOPING_ADX", 25))
        )
        if adx_value >= trending_adx:
            return "TRENDING"
        if adx_value >= developing_adx:
            return "DEVELOPING"
        if adx_value >= float(_rng["dead"]):
            return "RANGING"
        return "DEAD RANGING"
    except Exception:
        return "UNKNOWN"


def _blocked_states_for_v3(pair_type: str) -> set[str]:
    """Blocked trend labels for V3 live demotion.

    Live scans only honor an explicitly configured "live" scope. A flat
    ENGINE_A_BLOCKED_TREND_STATES list is backtest-only by contract
    (scoring.get_blocked_trend_states) and must never demote live scans.
    """
    try:
        from scoring import get_blocked_trend_states

        return get_blocked_trend_states({"type": pair_type}, scope="live")
    except Exception:
        return set()


def apply_legacy_filters(
    *,
    asset_type: str,
    score_group: str,
    family: str,
    direction: str,
    confluence: float,
    decision: str,
    snaps: Mapping[str, Mapping[str, Any]],
    candles: Mapping[str, list[dict]],
    coherence: Mapping[str, Any],
    mom_diag: Mapping[str, Any],
    entry_tf: str,
    series_cache=None,
    level_style: str = "trend",
    policy: Mapping[str, Any] | None = None,
) -> tuple[float, str, dict[str, Any]]:
    """Return (confluence, decision, diagnostics) after optional V2 filter ports.

    ``level_style`` "mean_reversion" exempts the signal from blocked-trend-state
    demotion: range fades are regime-appropriate in the ranging states the block
    targets, and demoting them would disable the only counter-trend path.
    """
    diag: dict[str, Any] = {
        "legacyFiltersEnabled": legacy_filters_enabled(),
        "forexEmaClusterMult": 1.0,
        "cryptoLateTrendMult": 1.0,
        "trendState": None,
        "trendStateBlocked": False,
    }
    if not legacy_filters_enabled():
        return confluence, decision, diag

    mult = 1.0
    # Anchor on the policy bias rung when policy provenance is supplied — that
    # is the rung the signal's trend judgment actually read. The historical H4
    # anchor is only the no-policy fallback: a scalp-style signal whose bias
    # rung is H1/M15 must not be penalised from an H4 cluster it never scored.
    anchor_tf = str(
        (policy or {}).get("bias") or (policy or {}).get("biasTf") or "H4"
    ).upper() or "H4"
    h4_snap = dict(snaps.get(anchor_tf) or snaps.get("H4") or snaps.get(entry_tf) or {})
    h4_candles = candles.get(anchor_tf) or candles.get("H4") or candles.get(entry_tf) or []
    h4_tf = (
        anchor_tf
        if candles.get(anchor_tf)
        else ("H4" if candles.get("H4") else entry_tf)
    )
    diag["legacyFilterAnchorTf"] = anchor_tf

    # ── Forex EMA cluster penalty ───────────────────────────────────────────
    if family == "forex" and _flag("FOREX_EMA_CLUSTER", True) and direction in ("LONG", "SHORT"):
        try:
            from factor_scoring import (
                _apply_forex_ema_cluster_penalty,
                _forex_ema_cluster_diagnostics,
            )

            cluster = _forex_ema_cluster_diagnostics(
                h4_snap, h4_candles, direction,
                series_cache=series_cache, timeframe=h4_tf,
            )
            labels = [str(v) for v in (coherence or {}).values() if v in ("UP", "DOWN", "FLAT")]
            agreement = sum(1 for v in labels if v == ("UP" if direction == "LONG" else "DOWN"))
            trend_detail = {
                **cluster,
                "coherence_ratio": (agreement / len(labels)) if labels else 0.0,
                "agreement_count": agreement,
            }
            adx_quality = {
                "adx_falling": bool(mom_diag.get("adxFalling") or mom_diag.get("adx_falling")),
            }
            cluster_mult, soft_cap, applied, reason = _apply_forex_ema_cluster_penalty(
                trend_detail, adx_quality, direction
            )
            if applied:
                mult *= float(cluster_mult)
                if soft_cap is not None:
                    confluence = min(confluence, float(soft_cap))
            diag["forexEmaClusterMult"] = round(float(cluster_mult), 4)
            diag["forexEmaClusterApplied"] = bool(applied)
            diag["forexEmaClusterReason"] = reason
            diag["forexEmaCluster"] = {
                k: cluster.get(k)
                for k in (
                    "price_inside_ema_cluster",
                    "ema_cluster_width_atr",
                    "clean_continuation",
                    "strong_price_contradiction",
                )
            }
        except Exception as exc:
            diag["forexEmaClusterError"] = type(exc).__name__

    # ── Crypto late-trend adjustment ────────────────────────────────────────
    if family == "crypto" and _flag("CRYPTO_LATE_TREND", True) and direction in ("LONG", "SHORT"):
        try:
            from factor_scoring import (
                _apply_crypto_late_trend_adjustment,
                _crypto_late_trend_diagnostics,
            )

            labels = [str(v) for v in (coherence or {}).values() if v in ("UP", "DOWN", "FLAT")]
            agreement = sum(1 for v in labels if v == ("UP" if direction == "LONG" else "DOWN"))
            trend_detail = {
                "coherence_ratio": (agreement / len(labels)) if labels else 0.0,
                "agreement_count": agreement,
            }
            adx_quality = {
                "adx_slope": mom_diag.get("adxSlope") or mom_diag.get("adx_slope"),
                "adx_falling": bool(mom_diag.get("adxFalling") or mom_diag.get("adx_falling")),
            }
            late_diag = _crypto_late_trend_diagnostics(
                h4_snap,
                h4_candles,
                direction,
                trend_detail,
                adx_quality,
                mom_diag.get("adxValue"),
                series_cache=series_cache,
                timeframe=h4_tf,
            )
            late_mult, applied, reason = _apply_crypto_late_trend_adjustment(
                late_diag, trend_detail, direction
            )
            if applied:
                mult *= float(late_mult)
            diag["cryptoLateTrendMult"] = round(float(late_mult), 4)
            diag["cryptoLateTrendApplied"] = bool(applied)
            diag["cryptoLateTrendReason"] = reason
            diag["cryptoLateTrendRisk"] = bool(late_diag.get("crypto_late_trend_risk"))
        except Exception as exc:
            diag["cryptoLateTrendError"] = type(exc).__name__

    confluence = max(0.0, float(confluence) * mult)

    # ── Blocked trend states → demote TRADE to WATCH ────────────────────────
    adx_val = None
    try:
        adx_val = float(mom_diag.get("adxValue")) if mom_diag.get("adxValue") is not None else None
    except (TypeError, ValueError):
        adx_val = None
    trend_state = resolve_v3_trend_state(adx_val, asset_type)
    diag["trendState"] = trend_state
    if _flag("BLOCKED_TREND_STATES", True) and decision == "TRADE":
        blocked = _blocked_states_for_v3(asset_type)
        if trend_state.upper() in blocked:
            if str(level_style or "").strip().lower() == "mean_reversion":
                diag["trendStateBlockedExempt"] = "mean_reversion"
            else:
                decision = "WATCH"
                diag["trendStateBlocked"] = True

    # Re-check TRADE after confluence mult
    return confluence, decision, diag
