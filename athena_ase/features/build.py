"""ASE feature builder — all inputs via PTIS asof() only (§5)."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

import numpy as np

from athena_ase.data.ptis import asof
from athena_ase.horizon import HORIZONS, Horizon

# Instruments with < COT_MIN_WEEKS of COT history route to core model only.
# cot_pct percentile window = min(156, available_weeks).
COT_MIN_WEEKS = 104

_MS = 1000
_RET_INTRADAY = ("ret_z_1", "ret_z_4", "ret_z_8", "ret_z_24")
_RET_SWING = ("ret_z_1", "ret_z_5", "ret_z_21")
_RET_ALL = ("ret_z_1", "ret_z_4", "ret_z_5", "ret_z_8", "ret_z_21", "ret_z_24")

_PATH_BLOCK = ("dd_64", "ru_64", "range_pos")
_LIQUIDITY_BLOCK = ("volu_z",)
_XSEC_BLOCK = ("xsec_pct", "family_dispersion")
_CORR_BLOCK = ("beta_bench",)
_SIGNAL_BLOCK = (
    "sig_tsmom",
    "sig_carry",
    "sig_xsec",
    "sig_mr",
    "agreement_count",
    "conflict_flag",
    "candidate_dir",
)
_CALENDAR_BLOCK = ("hour_sin", "hour_cos", "dow_sin", "dow_cos", "session")
_INSTRUMENT_BLOCK = ("instrument_id", "subclass")
_VOL_BLOCK = ("vol_level", "vol_of_vol", "vol_regime")
_ENRICHED_BLOCK = ("cot_pct", "cot_delta_4w", "funding_z", "oi_delta_z")

FEATURE_SCHEMA_CORE: tuple[str, ...] = (
    *_RET_ALL,
    *_VOL_BLOCK,
    *_PATH_BLOCK,
    *_LIQUIDITY_BLOCK,
    *_XSEC_BLOCK,
    *_CORR_BLOCK,
    *_SIGNAL_BLOCK,
    *_CALENDAR_BLOCK,
    *_INSTRUMENT_BLOCK,
)

FEATURE_SCHEMA_ENRICHED: tuple[str, ...] = FEATURE_SCHEMA_CORE + _ENRICHED_BLOCK

CATEGORICAL_FEATURES = frozenset({"instrument_id", "subclass", "session", "vol_regime"})

__all__ = [
    "COT_MIN_WEEKS",
    "FEATURE_SCHEMA_CORE",
    "FEATURE_SCHEMA_ENRICHED",
    "CATEGORICAL_FEATURES",
    "FeatureBuildContext",
    "build_features_for_candidate",
    "build_feature_matrix",
    "schema_hash",
    "asof",
]


def _compact_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").replace(" ", "").upper()


def _eodhd_series_id(symbol: str, tf: str, field: str) -> str:
    return f"EODHD:{_compact_symbol(symbol)}:{tf.upper()}:{field.lower()}"


def _duka_series_id(symbol: str, tf: str) -> str:
    return f"DUKASCOPY:{_compact_symbol(symbol)}:{tf.upper()}:volume"


def _bybit_series_id(symbol: str, field: str) -> str:
    return f"BYBIT:{_compact_symbol(symbol)}:{field.lower()}"


def _cot_series_id(asset: str) -> str:
    return f"CFTC:COT:{asset.upper()}:noncomm_net"


def schema_hash(schema: Sequence[str] | None = None) -> str:
    names = tuple(schema) if schema is not None else FEATURE_SCHEMA_ENRICHED
    payload = json.dumps(list(names), sort_keys=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _ewma_vol(logret: np.ndarray, span: int) -> np.ndarray:
    if len(logret) == 0:
        return np.array([], dtype=float)
    alpha = 2.0 / (span + 1.0)
    var = np.full(len(logret), np.nan)
    if len(logret) == 1:
        return var
    v = float(logret[1] ** 2) if not math.isnan(logret[1]) else 0.0
    var[1] = v
    for i in range(2, len(logret)):
        x = float(logret[i])
        if math.isnan(x):
            var[i] = var[i - 1]
        else:
            v = alpha * (x * x) + (1.0 - alpha) * v
            var[i] = v
    return np.sqrt(var)


def _asof_values(series_id: str, decision_time_ms: int, n: int) -> np.ndarray:
    rows = asof(series_id, decision_time_ms, n)
    if len(rows) == 0:
        return np.array([], dtype=float)
    return rows["value"].astype(float)


def _asof_close_log(symbol: str, tf: str, decision_time_ms: int, n: int) -> np.ndarray:
    vals = _asof_values(_eodhd_series_id(symbol, tf, "close"), decision_time_ms, n)
    if len(vals) == 0:
        return vals
    return np.log(np.maximum(vals, 1e-12))


def _vol_scaled_return(close_log: np.ndarray, sigma_bar: np.ndarray, lag: int) -> float:
    if len(close_log) <= lag or lag <= 0:
        return float("nan")
    sig = float(sigma_bar[-1])
    if sig != sig or sig <= 0:
        return float("nan")
    move = float(close_log[-1] - close_log[-1 - lag])
    return move / (sig * math.sqrt(lag))


def _session_ordinal(hour_utc: int) -> int:
    if 12 <= hour_utc < 16:
        return 3
    if 7 <= hour_utc < 16:
        return 1
    if 16 <= hour_utc < 22:
        return 2
    return 0


def _vol_regime_ordinal(vol_level: float) -> int:
    if vol_level != vol_level:
        return 1
    if vol_level < 0.8:
        return 0
    if vol_level > 1.25:
        return 2
    return 1


def _cot_percentile(rows: np.ndarray, window_weeks: int = 156) -> tuple[float, float, int]:
    if len(rows) < 2:
        return float("nan"), float("nan"), 0
    vals = rows["value"].astype(float)
    n_weeks = len(vals)
    window = min(window_weeks, n_weeks)
    if window < 2:
        return float("nan"), float("nan"), n_weeks
    tail = vals[-window:]
    current = float(tail[-1])
    pct = float(np.mean(tail <= current))
    delta_4w = float("nan")
    if len(vals) >= 5:
        delta_4w = current - float(vals[-5])
    return pct, delta_4w, n_weeks


def _zscore_log_volume(vol_rows: np.ndarray, window: int = 64) -> float:
    if len(vol_rows) == 0:
        return float("nan")
    vals = vol_rows.astype(float)
    vals = np.log(np.maximum(vals, 1.0))
    tail = vals[-window:] if len(vals) >= window else vals
    mu = float(np.mean(tail))
    sd = float(np.std(tail))
    if sd <= 0:
        return 0.0
    return (float(vals[-1]) - mu) / sd


def _rolling_beta(
    inst_log: np.ndarray,
    bench_log: np.ndarray,
    window: int = 64,
) -> float:
    n = min(len(inst_log), len(bench_log))
    if n < window or window < 2:
        return float("nan")
    inst = inst_log[-window:]
    bench = bench_log[-window:]
    inst_ret = np.diff(inst)
    bench_ret = np.diff(bench)
    var_b = float(np.var(bench_ret))
    if var_b <= 0:
        return float("nan")
    cov = float(np.cov(inst_ret, bench_ret)[0, 1])
    return cov / var_b


@dataclass
class FeatureBuildContext:
    symbol: str
    family: str
    subclass: str
    horizon: Horizon
    decision_time_ms: int
    direction: int
    agreement_count: int
    conflict_flag: bool
    sig_tsmom: float = 0.0
    sig_carry: float = 0.0
    sig_xsec: float = 0.0
    sig_mr: float = 0.0
    xsec_pct: float = float("nan")
    family_dispersion: float = float("nan")
    benchmark_symbol: str = "SPY"
    cot_asset: str = ""
    cot_verified: bool = True
    missing_feeds: list[str] = field(default_factory=list)


def build_features_for_candidate(
    ctx: FeatureBuildContext,
    *,
    enriched: bool = False,
) -> dict[str, Any]:
    """Build one feature row for a candidate event at decision time."""
    cfg = HORIZONS[ctx.horizon]
    tf = cfg.tf
    need_bars = max(280, cfg.sigma_long_span + 32, 64 + 5)
    close_log = _asof_close_log(ctx.symbol, tf, ctx.decision_time_ms, need_bars)
    out: dict[str, Any] = {name: float("nan") for name in FEATURE_SCHEMA_ENRICHED}

    if len(close_log) < 32:
        out["instrument_id"] = ctx.symbol
        out["subclass"] = ctx.subclass
        out["candidate_dir"] = float(ctx.direction)
        out["agreement_count"] = float(ctx.agreement_count)
        out["conflict_flag"] = float(1.0 if ctx.conflict_flag else 0.0)
        return _select_schema(out, enriched)

    logret = np.full(len(close_log), np.nan)
    logret[1:] = close_log[1:] - close_log[:-1]
    sigma_bar = _ewma_vol(logret, cfg.sigma_bar_span)
    sigma_long = _ewma_vol(logret, cfg.sigma_long_span)
    sig = float(sigma_bar[-1])
    sig_l = float(sigma_long[-1])
    vol_level = sig / sig_l if sig_l == sig_l and sig_l > 0 else float("nan")
    out["vol_level"] = vol_level

    vol_tail = sigma_bar[-32:] if len(sigma_bar) >= 32 else sigma_bar
    vol_tail = vol_tail[~np.isnan(vol_tail)]
    if len(vol_tail) >= 2:
        out["vol_of_vol"] = float(np.std(vol_tail) / max(np.mean(vol_tail), 1e-12))
    out["vol_regime"] = float(_vol_regime_ordinal(vol_level))

    ret_lags = _RET_INTRADAY if ctx.horizon == "intraday" else _RET_SWING
    for name in _RET_ALL:
        if name not in ret_lags:
            continue
        lag = int(name.split("_")[-1])
        out[name] = _vol_scaled_return(close_log, sigma_bar, lag)

    if len(close_log) >= 64 and sig == sig and sig > 0:
        tail = close_log[-64:]
        scale = sig * 8.0
        peak = float(np.maximum.accumulate(tail)[-1])
        trough = float(np.minimum.accumulate(tail)[-1])
        out["dd_64"] = (peak - float(tail[-1])) / scale
        out["ru_64"] = (float(tail[-1]) - trough) / scale
        hi = float(np.max(tail))
        lo = float(np.min(tail))
        out["range_pos"] = (float(tail[-1]) - lo) / (hi - lo) if hi > lo else 0.5

    vol_series_id = (
        _duka_series_id(ctx.symbol, tf)
        if ctx.family == "forex"
        else _eodhd_series_id(ctx.symbol, tf, "volume")
    )
    vol_rows = _asof_values(vol_series_id, ctx.decision_time_ms, 64)
    if len(vol_rows) == 0:
        ctx.missing_feeds.append("volume")
        out["volu_z"] = float("nan")
    else:
        out["volu_z"] = _zscore_log_volume(vol_rows)

    out["xsec_pct"] = ctx.xsec_pct
    out["family_dispersion"] = ctx.family_dispersion

    bench_log = _asof_close_log(ctx.benchmark_symbol, tf, ctx.decision_time_ms, need_bars)
    if len(bench_log) >= 64:
        out["beta_bench"] = _rolling_beta(close_log, bench_log)
    else:
        ctx.missing_feeds.append("benchmark")

    out["sig_tsmom"] = float(ctx.sig_tsmom)
    out["sig_carry"] = float(ctx.sig_carry)
    out["sig_xsec"] = float(ctx.sig_xsec)
    out["sig_mr"] = float(ctx.sig_mr)
    out["agreement_count"] = float(ctx.agreement_count)
    out["conflict_flag"] = float(1.0 if ctx.conflict_flag else 0.0)
    out["candidate_dir"] = float(ctx.direction)

    dt = datetime.fromtimestamp(ctx.decision_time_ms / _MS, tz=timezone.utc)
    hour = dt.hour + dt.minute / 60.0
    dow = dt.weekday()
    out["hour_sin"] = math.sin(2 * math.pi * hour / 24.0)
    out["hour_cos"] = math.cos(2 * math.pi * hour / 24.0)
    out["dow_sin"] = math.sin(2 * math.pi * dow / 7.0)
    out["dow_cos"] = math.cos(2 * math.pi * dow / 7.0)
    out["session"] = float(_session_ordinal(dt.hour))

    out["instrument_id"] = ctx.symbol
    out["subclass"] = ctx.subclass

    if enriched and ctx.cot_asset and ctx.cot_verified:
        cot_rows = asof(_cot_series_id(ctx.cot_asset), ctx.decision_time_ms, 160)
        pct, delta, n_weeks = _cot_percentile(cot_rows)
        if n_weeks >= COT_MIN_WEEKS:
            out["cot_pct"] = pct
            out["cot_delta_4w"] = delta
        else:
            ctx.missing_feeds.append("cot_history")
    elif enriched and ctx.family in ("forex", "commodity"):
        ctx.missing_feeds.append("cot")

    if enriched and ctx.family == "crypto":
        fund = _asof_values(_bybit_series_id(ctx.symbol, "funding"), ctx.decision_time_ms, 64)
        oi = _asof_values(_bybit_series_id(ctx.symbol, "open_interest"), ctx.decision_time_ms, 64)
        if len(fund) >= 8:
            mu = float(np.mean(fund))
            sd = float(np.std(fund))
            out["funding_z"] = (float(fund[-1]) - mu) / sd if sd > 0 else 0.0
        else:
            ctx.missing_feeds.append("funding")
        if len(oi) >= 8:
            oi_ret = np.diff(np.log(np.maximum(oi, 1.0)))
            mu = float(np.mean(oi_ret))
            sd = float(np.std(oi_ret))
            out["oi_delta_z"] = (float(oi_ret[-1]) - mu) / sd if sd > 0 else 0.0
        else:
            ctx.missing_feeds.append("open_interest")

    return _select_schema(out, enriched)


def _select_schema(row: dict[str, Any], enriched: bool) -> dict[str, Any]:
    schema = FEATURE_SCHEMA_ENRICHED if enriched else FEATURE_SCHEMA_CORE
    return {k: row[k] for k in schema}


def build_feature_matrix(
    contexts: Sequence[FeatureBuildContext],
    *,
    enriched: bool = False,
) -> tuple[np.ndarray, list[str], list[dict[str, Any]]]:
    """Build feature matrix aligned to schema column order."""
    schema = FEATURE_SCHEMA_ENRICHED if enriched else FEATURE_SCHEMA_CORE
    rows: list[dict[str, Any]] = []
    for ctx in contexts:
        rows.append(build_features_for_candidate(ctx, enriched=enriched))

    matrix = np.empty((len(rows), len(schema)), dtype=float)
    for i, row in enumerate(rows):
        for j, name in enumerate(schema):
            val = row[name]
            if name in CATEGORICAL_FEATURES:
                matrix[i, j] = hash(str(val)) % 10_000 if val == val else float("nan")
            elif isinstance(val, (int, float)):
                matrix[i, j] = float(val)
            else:
                matrix[i, j] = float("nan")
    return matrix, list(schema), rows
