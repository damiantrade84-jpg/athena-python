"""Fast live-gate replay for current Engine A/B setup.

This is not a trade simulator. It replays historical candles through current
gate/scoring functions and records pass/fail reachability plus blocker reasons.
It intentionally skips SL/TP simulation, equity curves, DB writes, Monte Carlo,
and walk-forward validation.
"""

from __future__ import annotations

import bisect
import html
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from athena_research.data_loader import asset_class_for, load_ohlcv_multi, parquet_cache_health


DEFAULT_OUTPUT_DIR = Path("logs/fast_live_gate_replay")
DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT", "LINK/USDT", "DOGE/USDT", "XRP/USDT")
OUTPUT_FILES = {
    "run_meta": "run_meta.json",
    "progress": "progress.json",
    "gate_pass_matrix": "gate_pass_matrix.csv",
    "blocker_summary": "blocker_summary.csv",
    "pair_timeframe_summary": "pair_timeframe_summary.csv",
    "top_replay_candidates": "top_replay_candidates.csv",
    "outcome_trades": "outcome_trades.csv",
    "outcome_summary": "outcome_summary.csv",
    "report": "report.html",
}

_REAL_ORDER_CONFIRM_ENV = "ATHENA_REAL_ORDERS_CONFIRM"
_REAL_ORDER_CONFIRM_TOKEN = "I_UNDERSTAND_REAL_ORDER_RISK"


def _allow_read_only_config_import() -> None:
    """Allow config import in this read-only process without changing config files."""
    os.environ.setdefault(_REAL_ORDER_CONFIRM_ENV, _REAL_ORDER_CONFIRM_TOKEN)


@dataclass(frozen=True)
class LiveGateReplayConfig:
    market_group: str = "crypto"
    style: str = "intra"
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    engines: tuple[str, ...] = ("A", "B")
    timeframes: tuple[str, ...] = ("H4",)
    lookback_days: int = 730
    output_dir: Path | str = DEFAULT_OUTPUT_DIR
    run_id: str | None = None
    force_refresh: bool = False
    bar_stride: int = 1
    max_bars_per_symbol_tf: int | None = None
    min_history_bars: int = 260
    outcome_bars: tuple[int, ...] = (6, 12, 24)
    progress: bool = False


def _run_id() -> str:
    return f"live_gate_replay_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def _as_list(values: tuple[str, ...] | list[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [part.strip() for part in values.split(",") if part.strip()]
    return [str(part).strip() for part in values if str(part).strip()]


