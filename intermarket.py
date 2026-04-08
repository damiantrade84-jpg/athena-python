"""Intermarket correlation and confirmation helpers."""

from __future__ import annotations

import copy
import itertools
import logging
import math
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


log = logging.getLogger("athena")

# Authoritative forex Engine A score contract cap (0.0–2.0).
# Pass this as max_score= to apply_confirmation_to_score() for ALL forex paths
# (live and backtest) so the cap cannot drift between environments.
FOREX_ENGINE_A_MAX_SCORE: float = 2.0


_DEFAULT_CFG = {
    "enabled": False,
    "analysis_tf": "H4",
    "windows": [20, 50, 90],
    "min_overlap_bars": 100,
    "max_lag_bars": 5,
    "lead_lag_enabled": False,
    "full_scan_time_matrix": True,
    "max_pairs_per_symbol": 12,
    "strong_corr": 0.65,
    "moderate_corr": 0.35,
    "neutral_corr": 0.20,
    "broken_stability": 0.45,
    "flip_lookback_bars": 10,
    "engine_a_enabled": False,
    "engine_a_score_cap": 0.18,
    "engine_c_enabled": False,
    "engine_c_conviction_cap": 0.08,
    "severe_contradiction_blocks": False,
    "asset_class_filters": {
        "forex": ["forex", "commodity", "index", "stock"],
        "commodity": ["forex", "commodity", "index", "stock"],
        "index": ["index", "stock", "etf", "commodity", "forex"],
        "stock": ["stock", "etf", "index", "commodity"],
        "crypto": ["crypto", "index", "stock"],
    },
    "macro_priors": [
        {"pair": "XAU/USD", "driver": "DXY", "relation": "inverse"},
        {"pair": "XAU/USD", "driver": "US10Y_REAL_YIELD_PROXY", "relation": "inverse"},
        {"pair": "EUR/USD", "driver": "DXY", "relation": "inverse"},
        {"pair": "GBP/USD", "driver": "DXY", "relation": "inverse"},
        {"pair": "USD/JPY", "driver": "US10Y_NOMINAL_PROXY", "relation": "positive"},
        {"pair": "USD/CAD", "driver": "WTI_OIL_PROXY", "relation": "inverse"},
        {"pair": "AUD/USD", "driver": "COMMODITY_BASKET_PROXY", "relation": "positive"},
        {"pair": "AUD/USD", "driver": "RISK_SENTIMENT_PROXY", "relation": "positive"},
    ],
}

_SCAN_CACHE_LOCK = threading.Lock()
_SCAN_CACHE: dict[str, Any] = {"key": None, "built_at": 0.0, "snapshot": None}

_SPECIAL_PROXY_PAIRS = {
    "DXY": {
        "symbol": "DX-Y.NYB",
        "display": "DXY",
        "source": "yfinance",
        "type": "index",
        "assetClass": "macro",
        "is_macro_proxy": True,
    },
}

_FILTER_ALIASES = {
    "commodities": "commodity",
    "indices": "index",
    "stocks": "stock",
    "etfs": "etf",
    "rates": "macro",
    "macro": "macro",
}


def intermarket_config(config: dict | None = None) -> dict:
    raw = {}
    if isinstance(config, dict):
        raw = config.get("INTERMARKET_CONFIRMATION", {}) or {}
    out = copy.deepcopy(_DEFAULT_CFG)
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        if key == "asset_class_filters" and isinstance(val, dict):
            out[key] = {
                _normalize_asset_label(k): [_normalize_asset_label(x) for x in (v or [])]
                for k, v in val.items()
            }
        elif key == "macro_priors" and isinstance(val, list):
            out[key] = [dict(x) for x in val if isinstance(x, dict)]
        elif key == "windows" and isinstance(val, (list, tuple)):
            try:
                out[key] = sorted(
                    {max(5, int(float(x))) for x in val if x is not None}
                )
            except (TypeError, ValueError):
                pass
        else:
            out[key] = val
    return out