def _json_sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_sanitize(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _symbol_to_pair(symbol: str, market_group: str) -> dict:
    asset_type = asset_class_for(symbol) or market_group
    raw_symbol = symbol.replace("/", "") if asset_type == "crypto" else symbol
    return {
        "display": symbol,
        "symbol": raw_symbol,
        "type": asset_type,
        "source": "binance" if asset_type == "crypto" else "mt5",
        "enabled": True,
    }


def _limit_for(timeframe: str, lookback_days: int) -> int:
    per_day = {"D1": 1, "H4": 6, "H1": 24, "M15": 96}.get(str(timeframe).upper(), 24)
    return int(min(max(math.ceil(lookback_days * per_day) + 300, 500), 60_000))


def dataframe_to_candles(df: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    if df is None or df.empty:
        return out
    ordered = df.sort_index()
    for ts, row in ordered.iterrows():
        out.append(
            {
                "time": pd.Timestamp(ts).isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
        )
    return out


def _filter_recent(df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    if df.empty:
        return df
    end = df.index.max()
    return df.loc[df.index >= end - pd.Timedelta(days=int(lookback_days))].copy()


def load_replay_frames(config: LiveGateReplayConfig) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[str, dict]]:
    symbols = _as_list(config.symbols)
    tfs = sorted(set(_as_list(config.timeframes) + ["D1", "H4", "H1"]), key=lambda tf: {"D1": 0, "H4": 1, "H1": 2}.get(tf, 9))
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    provenance: dict[str, dict] = {}
    for tf in tfs:
        loaded = load_ohlcv_multi(
            symbols,
            tf,
            limit=_limit_for(tf, config.lookback_days),
            force_refresh=config.force_refresh,
            min_bars=50,
        )
        for symbol, (df, prov) in loaded.items():
            if df is None or df.empty:
                continue
            recent = _filter_recent(df, config.lookback_days)
            frames[(symbol, tf)] = recent
            provenance[f"{symbol}|{tf}"] = prov.to_dict()
    return frames, provenance


def _times(candles: list[dict]) -> list[pd.Timestamp]:
    return [pd.Timestamp(c["time"]) for c in candles]


def _idx_at_or_before(times: list[pd.Timestamp], ts: pd.Timestamp) -> int:
    return bisect.bisect_right(times, ts) - 1


def _window(candles: list[dict], end_idx: int, size: int) -> list[dict]:
    if end_idx < 0:
        return []
    return candles[max(0, end_idx - size + 1) : end_idx + 1]


def _direction_counts(rows: list[dict]) -> str:
    counter = Counter(r.get("direction") or "NONE" for r in rows if r.get("passed"))
    return ";".join(f"{k}={v}" for k, v in sorted(counter.items()))


def _summary_from_details(engine: str, symbol: str, timeframe: str, rows: list[dict], threshold: float) -> dict:
    scores = [_finite(r.get("score"), float("nan")) for r in rows]
    scores = [s for s in scores if math.isfinite(s)]
    passes = [r for r in rows if r.get("passed")]
    blockers = Counter(r.get("blocker") or "passed" for r in rows)
    top_blocker = next((k for k, _ in blockers.most_common() if k != "passed"), "")
    near_miss = 0
    for r in rows:
        score = _finite(r.get("score"), 0.0)
        if not r.get("passed") and threshold > 0 and score >= threshold * 0.9:
            near_miss += 1
    return {
        "engine": engine,
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_evaluated": len(rows),
        "passes": len(passes),
        "pass_rate": round(len(passes) / len(rows), 6) if rows else 0.0,
        "near_miss": near_miss,
        "top_blocker": top_blocker,
        "threshold": threshold,
        "max_score": max(scores) if scores else 0.0,
        "mean_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
        "directions": _direction_counts(rows),
    }


def _calc_snap(candles: list[dict], asset_type: str) -> dict | None:
    if len(candles) < 50:
        return None
    _allow_read_only_config_import()
    from indicators import calc_indicators_with_normalized

    return calc_indicators_with_normalized(candles, asset_type)


def _replay_engine_a(config: LiveGateReplayConfig, symbol: str, timeframe: str, frames: dict[tuple[str, str], pd.DataFrame]) -> tuple[list[dict], list[dict]]:
    _allow_read_only_config_import()
    from scoring import calc_confluence, engine_a_regime_label_for_threshold, get_score_threshold, is_trend_state_blocked

    pair = _symbol_to_pair(symbol, config.market_group)
    asset_type = pair["type"]
    d1 = dataframe_to_candles(frames.get((symbol, "D1"), pd.DataFrame()))
    h4 = dataframe_to_candles(frames.get((symbol, "H4"), pd.DataFrame()))
    h1 = dataframe_to_candles(frames.get((symbol, "H1"), pd.DataFrame()))
    base = h1 if timeframe == "H1" else h4
    base_times = _times(base)
    d1_times, h4_times, h1_times = _times(d1), _times(h4), _times(h1)
    details: list[dict] = []
    max_rows = config.max_bars_per_symbol_tf
    start = max(config.min_history_bars, 60)
    indices = list(range(start, len(base), max(1, int(config.bar_stride))))
    if max_rows:
        indices = indices[-int(max_rows):]

    static_threshold = get_score_threshold(pair)
    for idx in indices:
        ts = base_times[idx]
        d1_idx = _idx_at_or_before(d1_times, ts)
        h4_idx = _idx_at_or_before(h4_times, ts)
        h1_idx = _idx_at_or_before(h1_times, ts)
        d1_w, h4_w, h1_w = _window(d1, d1_idx, 220), _window(h4, h4_idx, 250), _window(h1, h1_idx, 250)
        try:
            d1i, h4i, h1i = _calc_snap(d1_w, asset_type), _calc_snap(h4_w, asset_type), _calc_snap(h1_w, asset_type)
            if not d1i or not h4i or not h1i:
                continue
            vr = _finite((h1i.get("snap") or {}).get("volumeRatio"), 1.0)
            stoch = {
                "k": (h4i.get("snap") or {}).get("stochK"),
                "d": (h4i.get("snap") or {}).get("stochD"),
            }
            res = calc_confluence(
                d1i,
                h4i,
                h1i,
                vr,
                stoch,
                pair,
                "neutral",
                d1_candles=d1_w,
                h4_candles=h4_w,
                h1_candles=h1_w,
                bar_time=base[idx].get("time"),
            )
            regime = engine_a_regime_label_for_threshold(res)
            threshold = get_score_threshold(pair, regime=regime)
            score = _finite(res.get("score"), 0.0)
            direction = res.get("direction")
            blocker = ""
            passed = True
            if direction not in ("LONG", "SHORT"):
                passed, blocker = False, "no_direction"
            elif score < threshold:
                passed, blocker = False, "below_score"
            elif is_trend_state_blocked(res.get("trendState", "UNKNOWN"), pair, scope="backtest"):
                passed, blocker = False, "blocked_regime"
            details.append(
                {
                    "engine": "A",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar_time": base[idx].get("time"),
                    "passed": passed,
                    "score": score,
                    "threshold": threshold,
                    "direction": direction,
                    "blocker": blocker,
                    "regime": regime,
                }
            )
        except Exception as exc:
            details.append(
                {
                    "engine": "A",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar_time": base[idx].get("time"),
                    "passed": False,
                    "score": 0.0,
                    "threshold": static_threshold,
                    "direction": "",
                    "blocker": f"error:{type(exc).__name__}",
                }
            )
    return [_summary_from_details("A", symbol, timeframe, details, static_threshold)], details


def _atr_for(candles: list[dict], period: int = 14) -> list:
    _allow_read_only_config_import()
    from indicators import calc_atr

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    return calc_atr(highs, lows, closes, period)


def _engine_b_style_profile(style: str) -> tuple[str, dict]:
    _allow_read_only_config_import()
    from config import CONFIG

    style_key = {"intra": "intraday", "day": "intraday"}.get(str(style or "").lower(), str(style or "intraday"))
    profiles = ((CONFIG.get("NAKED_ENGINE") or {}).get("style_profiles") or {})
    return style_key, dict(profiles.get(style_key) or profiles.get("intraday") or {})


def _replay_engine_b(config: LiveGateReplayConfig, symbol: str, timeframe: str, frames: dict[tuple[str, str], pd.DataFrame]) -> tuple[list[dict], list[dict]]:
    _allow_read_only_config_import()
    from market_structure import NakedEngine, engine_b_confidence_passes, engine_b_min_score_threshold

    pair = _symbol_to_pair(symbol, config.market_group)
    asset_type = pair["type"]
    resolved_style, style_profile = _engine_b_style_profile(config.style)
    zone_tf = str(style_profile.get("zone_tf", "H4")).upper()
    entry_tf = str(style_profile.get("entry_tf", "H1")).upper()
    atr_tf = str(style_profile.get("atr_tf", "H4")).upper()

    d1 = dataframe_to_candles(frames.get((symbol, "D1"), pd.DataFrame()))
    h4 = dataframe_to_candles(frames.get((symbol, "H4"), pd.DataFrame()))
    h1 = dataframe_to_candles(frames.get((symbol, "H1"), pd.DataFrame()))
    tf_map = {"D1": d1, "H4": h4, "H1": h1}
    base = tf_map.get(timeframe, h4)
    zone = tf_map.get(zone_tf, h4)
    entry = tf_map.get(entry_tf, h1)
    atr_candles = tf_map.get(atr_tf, h4)
    atr_values = _atr_for(atr_candles) if atr_candles else []
    base_times = _times(base)
    d1_times, zone_times, entry_times, atr_times = _times(d1), _times(zone), _times(entry), _times(atr_candles)
    engine = NakedEngine()
    details: list[dict] = []
    start = max(config.min_history_bars, 60)
    indices = list(range(start, len(base), max(1, int(config.bar_stride))))
    if config.max_bars_per_symbol_tf:
        indices = indices[-int(config.max_bars_per_symbol_tf):]
    base_threshold = engine_b_min_score_threshold(style_profile, None, asset_type)

    for idx in indices:
        ts = base_times[idx]
        d1_idx = _idx_at_or_before(d1_times, ts)
        zone_idx = _idx_at_or_before(zone_times, ts)
        entry_idx = _idx_at_or_before(entry_times, ts)
        atr_idx = _idx_at_or_before(atr_times, ts)
        d1_w = _window(d1, d1_idx, 220)
        zone_w = _window(zone, zone_idx, 250)
        entry_w = _window(entry, entry_idx, 250)
        if not zone_w or not entry_w:
            continue
        current_price = _finite(entry_w[-1].get("close"), 0.0)
        atr = _finite(atr_values[atr_idx] if 0 <= atr_idx < len(atr_values) else None, current_price * 0.01)
        try:
            d1_snap = (_calc_snap(d1_w, asset_type) or {}).get("snap") or {}
            zone_snap = (_calc_snap(zone_w, asset_type) or {}).get("snap") or {}
            regime_label = "RANGING"
            best_detail: dict | None = None
            engine.set_registry_context(symbol)
            pre = engine.precompute_structure_data(
                d1_w,
                zone_w,
                entry_w,
                current_price,
                atr,
                regime=regime_label,
                fallback_rr=style_profile.get("fallback_rr", 2.0),
                asset_type=asset_type,
                d1_snap=d1_snap,
                h4_snap=zone_snap,
                style=resolved_style,
                pair=pair,
            )
            for direction in ("LONG", "SHORT"):
                res = engine.analyze_structure_direction(pre, current_price, direction)
                score = 0.0
                threshold = base_threshold
                blocker = "not_clear"
                passed = False
                if res.get("structural_verdict") == "CLEAR":
                    conf = engine.calculate_confidence(
                        res,
                        current_price,
                        direction,
                        entry_candles=entry_w,
                        style_profile=style_profile,
                    )
                    gate_ok, threshold = engine_b_confidence_passes(conf, style_profile, regime_label, asset_type)
                    score = _finite(conf.get("score"), 0.0)
                    if gate_ok:
                        passed, blocker = True, ""
                    elif not conf.get("structure_ok"):
                        blocker = "structure"
                    elif not conf.get("location_ok"):
                        blocker = "location"
                    elif not conf.get("entry_ok"):
                        blocker = "entry"
                    elif not conf.get("room_ok"):
                        blocker = "room"
                    elif not conf.get("rr_ok"):
                        blocker = "rr"
                    elif not conf.get("macro_ok", True):
                        blocker = "macro"
                    else:
                        blocker = "below_score"
                detail = {
                    "engine": "B",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar_time": base[idx].get("time"),
                    "passed": passed,
                    "score": score,
                    "threshold": threshold,
                    "direction": direction,
                    "blocker": blocker,
                    "regime": regime_label,
                }
                if best_detail is None or (bool(detail["passed"]), detail["score"]) > (bool(best_detail["passed"]), _finite(best_detail["score"])):
                    best_detail = detail
            if best_detail:
                details.append(best_detail)
        except Exception as exc:
            details.append(
                {
                    "engine": "B",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar_time": base[idx].get("time"),
                    "passed": False,
                    "score": 0.0,
                    "threshold": base_threshold,
                    "direction": "",
                    "blocker": f"error:{type(exc).__name__}",
                    "regime": "",
                }
            )
    return [_summary_from_details("B", symbol, timeframe, details, base_threshold)], details


def _replay_symbol_engine(
    config: LiveGateReplayConfig,
    symbol: str,
    engine: str,
    timeframe: str,
    frames: dict[tuple[str, str], pd.DataFrame],
) -> tuple[list[dict], list[dict]]:
    if engine.upper() == "A":
        return _replay_engine_a(config, symbol, timeframe, frames)
    if engine.upper() == "B":
        return _replay_engine_b(config, symbol, timeframe, frames)
    return [], []


def _blocker_summary(details_df: pd.DataFrame) -> pd.DataFrame:
    if details_df.empty:
        return pd.DataFrame(columns=["engine", "symbol", "timeframe", "blocker", "count"])
    grouped = details_df.groupby(["engine", "symbol", "timeframe", "blocker"], dropna=False).size().reset_index(name="count")
    return grouped.sort_values(["engine", "symbol", "timeframe", "count"], ascending=[True, True, True, False])


def _top_candidates(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df.empty:
        return summary_df.copy()
    return summary_df.sort_values(["passes", "pass_rate", "max_score"], ascending=[False, False, False]).copy()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_sanitize(payload), indent=2, sort_keys=True), encoding="utf-8")


def _html_table(df: pd.DataFrame, columns: list[str], limit: int = 80) -> str:
    if df.empty:
        return "<p>No rows.</p>"
    subset = df[[c for c in columns if c in df.columns]].head(limit)
    header = "".join(f"<th>{html.escape(str(c))}</th>" for c in subset.columns)
    rows = []
    for row in subset.to_dict(orient="records"):
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(c, '')))}</td>" for c in subset.columns) + "</tr>")
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _sqn(values: list[float]) -> float:
    clean = [float(v) for v in values if math.isfinite(float(v))]
    if len(clean) < 2:
        return 0.0
    std = float(np.std(clean, ddof=1))
    if std <= 0:
        return 0.0
    return round(math.sqrt(len(clean)) * (float(np.mean(clean)) / std), 6)


def _outcome_dataframes(detail_rows: list[dict], frames: dict[tuple[str, str], pd.DataFrame], horizons: tuple[int, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades: list[dict] = []
    sorted_frames: dict[tuple[str, str], pd.DataFrame] = {}
    for key, df in frames.items():
        if df is not None and not df.empty:
            sorted_frames[key] = df.sort_index()
    for row in detail_rows:
        if not row.get("passed"):
            continue
        direction = str(row.get("direction") or "").upper()
        if direction not in {"LONG", "SHORT"}:
            continue
        symbol = str(row.get("symbol") or "")
        timeframe = str(row.get("timeframe") or "").upper()
        df = sorted_frames.get((symbol, timeframe))
        if df is None or df.empty:
            continue
        ts = pd.Timestamp(row.get("bar_time"))
        index = df.index
        idx = int(index.searchsorted(ts, side="right") - 1)
        if idx < 0 or idx >= len(df):
            continue
        entry = _finite(df.iloc[idx].get("close"), float("nan"))
        if not math.isfinite(entry) or entry <= 0:
            continue
        for horizon in horizons:
            future_idx = idx + int(horizon)
            if future_idx >= len(df):
                continue
            future = _finite(df.iloc[future_idx].get("close"), float("nan"))
            if not math.isfinite(future):
                continue
            window = df.iloc[idx + 1 : future_idx + 1]
            if window.empty:
                continue
            if direction == "LONG":
                gross_return = (future - entry) / entry
                mfe = (_finite(window["high"].max(), entry) - entry) / entry
                mae = (_finite(window["low"].min(), entry) - entry) / entry
            else:
                gross_return = (entry - future) / entry
                mfe = (entry - _finite(window["low"].min(), entry)) / entry
                mae = (entry - _finite(window["high"].max(), entry)) / entry
            trades.append(
                {
                    "engine": row.get("engine"),
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar_time": row.get("bar_time"),
                    "direction": direction,
                    "score": row.get("score"),
                    "threshold": row.get("threshold"),
                    "horizon_bars": int(horizon),
                    "entry_close": entry,
                    "exit_close": future,
                    "gross_return_pct": round(gross_return * 100.0, 6),
                    "mfe_pct": round(mfe * 100.0, 6),
                    "mae_pct": round(mae * 100.0, 6),
                    "win": gross_return > 0,
                }
            )
    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return trades_df, pd.DataFrame(
            columns=[
                "engine",
                "symbol",
                "timeframe",
                "horizon_bars",
                "trades",
                "wr",
                "avg_return_pct",
                "median_return_pct",
                "sqn",
                "avg_mfe_pct",
                "avg_mae_pct",
                "reliability",
            ]
        )
    summary_rows: list[dict] = []
    grouped = trades_df.groupby(["engine", "symbol", "timeframe", "horizon_bars"], dropna=False)
    for keys, group in grouped:
        returns = [float(v) for v in group["gross_return_pct"].tolist()]
        n = len(group)
        summary_rows.append(
            {
                "engine": keys[0],
                "symbol": keys[1],
                "timeframe": keys[2],
                "horizon_bars": int(keys[3]),
                "trades": n,
                "wr": round(float(group["win"].mean()), 6) if n else 0.0,
                "avg_return_pct": round(float(group["gross_return_pct"].mean()), 6),
                "median_return_pct": round(float(group["gross_return_pct"].median()), 6),
                "sqn": _sqn(returns),
                "avg_mfe_pct": round(float(group["mfe_pct"].mean()), 6),
                "avg_mae_pct": round(float(group["mae_pct"].mean()), 6),
                "reliability": "low_sample" if n < 30 else "medium_sample" if n < 100 else "ok",
            }
        )
    summary_df = pd.DataFrame(summary_rows).sort_values(["sqn", "wr", "avg_return_pct"], ascending=[False, False, False])
    return trades_df, summary_df


def _write_report(
    path: Path,
    meta: dict,
    summary_df: pd.DataFrame,
    blockers_df: pd.DataFrame,
    top_df: pd.DataFrame,
    outcome_summary_df: pd.DataFrame,
) -> None:
    meta_text = html.escape(json.dumps(_json_sanitize(meta), indent=2, sort_keys=True))
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Fast Live Gate Replay {html.escape(str(meta.get("run_id", "")))}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #202124; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; }}
    th {{ background: #f6f8fa; }}
    pre {{ background: #f6f8fa; border: 1px solid #d0d7de; padding: 12px; overflow-x: auto; }}
  </style>
</head>
<body>
  <h1>Fast Live Gate Replay</h1>
  <p>Read-only current-gate replay. This is not a trade simulator.</p>
  <p>Outcome WR/SQN uses forward close-to-close movement after a gate pass. It does not include SL/TP, fees, slippage, risk sizing, or execution checks.</p>
  <h2>Forward Outcome WR / SQN</h2>
  {_html_table(outcome_summary_df, ["engine", "symbol", "timeframe", "horizon_bars", "trades", "wr", "avg_return_pct", "median_return_pct", "sqn", "avg_mfe_pct", "avg_mae_pct", "reliability"])}
  <h2>Top Replay Candidates</h2>
  {_html_table(top_df, ["engine", "symbol", "timeframe", "bars_evaluated", "passes", "pass_rate", "near_miss", "threshold", "max_score", "mean_score", "top_blocker", "directions"])}
  <h2>Pair / Timeframe Summary</h2>
  {_html_table(summary_df, ["engine", "symbol", "timeframe", "bars_evaluated", "passes", "pass_rate", "near_miss", "threshold", "max_score", "mean_score", "top_blocker", "directions"])}
  <h2>Blocker Summary</h2>
  {_html_table(blockers_df, ["engine", "symbol", "timeframe", "blocker", "count"])}
  <h2>Run Metadata</h2>
  <pre>{meta_text}</pre>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def _replay_dataframes(
    summary_rows: list[dict],
    detail_rows: list[dict],
    frames: dict[tuple[str, str], pd.DataFrame],
    horizons: tuple[int, ...],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_df = pd.DataFrame(summary_rows)
    details_df = pd.DataFrame(detail_rows)
    blockers_df = _blocker_summary(details_df)
    top_df = _top_candidates(summary_df)
    outcome_trades_df, outcome_summary_df = _outcome_dataframes(detail_rows, frames, horizons)
    return summary_df, details_df, blockers_df, top_df, outcome_trades_df, outcome_summary_df


def _write_replay_outputs(
    files: dict[str, Path],
    meta: dict,
    summary_rows: list[dict],
    detail_rows: list[dict],
    frames: dict[tuple[str, str], pd.DataFrame],
    horizons: tuple[int, ...],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_df, details_df, blockers_df, top_df, outcome_trades_df, outcome_summary_df = _replay_dataframes(
        summary_rows,
        detail_rows,
        frames,
        horizons,
    )
    details_df.to_csv(files["gate_pass_matrix"], index=False)
    blockers_df.to_csv(files["blocker_summary"], index=False)
    summary_df.to_csv(files["pair_timeframe_summary"], index=False)
    top_df.to_csv(files["top_replay_candidates"], index=False)
    outcome_trades_df.to_csv(files["outcome_trades"], index=False)
    outcome_summary_df.to_csv(files["outcome_summary"], index=False)
    meta["summary_rows"] = len(summary_df)
    meta["detail_rows"] = len(details_df)
    meta["outcome_rows"] = len(outcome_summary_df)
    meta["outcome_trade_rows"] = len(outcome_trades_df)
    _write_json(files["run_meta"], meta)
    _write_report(files["report"], meta, summary_df, blockers_df, top_df, outcome_summary_df)
    return summary_df, details_df, blockers_df, top_df, outcome_trades_df, outcome_summary_df


def run_fast_live_gate_replay(config: LiveGateReplayConfig | None = None) -> dict:
    config = config or LiveGateReplayConfig()
    run_id = config.run_id or _run_id()
    run_dir = Path(config.output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    frames, provenance = load_replay_frames(config)
    files = {
        "run_meta": run_dir / OUTPUT_FILES["run_meta"],
        "progress": run_dir / OUTPUT_FILES["progress"],
        "gate_pass_matrix": run_dir / OUTPUT_FILES["gate_pass_matrix"],
        "blocker_summary": run_dir / OUTPUT_FILES["blocker_summary"],
        "pair_timeframe_summary": run_dir / OUTPUT_FILES["pair_timeframe_summary"],
        "top_replay_candidates": run_dir / OUTPUT_FILES["top_replay_candidates"],
        "outcome_trades": run_dir / OUTPUT_FILES["outcome_trades"],
        "outcome_summary": run_dir / OUTPUT_FILES["outcome_summary"],
        "report": run_dir / OUTPUT_FILES["report"],
    }
    symbols = _as_list(config.symbols)
    engines = [e.upper() for e in _as_list(config.engines)]
    timeframes = [tf.upper() for tf in _as_list(config.timeframes)]
    work_items = [(symbol, engine, timeframe) for symbol in symbols for engine in engines for timeframe in timeframes]
    meta = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "live_gate_replay": True,
        "not_trade_simulation": True,
        "market_group": config.market_group,
        "style": config.style,
        "symbols": symbols,
        "engines": engines,
        "timeframes": timeframes,
        "lookback_days": config.lookback_days,
        "bar_stride": config.bar_stride,
        "max_bars_per_symbol_tf": config.max_bars_per_symbol_tf,
        "outcome_bars": list(config.outcome_bars),
        "cache_health": parquet_cache_health(),
        "data_provenance": provenance,
        "live_context_notes": [
            "Engine A uses current calc_confluence/get_score_threshold/is_trend_state_blocked.",
            "Engine B uses current NakedEngine/engine_b_confidence_passes/style_profiles.",
            "Replay skips SL/TP simulation, execution/risk gates, DB writes, Monte Carlo, and walk-forward equity validation.",
            "WR/SQN outcomes are forward close-to-close movement after a gate pass, not full order-level strategy results.",
            "Crypto funding/OI/macro overlays are not fetched in this fast replay.",
            "The process sets ATHENA_REAL_ORDERS_CONFIRM only to permit read-only config imports; it does not execute orders.",
        ],
        "forbidden_writes": ["config.yaml", "thresholds", "live_state", "audit.db", "broker/execution files"],
        "summary_rows": 0,
        "detail_rows": 0,
    }
    summary_rows: list[dict] = []
    detail_rows: list[dict] = []
    horizons = tuple(sorted({max(1, int(v)) for v in config.outcome_bars}))
    _write_replay_outputs(files, meta, summary_rows, detail_rows, frames, horizons)
    total = len(work_items)
    started_at = datetime.now(timezone.utc)
    for index, (symbol, engine, timeframe) in enumerate(work_items, start=1):
        if config.progress:
            print(f"[replay] start {index}/{total} {engine} {symbol} {timeframe}", flush=True)
        rows, details = _replay_symbol_engine(config, symbol, engine, timeframe, frames)
        summary_rows.extend(rows)
        detail_rows.extend(details)
        summary_df, details_df, blockers_df, top_df, outcome_trades_df, outcome_summary_df = _write_replay_outputs(
            files,
            meta,
            summary_rows,
            detail_rows,
            frames,
            horizons,
        )
        progress_payload = {
            "run_id": run_id,
            "started_at_utc": started_at.isoformat(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "completed_items": index,
            "total_items": total,
            "last_item": {"symbol": symbol, "engine": engine, "timeframe": timeframe},
            "summary_rows": len(summary_df),
            "detail_rows": len(details_df),
            "outcome_rows": len(outcome_summary_df),
            "outcome_trade_rows": len(outcome_trades_df),
            "report": str(files["report"]),
        }
        _write_json(files["progress"], progress_payload)
        if config.progress:
            print(f"[replay] done {index}/{total} {engine} {symbol} {timeframe} rows={len(details)}", flush=True)
    return {"run_id": run_id, "run_dir": run_dir, "files": files, "summary_rows": len(summary_df), "detail_rows": len(details_df)}