def _normalize_asset_label(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in _FILTER_ALIASES:
        return _FILTER_ALIASES[raw]
    if raw.endswith("s") and raw[:-1] in {
        "forex",
        "crypto",
        "stock",
        "commodity",
        "index",
        "etf",
    }:
        return raw[:-1]
    return raw


def _norm_alias(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _canonical_label(pair: dict) -> str:
    return str(pair.get("display") or pair.get("symbol") or "").strip()


def discover_active_universe(
    all_pairs: list[dict] | None,
    *,
    disabled_pairs: set | list | tuple | None = None,
    etf_pairs: list[dict] | None = None,
) -> dict:
    """Load active tradable symbols and group them by asset class."""

    disabled = {str(x) for x in (disabled_pairs or [])}
    etf_symbols = {
        str((p or {}).get("symbol") or "")
        for p in (etf_pairs or [])
        if isinstance(p, dict)
    }
    etf_displays = {
        str((p or {}).get("display") or "")
        for p in (etf_pairs or [])
        if isinstance(p, dict)
    }

    pairs: list[dict] = []
    by_canonical: dict[str, dict] = {}
    groups: dict[str, list[dict]] = defaultdict(list)

    for pair in all_pairs or []:
        if not isinstance(pair, dict):
            continue
        canonical = _canonical_label(pair)
        if not canonical:
            continue
        if canonical in disabled or not pair.get("enabled", True):
            continue
        asset_class = _normalize_asset_label(pair.get("type") or "stock")
        if (
            str(pair.get("symbol") or "") in etf_symbols
            or canonical in etf_displays
        ):
            asset_class = "etf"
        entry = dict(pair)
        entry["canonical"] = canonical
        entry["assetClass"] = asset_class
        entry["isTradable"] = True
        pairs.append(entry)
        by_canonical[canonical] = entry
        groups[asset_class].append(entry)

    universe = {
        "pairs": pairs,
        "byCanonical": by_canonical,
        "groups": dict(groups),
    }
    universe["aliasMap"] = resolve_symbol_aliases(universe)
    return universe


def resolve_symbol_aliases(
    universe: dict | list[dict] | None,
    extra_aliases: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map common symbol/display variants to one canonical label."""

    alias_map: dict[str, str] = {}
    pairs = universe.get("pairs", []) if isinstance(universe, dict) else (universe or [])
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        canonical = _canonical_label(pair)
        if not canonical:
            continue
        candidates = {
            canonical,
            pair.get("symbol"),
            canonical.replace("/", ""),
            canonical.replace(" ", ""),
            canonical.replace("-", ""),
            str(pair.get("symbol") or "").replace("/", ""),
            str(pair.get("symbol") or "").replace(".", ""),
        }
        for candidate in candidates:
            if candidate:
                alias_map[_norm_alias(candidate)] = canonical
    for alias, canonical in (extra_aliases or {}).items():
        if alias and canonical:
            alias_map[_norm_alias(alias)] = canonical
    return alias_map


def _median_step_seconds(index: pd.Index) -> float | None:
    if len(index) < 2:
        return None
    diffs = index.to_series().diff().dropna().dt.total_seconds()
    valid = [float(x) for x in diffs.tolist() if x and x > 0]
    if not valid:
        return None
    try:
        return float(statistics.median(valid))
    except statistics.StatisticsError:
        return None


def _series_meta(index: pd.Index, raw_total: int) -> dict:
    valid = index.dropna()
    latest = valid[-1] if len(valid) else None
    return {
        "rawBars": int(raw_total),
        "validBars": int(len(valid)),
        "parseFail": int(raw_total - len(valid)),
        "duplicateTs": int(valid.duplicated().sum()) if len(valid) else 0,
        "monotonic": bool(valid.is_monotonic_increasing) if len(valid) else False,
        "latestTs": latest.isoformat() if latest is not None else None,
        "stepSeconds": _median_step_seconds(valid),
    }


def _coerce_close_series(item: Any, label: str) -> tuple[pd.Series | None, dict]:
    if isinstance(item, dict) and isinstance(item.get("close"), pd.Series):
        series = item["close"].copy()
        meta = dict(item.get("meta") or {})
        return series.sort_index(), meta
    if isinstance(item, pd.Series):
        series = pd.to_numeric(item, errors="coerce")
        series.index = pd.to_datetime(series.index, utc=True, errors="coerce")
        series = series.dropna()
        return series.sort_index(), _series_meta(series.index, len(item))
    candles = item if isinstance(item, list) else None
    if not candles:
        return None, {
            "label": label,
            "rawBars": 0,
            "validBars": 0,
            "parseFail": 0,
            "duplicateTs": 0,
            "monotonic": False,
            "latestTs": None,
            "stepSeconds": None,
        }
    times = pd.to_datetime(
        [c.get("time") or c.get("datetime") for c in candles],
        utc=True,
        errors="coerce",
    )
    close = pd.to_numeric(
        [c.get("close") for c in candles],
        errors="coerce",
    )
    df = pd.DataFrame({"close": close}, index=times).dropna()
    if df.empty:
        return None, _series_meta(pd.Index([], dtype="datetime64[ns, UTC]"), len(candles))
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df["close"], _series_meta(df.index, len(candles))


def _coerce_return_series(item: Any, label: str) -> tuple[pd.Series | None, dict]:
    if isinstance(item, dict) and isinstance(item.get("returns"), pd.Series):
        returns = item["returns"].copy().sort_index().dropna()
        meta = dict(item.get("meta") or {})
        return returns, meta
    close, meta = _coerce_close_series(item, label)
    if close is None or len(close) < 2:
        return None, meta
    returns = np.log(close.astype(float)).diff().dropna()
    return returns, meta


def build_aligned_return_frame(
    series_by_symbol: dict[str, Any],
    *,
    min_overlap_bars: int = 100,
) -> dict:
    """Normalize return series to one timestamp index and validate alignment."""

    returns_map: dict[str, pd.Series] = {}
    meta_map: dict[str, dict] = {}
    for label, item in (series_by_symbol or {}).items():
        returns, meta = _coerce_return_series(item, label)
        meta_map[label] = meta
        if returns is None or returns.empty:
            return {
                "ok": False,
                "reason": "missing_returns",
                "frame": pd.DataFrame(),
                "meta": meta_map,
            }
        returns_map[label] = returns

    frame = pd.concat(returns_map, axis=1, join="inner").dropna()
    if frame.empty or len(frame) < int(min_overlap_bars):
        return {
            "ok": False,
            "reason": "insufficient_overlap",
            "frame": frame,
            "meta": meta_map,
        }

    latest_ts = []
    step_candidates = []
    for meta in meta_map.values():
        ts = meta.get("latestTs")
        if ts:
            latest_ts.append(pd.Timestamp(ts))
        step = meta.get("stepSeconds")
        if step:
            step_candidates.append(float(step))
    if len(latest_ts) >= 2 and step_candidates:
        max_lag = abs((max(latest_ts) - min(latest_ts)).total_seconds())
        step_ref = max(1.0, min(step_candidates))
        if max_lag > step_ref * 1.5:
            return {
                "ok": False,
                "reason": "lagged_candles",
                "frame": frame,
                "meta": meta_map,
            }

    return {
        "ok": True,
        "reason": None,
        "frame": frame.sort_index(),
        "meta": meta_map,
    }


def _corr_value(series_a: pd.Series, series_b: pd.Series, method: str) -> float:
    try:
        val = series_a.corr(series_b, method=method)
        if val is None or math.isnan(float(val)):
            return 0.0
        return float(val)
    except Exception:
        return 0.0


def _count_sign_flips(series: pd.Series) -> int:
    if series is None or series.empty:
        return 0
    cleaned = series.replace(0, np.nan).dropna()
    if len(cleaned) < 2:
        return 0
    signs = np.sign(cleaned.astype(float))
    prev = signs.shift(1)
    return int(((signs != prev) & prev.notna()).sum())


def _lead_lag_metrics(
    target_returns: pd.Series,
    driver_returns: pd.Series,
    cfg: dict,
) -> dict | None:
    if not cfg.get("lead_lag_enabled", False):
        return None
    max_lag = max(1, int(cfg.get("max_lag_bars", 5) or 5))
    best = {"lagBars": 0, "correlation": 0.0, "leader": None, "evidence": "weak"}
    for lag in range(1, max_lag + 1):
        lead_corr = _corr_value(target_returns, driver_returns.shift(lag), "pearson")
        if abs(lead_corr) > abs(best["correlation"]):
            best = {
                "lagBars": lag,
                "correlation": float(lead_corr),
                "leader": "driver",
                "evidence": "weak",
            }
        reverse_corr = _corr_value(driver_returns, target_returns.shift(lag), "pearson")
        if abs(reverse_corr) > abs(best["correlation"]):
            best = {
                "lagBars": lag,
                "correlation": float(reverse_corr),
                "leader": "target",
                "evidence": "weak",
            }
    strength = abs(float(best["correlation"]))
    if strength >= 0.45:
        best["evidence"] = "strong"
    elif strength >= 0.25:
        best["evidence"] = "moderate"
    return best


def classify_relationship_regime(metrics: dict, config: dict | None = None) -> dict:
    """Classify the current relationship strength and stability."""

    cfg = intermarket_config(config)
    corr = float(metrics.get("correlation", 0.0) or 0.0)
    stability = float(metrics.get("stability", 0.0) or 0.0)
    flipped = bool(metrics.get("flippedRecently"))
    strong = float(cfg.get("strong_corr", 0.65))
    moderate = float(cfg.get("moderate_corr", 0.35))
    neutral = float(cfg.get("neutral_corr", 0.20))
    broken = float(cfg.get("broken_stability", 0.45))

    label = "weak / neutral"
    if stability < broken:
        label = "broken / unstable"
    elif flipped:
        label = "flipped recently"
    elif corr >= strong:
        label = "strongly positive"
    elif corr >= moderate:
        label = "moderately positive"
    elif corr <= -strong:
        label = "strongly inverse"
    elif corr <= -moderate:
        label = "moderately inverse"
    elif abs(corr) < neutral:
        label = "weak / neutral"

    relation = "neutral"
    if corr >= neutral:
        relation = "positive"
    elif corr <= -neutral:
        relation = "inverse"

    return {
        "label": label,
        "relation": relation,
        "stable": stability >= broken and not flipped,
        "broken": stability < broken,
        "flippedRecently": flipped,
    }


def _window_metrics(
    frame: pd.DataFrame,
    target_col: str,
    driver_col: str,
    window: int,
    cfg: dict,
) -> dict | None:
    if frame is None or len(frame) < window:
        return None
    sub = frame[[target_col, driver_col]].tail(window).dropna()
    if len(sub) < window:
        return None

    target_returns = sub[target_col].astype(float)
    driver_returns = sub[driver_col].astype(float)
    pearson = _corr_value(target_returns, driver_returns, "pearson")
    spearman = _corr_value(target_returns, driver_returns, "spearman")
    corr = float(np.mean([pearson, spearman]))

    var_driver = float(driver_returns.var(ddof=0) or 0.0)
    beta = (
        float(target_returns.cov(driver_returns) / var_driver)
        if var_driver > 1e-12
        else 0.0
    )
    var_target = float(target_returns.var(ddof=0) or 0.0)
    beta_reverse = (
        float(driver_returns.cov(target_returns) / var_target)
        if var_target > 1e-12
        else 0.0
    )

    sign_agreement = float(
        np.mean(np.sign(target_returns) == np.sign(driver_returns))
    )
    vol_adj = math.copysign(min(abs(beta), 3.0) / 3.0, corr) if corr else 0.0

    corr_series = frame[target_col].rolling(window).corr(frame[driver_col]).dropna()
    recent_corr = corr_series.tail(max(2, int(cfg.get("flip_lookback_bars", 10) or 10)))
    if len(recent_corr) >= 2:
        std = float(recent_corr.std(ddof=0) or 0.0)
        stability = max(0.0, 1.0 - min(std / 0.75, 1.0))
    else:
        stability = 0.5
    current_sign = np.sign(corr) if corr else 0.0
    signed_recent = recent_corr.replace(0, np.nan).dropna()
    sign_persistence = (
        float(np.mean(np.sign(signed_recent) == current_sign))
        if len(signed_recent) and current_sign
        else 0.5
    )
    flip_count = _count_sign_flips(recent_corr)
    flipped_recently = bool(
        flip_count > 0 and abs(corr) >= float(cfg.get("moderate_corr", 0.35))
    )

    last_target = float(target_returns.iloc[-1])
    last_driver = float(driver_returns.iloc[-1])
    last_bar_contradiction = bool(
        current_sign
        and last_target
        and last_driver
        and np.sign(last_target * last_driver) != current_sign
    )

    target_recent = float(
        np.expm1(target_returns.tail(min(3, len(target_returns))).sum()) * 100.0
    )
    driver_recent = float(
        np.expm1(driver_returns.tail(min(3, len(driver_returns))).sum()) * 100.0
    )
    target_last = float(np.expm1(last_target) * 100.0)
    driver_last = float(np.expm1(last_driver) * 100.0)
    lead_lag = _lead_lag_metrics(target_returns, driver_returns, cfg)

    metrics = {
        "window": int(window),
        "overlapBars": int(len(sub)),
        "correlation": round(corr, 6),
        "pearson": round(pearson, 6),
        "spearman": round(spearman, 6),
        "beta": round(beta, 6),
        "betaReverse": round(beta_reverse, 6),
        "signAgreement": round(sign_agreement, 6),
        "volAdjustedScore": round(vol_adj, 6),
        "stability": round(stability, 6),
        "signPersistence": round(sign_persistence, 6),
        "flipCount": int(flip_count),
        "flippedRecently": flipped_recently,
        "lastBarContradiction": last_bar_contradiction,
        "targetRecentChangePct": round(target_recent, 4),
        "driverRecentChangePct": round(driver_recent, 4),
        "targetLastReturnPct": round(target_last, 4),
        "driverLastReturnPct": round(driver_last, 4),
        "leadLag": lead_lag,
    }
    metrics["regime"] = classify_relationship_regime(metrics, cfg)
    rank = abs(corr) * max(0.2, stability) * max(0.2, sign_persistence)
    metrics["rank"] = round(float(rank), 6)
    return metrics


def _summarize_ordered_relationship(
    target_label: str,
    driver_label: str,
    target_entry: dict,
    driver_entry: dict,
    window_metrics: dict[int, dict],
    cfg: dict,
) -> dict:
    if not window_metrics:
        return {
            "target": target_label,
            "driver": driver_label,
            "targetAssetClass": target_entry.get("assetClass"),
            "driverAssetClass": driver_entry.get("assetClass"),
            "bestWindow": None,
            "current": None,
            "regime": {
                "label": "broken / unstable",
                "relation": "neutral",
                "stable": False,
                "broken": True,
                "flippedRecently": False,
            },
            "strength": 0.0,
        }
    best_window = max(
        window_metrics,
        key=lambda w: float((window_metrics[w] or {}).get("rank", 0.0)),
    )
    current = dict(window_metrics[best_window])
    current["window"] = int(best_window)
    regime = current.get("regime") or classify_relationship_regime(current, cfg)
    return {
        "target": target_label,
        "driver": driver_label,
        "targetAssetClass": target_entry.get("assetClass"),
        "driverAssetClass": driver_entry.get("assetClass"),
        "bestWindow": int(best_window),
        "current": current,
        "windows": window_metrics,
        "regime": regime,
        "strength": round(float(current.get("rank", 0.0)), 6),
        "correlation": float(current.get("correlation", 0.0)),
        "stability": float(current.get("stability", 0.0)),
        "flippedRecently": bool(current.get("flippedRecently")),
        "lastBarContradiction": bool(current.get("lastBarContradiction")),
    }


def _reverse_summary(summary: dict) -> dict:
    current = dict(summary.get("current") or {})
    target_recent = current.get("targetRecentChangePct")
    target_last = current.get("targetLastReturnPct")
    current["targetRecentChangePct"] = current.get("driverRecentChangePct")
    current["targetLastReturnPct"] = current.get("driverLastReturnPct")
    current["driverRecentChangePct"] = target_recent
    current["driverLastReturnPct"] = target_last
    current["beta"] = current.get("betaReverse", current.get("beta", 0.0))

    windows = {}
    for window, metrics in (summary.get("windows") or {}).items():
        wm = dict(metrics)
        tr = wm.get("targetRecentChangePct")
        tl = wm.get("targetLastReturnPct")
        wm["targetRecentChangePct"] = wm.get("driverRecentChangePct")
        wm["targetLastReturnPct"] = wm.get("driverLastReturnPct")
        wm["driverRecentChangePct"] = tr
        wm["driverLastReturnPct"] = tl
        wm["beta"] = wm.get("betaReverse", wm.get("beta", 0.0))
        windows[int(window)] = wm
    return {
        **summary,
        "target": summary.get("driver"),
        "driver": summary.get("target"),
        "targetAssetClass": summary.get("driverAssetClass"),
        "driverAssetClass": summary.get("targetAssetClass"),
        "current": current,
        "windows": windows,
    }


def _public_relationship_row(summary: dict) -> dict:
    current = summary.get("current") or {}
    regime = summary.get("regime") or {}
    return {
        "target": summary.get("target"),
        "driver": summary.get("driver"),
        "targetAssetClass": summary.get("targetAssetClass"),
        "driverAssetClass": summary.get("driverAssetClass"),
        "bestWindow": summary.get("bestWindow"),
        "correlation": round(float(summary.get("correlation", 0.0)), 4),
        "pearson": round(float(current.get("pearson", 0.0)), 4),
        "spearman": round(float(current.get("spearman", 0.0)), 4),
        "beta": round(float(current.get("beta", 0.0)), 4),
        "signAgreement": round(float(current.get("signAgreement", 0.0)), 4),
        "volAdjustedScore": round(float(current.get("volAdjustedScore", 0.0)), 4),
        "stability": round(float(summary.get("stability", 0.0)), 4),
        "signPersistence": round(float(current.get("signPersistence", 0.0)), 4),
        "flipCount": int(current.get("flipCount", 0)),
        "flippedRecently": bool(summary.get("flippedRecently")),
        "lastBarContradiction": bool(summary.get("lastBarContradiction")),
        "regimeLabel": regime.get("label", "weak / neutral"),
        "relation": regime.get("relation", "neutral"),
        "strength": round(float(summary.get("strength", 0.0)), 4),
    }


def compute_relationship_matrix(
    universe: dict,
    series_store: dict[str, dict],
    *,
    config: dict | None = None,
) -> dict:
    """Compute same-timeframe empirical rolling relationships across the active universe."""

    cfg = intermarket_config(config)
    windows = [int(x) for x in cfg.get("windows", [20, 50, 90])]
    min_overlap = max(5, int(cfg.get("min_overlap_bars", 100) or 100))
    relation_lookup: dict[tuple[str, str], dict] = {}
    matrix: dict[str, dict[str, dict]] = defaultdict(dict)
    flat_rows: list[dict] = []

    pairs = universe.get("pairs", []) if isinstance(universe, dict) else []
    by_canonical = universe.get("byCanonical", {}) if isinstance(universe, dict) else {}

    for pair_a, pair_b in itertools.combinations(pairs, 2):
        a = pair_a.get("canonical")
        b = pair_b.get("canonical")
        if not a or not b:
            continue
        aligned = build_aligned_return_frame(
            {a: series_store.get(a), b: series_store.get(b)},
            min_overlap_bars=min_overlap,
        )
        if not aligned.get("ok"):
            continue
        frame = aligned["frame"]
        window_metrics_ab: dict[int, dict] = {}
        for window in windows:
            wm = _window_metrics(frame, a, b, int(window), cfg)
            if wm:
                window_metrics_ab[int(window)] = wm
        if not window_metrics_ab:
            continue

        summary_ab = _summarize_ordered_relationship(
            a,
            b,
            by_canonical.get(a, pair_a),
            by_canonical.get(b, pair_b),
            window_metrics_ab,
            cfg,
        )
        summary_ba = _reverse_summary(summary_ab)

        relation_lookup[(a, b)] = summary_ab
        relation_lookup[(b, a)] = summary_ba
        matrix[a][b] = _public_relationship_row(summary_ab)
        matrix[b][a] = _public_relationship_row(summary_ba)
        flat_rows.append(_public_relationship_row(summary_ab))

    strongest_positive = sorted(
        [
            row
            for row in flat_rows
            if row.get("correlation", 0.0)
            >= float(cfg.get("moderate_corr", 0.35))
        ],
        key=lambda row: abs(float(row.get("correlation", 0.0)))
        * float(row.get("stability", 0.0)),
        reverse=True,
    )[:20]
    strongest_inverse = sorted(
        [
            row
            for row in flat_rows
            if row.get("correlation", 0.0)
            <= -float(cfg.get("moderate_corr", 0.35))
        ],
        key=lambda row: abs(float(row.get("correlation", 0.0)))
        * float(row.get("stability", 0.0)),
        reverse=True,
    )[:20]
    broken = sorted(
        [
            row
            for row in flat_rows
            if row.get("regimeLabel") in ("broken / unstable", "flipped recently")
        ],
        key=lambda row: float(row.get("strength", 0.0)),
        reverse=True,
    )[:20]
    return {
        "relationLookup": relation_lookup,
        "matrix": dict(matrix),
        "flatRows": flat_rows,
        "strongestPositive": strongest_positive,
        "strongestInverse": strongest_inverse,
        "brokenRelationships": broken,
    }


def prepare_series_store(
    universe: dict,
    *,
    fetch_candles,
    timeframe: str = "H4",
    limit: int = 240,
    config: dict | None = None,
    preloaded_candles: dict[str, list] | None = None,
) -> dict[str, dict]:
    """Fetch and cache aligned return series for tradable symbols plus required macro proxies."""

    cfg = intermarket_config(config)
    store: dict[str, dict] = {}
    preload = preloaded_candles or {}

    for pair in universe.get("pairs", []):
        canonical = pair.get("canonical")
        if not canonical:
            continue
        candles = preload.get(canonical)
        if candles is None:
            try:
                candles = fetch_candles(pair, timeframe, limit)
            except Exception as exc:
                log.debug("[INTERMARKET] %s candle fetch failed: %s", canonical, exc)
                candles = None
        close, meta = _coerce_close_series(candles, canonical)
        if close is None or len(close) < 2:
            continue
        returns = np.log(close.astype(float)).diff().dropna()
        if returns.empty:
            continue
        store[canonical] = {
            "pair": pair,
            "label": canonical,
            "close": close,
            "returns": returns,
            "meta": meta,
            "candles": candles or [],
            "assetClass": pair.get("assetClass")
            or _normalize_asset_label(pair.get("type") or "stock"),
            "sourceType": "tradable",
        }

    if any(
        str(prior.get("driver") or "").strip().upper() == "DXY"
        for prior in cfg.get("macro_priors", [])
    ):
        if "DXY" not in store:
            proxy_pair = dict(_SPECIAL_PROXY_PAIRS["DXY"])
            try:
                dxy_candles = fetch_candles(proxy_pair, timeframe, limit)
            except Exception as exc:
                log.debug("[INTERMARKET] DXY fetch failed: %s", exc)
                dxy_candles = None
            close, meta = _coerce_close_series(dxy_candles, "DXY")
            if close is not None and len(close) >= 2:
                store["DXY"] = {
                    "pair": proxy_pair,
                    "label": "DXY",
                    "close": close,
                    "returns": np.log(close.astype(float)).diff().dropna(),
                    "meta": meta,
                    "candles": dxy_candles or [],
                    "assetClass": "macro",
                    "sourceType": "macro_proxy",
                }
    return store


def _composite_proxy_series(
    labels: list[str],
    series_store: dict[str, dict],
    proxy_name: str,
) -> dict | None:
    series_map = {
        label: series_store[label]["returns"]
        for label in labels
        if label in series_store
        and isinstance(series_store[label].get("returns"), pd.Series)
    }
    if not series_map:
        return None
    frame = pd.concat(series_map, axis=1, join="inner").dropna()
    if frame.empty or len(frame) < 20:
        return None
    composite = frame.mean(axis=1)
    meta = {
        "rawBars": int(len(frame)),
        "validBars": int(len(frame)),
        "parseFail": 0,
        "duplicateTs": 0,
        "monotonic": bool(frame.index.is_monotonic_increasing),
        "latestTs": frame.index[-1].isoformat(),
        "stepSeconds": _median_step_seconds(frame.index),
    }
    return {
        "pair": {
            "display": proxy_name,
            "symbol": proxy_name,
            "type": "macro",
            "assetClass": "macro",
        },
        "label": proxy_name,
        "close": composite.cumsum().pipe(np.exp),
        "returns": composite,
        "meta": meta,
        "candles": [],
        "assetClass": "macro",
        "sourceType": "composite_proxy",
    }


def _resolve_macro_driver(
    driver_name: str,
    *,
    universe: dict,
    series_store: dict[str, dict],
) -> dict | None:
    alias_map = universe.get("aliasMap", {}) if isinstance(universe, dict) else {}
    canonical = alias_map.get(_norm_alias(driver_name))
    if canonical and canonical in series_store:
        return {
            "label": canonical,
            "series": series_store[canonical],
            "effectiveRelation": None,
            "resolvedFrom": "universe",
        }

    driver = str(driver_name or "").strip().upper()
    if driver == "DXY" and "DXY" in series_store:
        return {
            "label": "DXY",
            "series": series_store["DXY"],
            "effectiveRelation": None,
            "resolvedFrom": "macro_proxy",
        }
    if driver == "WTI_OIL_PROXY":
        for label in ("WTI Oil", "Brent Oil", "USO"):
            if label in series_store:
                return {
                    "label": label,
                    "series": series_store[label],
                    "effectiveRelation": None,
                    "resolvedFrom": "internal_proxy",
                }
    if driver == "US10Y_NOMINAL_PROXY":
        if "TLT" in series_store:
            return {
                "label": "TLT",
                "series": series_store["TLT"],
                "effectiveRelation": "inverse_proxy",
                "resolvedFrom": "internal_proxy",
            }
        return None
    if driver == "US10Y_REAL_YIELD_PROXY":
        return None
    if driver == "RISK_SENTIMENT_PROXY":
        composite = _composite_proxy_series(
            [
                x
                for x in (
                    "SPY",
                    "QQQ",
                    "S&P 500",
                    "NASDAQ-100",
                    "Dow Jones",
                    "DAX 40",
                )
                if x in series_store
            ],
            series_store,
            "RISK_SENTIMENT_PROXY",
        )
        if composite:
            return {
                "label": "RISK_SENTIMENT_PROXY",
                "series": composite,
                "effectiveRelation": None,
                "resolvedFrom": "composite_proxy",
            }
        return None
    if driver == "COMMODITY_BASKET_PROXY":
        composite = _composite_proxy_series(
            [
                x
                for x in (
                    "XAU/USD",
                    "XAG/USD",
                    "Copper",
                    "WTI Oil",
                    "Brent Oil",
                    "GLD",
                    "SLV",
                    "USO",
                )
                if x in series_store
            ],
            series_store,
            "COMMODITY_BASKET_PROXY",
        )
        if composite:
            return {
                "label": "COMMODITY_BASKET_PROXY",
                "series": composite,
                "effectiveRelation": None,
                "resolvedFrom": "composite_proxy",
            }
    return None


def _pair_priors(target_label: str, cfg: dict) -> list[dict]:
    out = []
    for prior in cfg.get("macro_priors", []) or []:
        if str(prior.get("pair") or "").strip() == target_label:
            out.append(dict(prior))
    return out


def build_symbol_context(
    pair: dict,
    snapshot: dict | None,
    *,
    config: dict | None = None,
) -> dict | None:
    """Build direction-agnostic intermarket context for one tradable symbol."""

    if not snapshot or not isinstance(snapshot, dict):
        return None
    cfg = intermarket_config(config)
    target_label = str(
        pair.get("display") or pair.get("canonical") or pair.get("symbol") or ""
    ).strip()
    if not target_label:
        return None
    universe = snapshot.get("universe") or {}
    series_store = snapshot.get("seriesStore") or {}
    relation_lookup = snapshot.get("relationLookup") or {}
    target_entry = (universe.get("byCanonical") or {}).get(target_label) or dict(pair)
    target_asset = _normalize_asset_label(
        target_entry.get("assetClass") or target_entry.get("type") or "stock"
    )
    allowed_assets = set(cfg.get("asset_class_filters", {}).get(target_asset, [])) or {
        "forex",
        "crypto",
        "commodity",
        "index",
        "stock",
        "etf",
        "macro",
    }

    drivers: list[dict] = []
    for (target, driver), summary in relation_lookup.items():
        if target != target_label:
            continue
        driver_asset = _normalize_asset_label(summary.get("driverAssetClass"))
        if driver_asset not in allowed_assets:
            continue
        if summary.get("regime", {}).get("label") == "weak / neutral":
            continue
        drivers.append(
            {
                "driver": driver,
                "driverAssetClass": driver_asset,
                "sourceType": "empirical",
                "priorRelation": None,
                "effectivePriorRelation": None,
                "resolvedFrom": "matrix",
                "summary": summary,
            }
        )

    unavailable_priors: list[str] = []
    for prior in _pair_priors(target_label, cfg):
        driver_name = str(prior.get("driver") or "").strip()
        relation = str(prior.get("relation") or "").strip().lower()
        resolved = _resolve_macro_driver(
            driver_name,
            universe=universe,
            series_store=series_store,
        )
        if not resolved:
            unavailable_priors.append(driver_name)
            continue

        driver_label = resolved["label"]
        existing = next((d for d in drivers if d["driver"] == driver_label), None)
        if existing is None:
            target_series = series_store.get(target_label)
            driver_series = resolved.get("series")
            aligned = build_aligned_return_frame(
                {target_label: target_series, driver_label: driver_series},
                min_overlap_bars=max(5, int(cfg.get("min_overlap_bars", 100) or 100)),
            )
            if not aligned.get("ok"):
                unavailable_priors.append(driver_name)
                continue
            window_metrics: dict[int, dict] = {}
            for window in cfg.get("windows", [20, 50, 90]):
                wm = _window_metrics(
                    aligned["frame"],
                    target_label,
                    driver_label,
                    int(window),
                    cfg,
                )
                if wm:
                    window_metrics[int(window)] = wm
            if not window_metrics:
                unavailable_priors.append(driver_name)
                continue
            driver_entry = dict(
                (driver_series or {}).get("pair")
                or {"display": driver_label, "assetClass": "macro"}
            )
            driver_entry["assetClass"] = _normalize_asset_label(
                driver_entry.get("assetClass") or driver_entry.get("type") or "macro"
            )
            existing = {
                "driver": driver_label,
                "driverAssetClass": driver_entry.get("assetClass"),
                "sourceType": "structural",
                "priorRelation": relation,
                "effectivePriorRelation": relation,
                "resolvedFrom": resolved.get("resolvedFrom"),
                "summary": _summarize_ordered_relationship(
                    target_label,
                    driver_label,
                    target_entry,
                    driver_entry,
                    window_metrics,
                    cfg,
                ),
            }
            drivers.append(existing)
        else:
            existing["sourceType"] = "both"
            existing["priorRelation"] = relation
            existing["effectivePriorRelation"] = relation
            existing["resolvedFrom"] = resolved.get("resolvedFrom")

        if resolved.get("effectiveRelation") == "inverse_proxy" and existing is not None:
            effective = (
                "inverse"
                if relation == "positive"
                else "positive"
                if relation == "inverse"
                else relation
            )
            existing["effectivePriorRelation"] = effective

    drivers.sort(
        key=lambda d: float(((d.get("summary") or {}).get("strength", 0.0) or 0.0)),
        reverse=True,
    )
    max_pairs = max(1, int(cfg.get("max_pairs_per_symbol", 12) or 12))
    drivers = drivers[:max_pairs]

    return {
        "enabled": bool(cfg.get("enabled", False)),
        "target": target_label,
        "assetClass": target_asset,
        "drivers": drivers,
        "unavailablePriors": unavailable_priors,
        "analysisTf": str(cfg.get("analysis_tf", "H4")).upper(),
        "windows": [int(x) for x in cfg.get("windows", [20, 50, 90])],
        "generatedAt": snapshot.get("builtAt"),
    }


def _score_driver_support(driver_ctx: dict, direction: str, cfg: dict) -> dict | None:
    summary = driver_ctx.get("summary") or {}
    current = summary.get("current") or {}
    regime = summary.get("regime") or {}
    corr = float(current.get("correlation", 0.0) or 0.0)
    stability = float(current.get("stability", 0.0) or 0.0)
    sign_persistence = float(current.get("signPersistence", 0.0) or 0.0)
    vol_adj = float(current.get("volAdjustedScore", 0.0) or 0.0)
    driver_move = float(current.get("driverRecentChangePct", 0.0) or 0.0)
    relation = regime.get("relation", "neutral")
    if relation not in ("positive", "inverse"):
        return None
    if regime.get("label") == "broken / unstable":
        return None
    if abs(driver_move) < 0.02:
        return None

    relation_sign = 1 if relation == "positive" else -1
    trade_sign = 1 if str(direction).upper() == "LONG" else -1
    driver_sign = int(np.sign(driver_move))
    if driver_sign == 0:
        return None

    expected_trade_sign = relation_sign * driver_sign
    support_sign = 1 if expected_trade_sign == trade_sign else -1

    base_strength = abs(corr)
    stability_component = max(0.0, min(1.0, (stability + sign_persistence) / 2.0))
    magnitude = max(0.0, min(1.0, base_strength * (0.55 + 0.45 * stability_component)))
    magnitude *= 0.7 + (0.3 * min(1.0, abs(vol_adj)))

    flipped_recently = bool(
        regime.get("flippedRecently") or current.get("flippedRecently")
    )
    if flipped_recently:
        magnitude *= 0.20
    if current.get("lastBarContradiction"):
        magnitude *= 0.85

    prior_relation = driver_ctx.get("effectivePriorRelation") or driver_ctx.get(
        "priorRelation"
    )
    if prior_relation:
        if prior_relation == relation:
            magnitude *= 1.0
        elif base_strength < float(cfg.get("moderate_corr", 0.35)):
            magnitude *= 0.35
        else:
            magnitude *= 0.20
    if driver_ctx.get("sourceType") == "both":
        magnitude = min(1.0, magnitude * 1.10)

    lead_lag = current.get("leadLag") or {}
    lead_lag_bonus = 0.0
    if support_sign > 0 and lead_lag.get("leader") == "driver" and not flipped_recently:
        evidence = str(lead_lag.get("evidence") or "").lower()
        if evidence == "strong":
            lead_lag_bonus = 0.06
        elif evidence == "moderate":
            lead_lag_bonus = 0.03

    score = support_sign * magnitude
    return {
        "driver": driver_ctx.get("driver"),
        "sourceType": driver_ctx.get("sourceType"),
        "driverAssetClass": driver_ctx.get("driverAssetClass"),
        "priorRelation": driver_ctx.get("priorRelation"),
        "effectivePriorRelation": prior_relation,
        "currentRelation": relation,
        "regimeLabel": regime.get("label", "weak / neutral"),
        "window": current.get("window"),
        "correlation": round(corr, 4),
        "stability": round(stability, 4),
        "signPersistence": round(sign_persistence, 4),
        "driverRecentChangePct": round(driver_move, 4),
        "targetRecentChangePct": round(
            float(current.get("targetRecentChangePct", 0.0) or 0.0), 4
        ),
        "leadLag": lead_lag,
        "leadLagBonus": round(lead_lag_bonus, 4),
        "flippedRecently": bool(current.get("flippedRecently")),
        "lastBarContradiction": bool(current.get("lastBarContradiction")),
        "score": round(score, 6),
        "supportive": bool(score > 0),
        "strength": round(magnitude, 4),
    }


def score_confirmation(
    raw_context: dict | None,
    direction: str,
    pair: dict,
    *,
    config: dict | None = None,
) -> dict:
    """Turn raw intermarket relationships into a direction-aware confirmation verdict."""

    cfg = intermarket_config(config)
    neutral = {
        "verdict": "neutral",
        "score": 0.0,
        "supportDirection": "neutral",
        "supportStrength": "weak",
        "stable": False,
        "flippedRecently": False,
        "activeWindow": None,
        "topSupporting": [],
        "topContradictory": [],
        "contradictionFlags": {
            "severeContradiction": False,
            "lastBarContradiction": False,
            "flippedDrivers": [],
        },
        "unavailablePriors": list((raw_context or {}).get("unavailablePriors") or []),
        "explanation": "Intermarket confirmation neutral: insufficient or unstable evidence.",
        "driversConsidered": 0,
    }
    if not raw_context or not isinstance(raw_context, dict):
        return neutral

    scored = []
    for driver_ctx in raw_context.get("drivers", []) or []:
        item = _score_driver_support(driver_ctx, direction, cfg)
        if item:
            scored.append(item)

    if not scored:
        neutral["explanation"] = (
            "Intermarket confirmation neutral: available relationships were weak, broken, or inactive."
        )
        neutral["stable"] = False
        neutral["flippedRecently"] = any(
            bool(((d.get("summary") or {}).get("current") or {}).get("flippedRecently"))
            for d in (raw_context.get("drivers") or [])
        )
        return neutral

    supportive = [x for x in scored if x["score"] > 0]
    contradictory = [x for x in scored if x["score"] < 0]
    alignment = (
        float(np.mean([x["score"] for x in supportive])) if supportive else 0.0
    )
    contradiction = (
        float(np.mean([abs(x["score"]) for x in contradictory]))
        if contradictory
        else 0.0
    )
    stability_component = (
        float(np.mean([x["stability"] for x in scored])) if scored else 0.0
    )
    regime_confidence = max(
        0.0,
        min(1.0, (stability_component + max(abs(alignment), contradiction)) / 2.0),
    )
    lead_bonus = float(sum(x.get("leadLagBonus", 0.0) for x in supportive))
    raw_score = ((alignment - contradiction) + lead_bonus) * regime_confidence
    raw_score = max(-1.0, min(1.0, raw_score))

    verdict = "mixed"
    if raw_score >= 0.12:
        verdict = "supportive"
    elif raw_score <= -0.12:
        verdict = "contradictory"
    elif supportive or contradictory:
        verdict = "mixed"

    support_direction = "neutral"
    if raw_score > 0:
        support_direction = str(direction).upper()
    elif raw_score < 0:
        support_direction = "SHORT" if str(direction).upper() == "LONG" else "LONG"

    strength_abs = abs(raw_score)
    support_strength = "weak"
    if strength_abs >= 0.45:
        support_strength = "strong"
    elif strength_abs >= 0.22:
        support_strength = "moderate"

    top_supporting = sorted(
        supportive,
        key=lambda x: abs(float(x["score"])),
        reverse=True,
    )[:3]
    top_contradictory = sorted(
        contradictory,
        key=lambda x: abs(float(x["score"])),
        reverse=True,
    )[:3]
    windows = [x.get("window") for x in scored if x.get("window") is not None]
    active_window = Counter(windows).most_common(1)[0][0] if windows else None
    flipped_drivers = [x["driver"] for x in scored if x.get("flippedRecently")]
    severe_contradiction = contradiction >= 0.45 and contradiction > alignment
    stable = (
        stability_component >= float(cfg.get("broken_stability", 0.45))
        and not severe_contradiction
    )

    support_names = ", ".join(x["driver"] for x in top_supporting) or "none"
    contradiction_names = ", ".join(x["driver"] for x in top_contradictory) or "none"
    explanation = (
        f"Intermarket confirmation {verdict}: support {support_names}; contradiction {contradiction_names}; "
        f"window {active_window or 'n/a'}; stability {stability_component:.2f}."
    )

    return {
        "verdict": verdict,
        "score": round(raw_score, 6),
        "supportDirection": support_direction,
        "supportStrength": support_strength,
        "stable": bool(stable),
        "flippedRecently": bool(flipped_drivers),
        "activeWindow": active_window,
        "topSupporting": top_supporting,
        "topContradictory": top_contradictory,
        "contradictionFlags": {
            "severeContradiction": bool(severe_contradiction),
            "lastBarContradiction": any(
                bool(x.get("lastBarContradiction")) for x in scored
            ),
            "flippedDrivers": flipped_drivers,
        },
        "unavailablePriors": list(raw_context.get("unavailablePriors") or []),
        "explanation": explanation,
        "driversConsidered": len(scored),
        "stabilityScore": round(stability_component, 6),
        "regimeConfidence": round(regime_confidence, 6),
    }


def apply_confirmation_to_score(
    base_score: float,
    direction: str,
    pair: dict,
    raw_context: dict | None,
    *,
    max_score: float,
    config: dict | None = None,
) -> dict:
    """Apply a bounded confirmation delta to an existing Engine A-style score."""

    cfg = intermarket_config(config)
    confirmation = score_confirmation(raw_context, direction, pair, config=config)
    confirmation["engineADelta"] = 0.0
    if not cfg.get("enabled", False) or not cfg.get("engine_a_enabled", False):
        return {
            "adjusted_score": float(base_score),
            "confirmation": confirmation,
        }

    score_cap = abs(float(cfg.get("engine_a_score_cap", 0.18) or 0.18))
    delta = max(
        -score_cap,
        min(
            score_cap,
            float(confirmation.get("score", 0.0))
            * float(confirmation.get("regimeConfidence", 0.0) or 0.0),
        ),
    )
    adjusted = max(0.0, min(float(max_score), float(base_score) + float(delta)))
    confirmation["engineADelta"] = round(delta, 6)
    return {
        "adjusted_score": round(adjusted, 6),
        "confirmation": confirmation,
    }


def engine_c_conviction_multiplier(
    confirmation: dict | None,
    *,
    config: dict | None = None,
) -> float:
    cfg = intermarket_config(config)
    if not cfg.get("enabled", False) or not cfg.get("engine_c_enabled", False):
        return 1.0
    if not confirmation or not isinstance(confirmation, dict):
        return 1.0
    cap = abs(float(cfg.get("engine_c_conviction_cap", 0.08) or 0.08))
    mult = 1.0 + max(
        -cap,
        min(cap, float(confirmation.get("score", 0.0)) * cap),
    )
    return max(0.0, float(mult))


def build_scan_snapshot(
    all_pairs: list[dict],
    *,
    disabled_pairs: set | list | tuple | None,
    etf_pairs: list[dict] | None,
    fetch_candles,
    config: dict | None = None,
    preloaded_h4_candles: dict[str, list] | None = None,
    force: bool = False,
) -> dict | None:
    cfg = intermarket_config(config)
    universe = discover_active_universe(
        all_pairs,
        disabled_pairs=disabled_pairs,
        etf_pairs=etf_pairs,
    )
    if not universe.get("pairs"):
        return None

    timeframe = str(cfg.get("analysis_tf", "H4") or "H4").upper()
    windows = tuple(int(x) for x in cfg.get("windows", [20, 50, 90]))
    key = (
        timeframe,
        tuple(sorted(p["canonical"] for p in universe["pairs"])),
        windows,
        bool(cfg.get("lead_lag_enabled", False)),
    )
    now = time.time()
    with _SCAN_CACHE_LOCK:
        if (
            not force
            and _SCAN_CACHE["key"] == key
            and (now - float(_SCAN_CACHE["built_at"] or 0.0)) <= 60.0
        ):
            return _SCAN_CACHE["snapshot"]

    min_overlap = int(cfg.get("min_overlap_bars", 100) or 100)
    limit = max(max(windows) * 3, min_overlap + 40, 220)
    series_store = prepare_series_store(
        universe,
        fetch_candles=fetch_candles,
        timeframe=timeframe,
        limit=limit,
        config=config,
        preloaded_candles=preloaded_h4_candles,
    )
    matrix = compute_relationship_matrix(universe, series_store, config=config)
    snapshot = {
        "builtAt": datetime.now(timezone.utc).isoformat(),
        "analysisTf": timeframe,
        "enabled": bool(cfg.get("enabled", False)),
        "universe": universe,
        "seriesStore": series_store,
        **matrix,
    }
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE["key"] = key
        _SCAN_CACHE["built_at"] = now
        _SCAN_CACHE["snapshot"] = snapshot
    return snapshot


def build_point_in_time_context(
    pair: dict,
    *,
    all_pairs: list[dict],
    disabled_pairs: set | list | tuple | None,
    etf_pairs: list[dict] | None,
    series_store: dict[str, dict],
    cutoff_ts: Any,
    config: dict | None = None,
) -> dict | None:
    """Slice a preloaded H4 series store to one historical cutoff without lookahead."""

    if cutoff_ts is None or pd.isna(cutoff_ts):
        return None
    universe = discover_active_universe(
        all_pairs,
        disabled_pairs=disabled_pairs,
        etf_pairs=etf_pairs,
    )
    sliced: dict[str, dict] = {}
    for label, entry in (series_store or {}).items():
        returns = entry.get("returns")
        close = entry.get("close")
        if not isinstance(returns, pd.Series) or not isinstance(close, pd.Series):
            continue
        close_cut = close.loc[close.index < cutoff_ts]
        returns_cut = returns.loc[returns.index < cutoff_ts]
        if len(close_cut) < 2 or len(returns_cut) < 10:
            continue
        meta = dict(entry.get("meta") or {})
        if len(close_cut):
            meta["latestTs"] = close_cut.index[-1].isoformat()
            meta["validBars"] = int(len(close_cut))
            meta["stepSeconds"] = _median_step_seconds(close_cut.index)
        sliced[label] = {
            **entry,
            "close": close_cut,
            "returns": returns_cut,
            "meta": meta,
        }
    snapshot = {
        "builtAt": pd.Timestamp(cutoff_ts).isoformat(),
        "analysisTf": str(intermarket_config(config).get("analysis_tf", "H4")).upper(),
        "enabled": bool(intermarket_config(config).get("enabled", False)),
        "universe": universe,
        "seriesStore": sliced,
        "relationLookup": {},
    }
    cfg = intermarket_config(config)
    target_label = _canonical_label(pair)
    temp_lookup: dict[tuple[str, str], dict] = {}
    for driver in universe.get("pairs", []):
        driver_label = driver.get("canonical")
        if not driver_label or driver_label == target_label:
            continue
        aligned = build_aligned_return_frame(
            {target_label: sliced.get(target_label), driver_label: sliced.get(driver_label)},
            min_overlap_bars=max(5, int(cfg.get("min_overlap_bars", 100) or 100)),
        )
        if not aligned.get("ok"):
            continue
        window_metrics: dict[int, dict] = {}
        for window in cfg.get("windows", [20, 50, 90]):
            wm = _window_metrics(
                aligned["frame"],
                target_label,
                driver_label,
                int(window),
                cfg,
            )
            if wm:
                window_metrics[int(window)] = wm
        if not window_metrics:
            continue
        temp_lookup[(target_label, driver_label)] = _summarize_ordered_relationship(
            target_label,
            driver_label,
            universe.get("byCanonical", {}).get(target_label, pair),
            driver,
            window_metrics,
            cfg,
        )
    snapshot["relationLookup"] = temp_lookup
    return build_symbol_context(pair, snapshot, config=config)


def parse_asset_class_filter(raw_filter: str | None) -> tuple[str | None, str | None]:
    if not raw_filter:
        return None, None
    raw = str(raw_filter).strip().lower()
    if ":" in raw:
        left, right = raw.split(":", 1)
        return _normalize_asset_label(left), _normalize_asset_label(right)
    label = _normalize_asset_label(raw)
    return label, None


def build_public_matrix_payload(
    snapshot: dict | None,
    *,
    asset_class_filter: str | None = None,
    limit: int = 40,
) -> dict:
    if not snapshot or not isinstance(snapshot, dict):
        return {
            "success": False,
            "error": "Intermarket snapshot unavailable",
            "relationships": [],
            "strongestPositive": [],
            "strongestInverse": [],
            "brokenRelationships": [],
        }
    left_filter, right_filter = parse_asset_class_filter(asset_class_filter)
    rows = list(snapshot.get("flatRows") or [])
    if left_filter:
        filtered = []
        for row in rows:
            target_asset = _normalize_asset_label(row.get("targetAssetClass"))
            driver_asset = _normalize_asset_label(row.get("driverAssetClass"))
            if right_filter is None:
                if target_asset == left_filter or driver_asset == left_filter:
                    filtered.append(row)
            else:
                if (
                    target_asset == left_filter and driver_asset == right_filter
                ) or (
                    target_asset == right_filter and driver_asset == left_filter
                ):
                    filtered.append(row)
        rows = filtered

    strongest_positive = [row for row in rows if row.get("correlation", 0.0) >= 0]
    strongest_positive = sorted(
        strongest_positive,
        key=lambda row: abs(float(row.get("correlation", 0.0)))
        * float(row.get("stability", 0.0)),
        reverse=True,
    )[:limit]
    strongest_inverse = [row for row in rows if row.get("correlation", 0.0) < 0]
    strongest_inverse = sorted(
        strongest_inverse,
        key=lambda row: abs(float(row.get("correlation", 0.0)))
        * float(row.get("stability", 0.0)),
        reverse=True,
    )[:limit]
    broken = [
        row
        for row in rows
        if row.get("regimeLabel") in ("broken / unstable", "flipped recently")
    ][:limit]

    return {
        "success": True,
        "builtAt": snapshot.get("builtAt"),
        "analysisTf": snapshot.get("analysisTf"),
        "activeSymbols": len((snapshot.get("universe") or {}).get("pairs", [])),
        "assetClassFilter": asset_class_filter,
        "relationships": rows[:limit],
        "strongestPositive": strongest_positive,
        "strongestInverse": strongest_inverse,
        "brokenRelationships": broken,
    }
