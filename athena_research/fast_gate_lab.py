"""
Fast A/B Gate Research Lab.

Standalone read-only screening for Engine A and Engine B gate/indicator research.
The Stage 1 screen is vectorized and lightweight; Stage 2 only confirms the top
screened candidates through the existing Research Lab evaluate_strategy path.
"""

from __future__ import annotations

import html
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from athena_research.data_loader import (
    DataProvenance,
    asset_class_for,
    load_ohlcv_multi,
    parquet_cache_health,
    vbt_freq,
)
from athena_research.metrics import StrategyMetrics, evaluate_strategy
from athena_research.strategies import StrategySpec, iter_strategy_specs, run_strategy


DEFAULT_CONFIG_PATH = Path("configs/vectorbt_research_lab.yaml")
DEFAULT_OUTPUT_DIR = Path("logs/fast_gate_lab")

ENGINE_A_FAMILIES = (
    "trend_momentum",
    "pullback",
    "mean_reversion",
    "volatility",
    "stochastic",
    "divergence",
    "trend_follow",
    "vol_regime",
)
ENGINE_B_FAMILIES = ("engine_b_proxy",)

ENGINE_A_GATE_SWEEP = ("current", "-0.2", "-0.1", "+0.1", "+0.2")
ENGINE_B_GATE_SWEEP = ("current", "-0.5", "+0.5")
RUNTIME_CONFIG_PATH = Path("config.yaml")

_ALTCOIN_MAJORS = {
    "SOL/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "POL/USDT",
    "BNB/USDT",
    "DOT/USDT",
    "LTC/USDT",
    "SUI/USDT",
    "NEAR/USDT",
    "APT/USDT",
    "INJ/USDT",
    "RENDER/USDT",
}
_MAJOR_FOREX = {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD", "USD/CAD", "USD/CHF"}
_FOREX_CROSSES = {"EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD", "GBP/AUD", "AUD/CHF", "AUD/NZD", "EUR/CHF"}
_EXOTIC_FOREX = {"USD/ZAR", "USD/MXN", "USD/SGD", "USD/BRL", "USD/INR", "USD/TRY"}

OUTPUT_FILES = {
    "run_meta": "run_meta.json",
    "gate_matrix": "gate_matrix.csv",
    "indicator_attribution": "indicator_attribution.csv",
    "pair_summary": "pair_summary.csv",
    "top_candidates": "top_candidates.csv",
    "screening_details": "screening_details.json",
    "report": "report.html",
}


@dataclass(frozen=True)
class FastGateConfig:
    market_group: str = "crypto"
    style: str = "intra"
    timeframes: tuple[str, ...] = ("H1", "H4")
    engines: tuple[str, ...] = ("A", "B")
    lookback_days: int = 180
    symbols: tuple[str, ...] | None = None
    top_n: int = 25
    max_candidates: int = 250
    min_bars: int = 50
    forward_bars: int = 5
    output_dir: Path | str = DEFAULT_OUTPUT_DIR
    run_id: str | None = None
    config_path: Path | str = DEFAULT_CONFIG_PATH
    force_refresh: bool = False
    engine_a_gate_sweep: tuple[str, ...] = ENGINE_A_GATE_SWEEP
    engine_b_gate_sweep: tuple[str, ...] = ENGINE_B_GATE_SWEEP


def _run_id() -> str:
    return f"fast_gate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def _as_list(values: tuple[str, ...] | list[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [part.strip() for part in values.split(",") if part.strip()]
    return [str(part).strip() for part in values if str(part).strip()]


def _load_research_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("research_lab", raw)


def _load_runtime_yaml(path: str | Path = RUNTIME_CONFIG_PATH) -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _group_symbols(research_cfg: dict, market_group: str) -> list[str]:
    aliases = {"metals": "commodity", "indices": "index", "stocks": "stock"}
    group_key = aliases.get(market_group, market_group)
    symbols = (research_cfg.get("symbols") or {}).get(group_key, [])
    return list(symbols) if isinstance(symbols, list) else []


def _engine_b_style_key(style: str) -> str:
    return {"intra": "intraday", "day": "intraday"}.get(str(style or "").lower(), str(style or ""))


def _fallback_pair_score_group(symbol: str, market_group: str, runtime_cfg: dict) -> str:
    profiles = runtime_cfg.get("PAIR_PROFILES") or {}
    profile = profiles.get(symbol) or {}
    if profile.get("score_group"):
        return str(profile["score_group"])

    asset_type = asset_class_for(symbol) or market_group
    if asset_type == "crypto":
        if symbol == "BTC/USDT":
            return "crypto_btc"
        if symbol == "ETH/USDT":
            return "crypto_eth"
        if symbol == "DOGE/USDT":
            return "crypto_doge"
        if symbol in _ALTCOIN_MAJORS:
            return "crypto_alt_majors"
        return "crypto_other"
    if asset_type == "forex":
        if symbol in _MAJOR_FOREX:
            return "forex_majors"
        if symbol in _FOREX_CROSSES:
            return "forex_crosses"
        if symbol in _EXOTIC_FOREX:
            return "forex_exotics"
        return "forex_other"
    if asset_type == "commodity":
        if symbol in {"XAU/USD", "XAG/USD", "GLD", "SLV"}:
            return "precious_trackers"
        if symbol in {"WTI Oil", "Brent Oil", "USOIL", "UKOIL", "USO"}:
            return "energy_oil"
        if symbol == "Nat Gas":
            return "nat_gas"
        return "commodity_other"
    return f"{asset_type}_other" if asset_type else "unknown"


def _fallback_engine_a_threshold(symbol: str, market_group: str) -> tuple[float, str]:
    runtime_cfg = _load_runtime_yaml()
    pair_group = _fallback_pair_score_group(symbol, market_group, runtime_cfg)
    asset_type = asset_class_for(symbol) or market_group
    profile = (runtime_cfg.get("PAIR_PROFILES") or {}).get(symbol) or {}

    for key in (symbol,):
        pair_threshold = (runtime_cfg.get("ENGINE_A_PAIR_THRESHOLDS") or {}).get(key)
        if pair_threshold is not None:
            threshold = float(pair_threshold)
            break
    else:
        group_thresholds = runtime_cfg.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {}
        if pair_group in group_thresholds:
            threshold = float(group_thresholds[pair_group])
        elif "default" in group_thresholds:
            threshold = float(group_thresholds["default"])
        elif symbol in {"XAU/USD", "XAG/USD"}:
            threshold = 1.5
        elif asset_type == "crypto" or pair_group in {"nat_gas", "crypto_doge"}:
            threshold = 2.0
        elif pair_group in {"forex_exotics", "softs"}:
            threshold = 1.7
        else:
            threshold = 1.5

    if profile.get("min_confluence") is not None:
        profile_threshold = float(profile["min_confluence"])
        if profile_threshold >= threshold or profile.get("allow_lower_threshold") is True:
            threshold = profile_threshold

    return float(threshold), pair_group


def _fallback_engine_b_threshold(style: str) -> float:
    runtime_cfg = _load_runtime_yaml()
    style_key = _engine_b_style_key(style)
    profile = ((runtime_cfg.get("NAKED_ENGINE") or {}).get("style_profiles") or {}).get(style_key, {})
    base_min = float(profile.get("min_score", 0.0) or 0.0)
    if base_min <= 0:
        return 0.0
    return float(math.ceil((base_min * 10.0) - 1e-12) / 10.0)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


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


def _params_str(params: dict[str, Any]) -> str:
    return "|".join(f"{k}={v}" for k, v in sorted((params or {}).items()))


def _fee_for(symbol: str, fees_cfg: dict) -> float:
    asset_class = asset_class_for(symbol)
    round_trip_fee = float((fees_cfg or {}).get(asset_class, (fees_cfg or {}).get("default", 0.001)))
    return max(0.0, round_trip_fee / 2.0)


def _limit_for_lookback(timeframe: str, lookback_days: int) -> int:
    bars_per_day = {
        "M1": 1440,
        "M5": 288,
        "M15": 96,
        "H1": 24,
        "H4": 6,
        "D1": 1,
        "W1": 1 / 7,
    }.get(str(timeframe).upper(), 24)
    return int(min(max(math.ceil(lookback_days * bars_per_day) + 100, 250), 50_000))


def filter_recent_window(df: pd.DataFrame, lookback_days: int = 180) -> pd.DataFrame:
    """Keep only the most recent lookback_days from a DatetimeIndex OHLCV frame."""
    if df is None or df.empty:
        return pd.DataFrame()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("filter_recent_window requires a DatetimeIndex")
    end = df.index.max()
    cutoff = end - pd.Timedelta(days=int(lookback_days))
    return df.loc[df.index >= cutoff].copy()


def gate_sweep_values(engine: str, current_threshold: float, sweep: tuple[str, ...] | None = None) -> list[float]:
    """Resolve gate sweep tokens into absolute threshold values."""
    engine_key = str(engine).upper()
    tokens = sweep or (ENGINE_A_GATE_SWEEP if engine_key == "A" else ENGINE_B_GATE_SWEEP)
    current = _finite(current_threshold, 0.0)
    out: list[float] = []
    for token in tokens:
        token_s = str(token).strip().lower()
        if token_s == "current":
            value = current
        else:
            value = current + float(token_s)
        out.append(round(value, 10))
    return out


def engine_research_label(engine: str) -> str:
    engine_key = str(engine).upper()
    if engine_key == "A":
        return "live_aligned_engine_a"
    if engine_key == "B":
        return "proxy_engine_b"
    return "unsupported_engine"


def _current_engine_a_threshold(symbol: str, market_group: str) -> tuple[float, str]:
    if os.environ.get("FAST_GATE_LAB_USE_RUNTIME_RESOLVERS") != "1":
        return _fallback_engine_a_threshold(symbol, market_group)
    asset_type = asset_class_for(symbol) or market_group
    pair = {"display": symbol, "symbol": symbol, "type": asset_type}
    try:
        from scoring import get_pair_score_group, get_score_threshold

        pair_group = str(get_pair_score_group(pair))
        pair["score_group"] = pair_group
        return float(get_score_threshold(pair)), pair_group
    except BaseException:
        return _fallback_engine_a_threshold(symbol, market_group)


def _current_engine_b_threshold(style: str) -> float:
    if os.environ.get("FAST_GATE_LAB_USE_RUNTIME_RESOLVERS") != "1":
        return _fallback_engine_b_threshold(style)
    try:
        from config import CONFIG
        from market_structure import engine_b_min_score_threshold

        style_key = _engine_b_style_key(style)
        profile = ((CONFIG.get("NAKED_ENGINE") or {}).get("style_profiles") or {}).get(style_key, {})
        return float(engine_b_min_score_threshold(profile, None, "crypto"))
    except BaseException:
        return _fallback_engine_b_threshold(style)


def _strategy_specs_for_engine(config: FastGateConfig, engine: str, timeframe: str) -> list[StrategySpec]:
    research_cfg = _load_research_config(config.config_path)
    strategies_cfg = research_cfg.get("strategies") or {}
    engine_key = str(engine).upper()
    families = ENGINE_A_FAMILIES if engine_key == "A" else ENGINE_B_FAMILIES
    specs = list(
        iter_strategy_specs(
            list(families),
            strategies_cfg,
            direction="both",
            trading_style=config.style,
            market_group=config.market_group,
            timeframe=timeframe,
        )
    )
    if config.max_candidates and config.max_candidates > 0:
        return specs[: max(1, int(config.max_candidates))]
    return specs


def load_symbol_frames(config: FastGateConfig) -> dict[tuple[str, str], tuple[pd.DataFrame, DataProvenance]]:
    """Load and recent-filter OHLCV frames for the requested symbol/timeframe grid."""
    research_cfg = _load_research_config(config.config_path)
    symbols = _as_list(config.symbols) or _group_symbols(research_cfg, config.market_group)
    timeframes = _as_list(config.timeframes)
    data_cfg = research_cfg.get("data") or {}
    cache_dir = Path(data_cfg.get("cache_dir") or "logs/research_lab/data_cache")
    allow_yfinance = bool(data_cfg.get("ALLOW_YFINANCE_FALLBACK", False))

    frames: dict[tuple[str, str], tuple[pd.DataFrame, DataProvenance]] = {}
    for timeframe in timeframes:
        loaded = load_ohlcv_multi(
            symbols,
            timeframe,
            cache_dir=cache_dir,
            limit=_limit_for_lookback(timeframe, config.lookback_days),
            force_refresh=config.force_refresh,
            allow_yfinance=allow_yfinance,
            min_bars=config.min_bars,
        )
        for symbol, (df, prov) in loaded.items():
            if df is None or df.empty:
                continue
            recent = filter_recent_window(df, config.lookback_days)
            if len(recent) >= config.min_bars:
                frames[(symbol, timeframe)] = (recent, prov)
    return frames


def _directional_series(signals: dict, index: pd.Index, direction: str) -> tuple[pd.Series, pd.Series]:
    entries = signals.get("entries", pd.Series(False, index=index)).reindex(index, fill_value=False).fillna(False).astype(bool)
    shorts = signals.get("short_entries", pd.Series(False, index=index)).reindex(index, fill_value=False).fillna(False).astype(bool)
    if direction == "long":
        shorts = pd.Series(False, index=index)
    elif direction == "short":
        entries = pd.Series(False, index=index)
    return entries, shorts


def _future_window_extreme(series: pd.Series, bars: int, mode: str) -> pd.Series:
    shifted = series.shift(-1).iloc[::-1]
    rolled = shifted.rolling(int(bars), min_periods=1)
    out = rolled.max() if mode == "max" else rolled.min()
    return out.iloc[::-1]


def _entry_return_stats(
    df: pd.DataFrame,
    long_entries: pd.Series,
    short_entries: pd.Series,
    forward_bars: int,
) -> dict[str, Any]:
    close = df["close"].astype(float)
    fwd_close = close.shift(-int(forward_bars))
    raw_forward = (fwd_close / close) - 1.0
    long_returns = raw_forward.where(long_entries).dropna()
    short_returns = (-raw_forward).where(short_entries).dropna()
    returns = pd.concat([long_returns, short_returns]).dropna()

    future_high = _future_window_extreme(df["high"].astype(float), forward_bars, "max")
    future_low = _future_window_extreme(df["low"].astype(float), forward_bars, "min")
    long_mfe = ((future_high / close) - 1.0).where(long_entries).dropna()
    long_mae = ((future_low / close) - 1.0).where(long_entries).dropna()
    short_mfe = ((close / future_low) - 1.0).where(short_entries).dropna()
    short_mae = ((close / future_high) - 1.0).where(short_entries).dropna()

    candle_vol = ((df["high"].astype(float) - df["low"].astype(float)) / close).rolling(14, min_periods=3).mean()
    quality_long = (raw_forward.abs() / candle_vol.replace(0, np.nan)).where(long_entries)
    quality_short = (raw_forward.abs() / candle_vol.replace(0, np.nan)).where(short_entries)
    quality = pd.concat([quality_long.dropna(), quality_short.dropna()]).replace([np.inf, -np.inf], np.nan).dropna()

    return {
        "returns": returns,
        "quality": quality.clip(lower=0.0, upper=20.0),
        "long_entries": int(long_entries.sum()),
        "short_entries": int(short_entries.sum()),
        "mfe": _finite(pd.concat([long_mfe, short_mfe]).mean(), float("nan")),
        "mae": _finite(pd.concat([long_mae, short_mae]).mean(), float("nan")),
    }


def _warning_for(entry_count: int, selected_count: int, bar_count: int) -> str:
    warnings: list[str] = []
    if bar_count < 500:
        warnings.append("low_candle_sample")
    if entry_count < 20:
        warnings.append("low_entry_sample")
    if selected_count < 10:
        warnings.append("low_gate_sample")
    return ";".join(warnings)


def _screen_score(avg_return: float, win_rate: float, selected_count: int, delta: float) -> float:
    sample_score = min(max(selected_count, 0), 50) / 50.0
    return round((_finite(avg_return) * 100.0) + _finite(win_rate) + sample_score - max(0.0, delta) * 0.05, 6)


def _candidate_key(engine: str, symbol: str, timeframe: str, spec: StrategySpec) -> str:
    return "|".join([engine, symbol, timeframe, spec.family, spec.name, _params_str(spec.params), spec.direction])


def _screen_candidate(
    config: FastGateConfig,
    run_id: str,
    engine: str,
    symbol: str,
    timeframe: str,
    pair_group: str,
    base_threshold: float,
    spec: StrategySpec,
    df: pd.DataFrame,
    prov: DataProvenance,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    signals = run_strategy(df, spec)
    if not signals:
        return [], {"symbol": symbol, "timeframe": timeframe, "engine": engine, "strategy_name": spec.name, "skip": "signal_error"}

    long_entries, short_entries = _directional_series(signals, df.index, spec.direction)
    stats = _entry_return_stats(df, long_entries, short_entries, config.forward_bars)
    returns: pd.Series = stats["returns"]
    quality: pd.Series = stats["quality"]
    entry_count = int(stats["long_entries"] + stats["short_entries"])
    label = engine_research_label(engine)
    sweep = config.engine_a_gate_sweep if str(engine).upper() == "A" else config.engine_b_gate_sweep
    rows: list[dict[str, Any]] = []
    key = _candidate_key(engine, symbol, timeframe, spec)
    for gate in gate_sweep_values(engine, base_threshold, sweep):
        if entry_count and not quality.empty:
            selected_quality = quality[quality >= gate]
            selected_idx = selected_quality.index
            selected_returns = returns.loc[returns.index.intersection(selected_idx)]
        else:
            selected_returns = pd.Series(dtype=float)
        selected_count = int(len(selected_returns))
        avg_return = _finite(selected_returns.mean(), 0.0)
        win_rate = _finite((selected_returns > 0).mean(), 0.0)
        delta = gate - _finite(base_threshold, gate)
        rows.append(
            {
                "run_id": run_id,
                "engine": str(engine).upper(),
                "research_label": label,
                "symbol": symbol,
                "market_group": config.market_group,
                "pair_group": pair_group,
                "timeframe": timeframe,
                "style": config.style,
                "family": spec.family,
                "strategy_name": spec.name,
                "params_str": _params_str(spec.params),
                "direction": spec.direction,
                "base_threshold": _finite(base_threshold, float("nan")),
                "gate": gate,
                "threshold_delta": round(delta, 10),
                "entry_count": entry_count,
                "selected_entry_count": selected_count,
                "long_entries": int(stats["long_entries"]),
                "short_entries": int(stats["short_entries"]),
                "direction_split": f"L{int(stats['long_entries'])}/S{int(stats['short_entries'])}",
                "gate_pass_rate": round(selected_count / entry_count, 6) if entry_count else 0.0,
                "forward_bars": config.forward_bars,
                "avg_forward_return": avg_return,
                "win_rate_forward": win_rate,
                "avg_mfe": stats["mfe"],
                "avg_mae": stats["mae"],
                "screen_score": _screen_score(avg_return, win_rate, selected_count, delta),
                "reliability_warning": _warning_for(entry_count, selected_count, len(df)),
                "data_source": prov.data_source,
                "cache_used": bool(prov.cached),
                "fidelity_note": (
                    "Engine A threshold resolver plus Research Lab factor screen; not an automatic gate change"
                    if str(engine).upper() == "A"
                    else "Engine B proxy screen only; not live-parity proof"
                ),
                "_candidate_key": key,
            }
        )
    return rows, None


def _select_top(screen_df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if screen_df.empty:
        return screen_df.copy()
    sorted_df = screen_df.sort_values(["screen_score", "selected_entry_count"], ascending=[False, False])
    return sorted_df.groupby(["engine"], group_keys=False).head(int(top_n)).copy()


def _stage2_confirm(
    config: FastGateConfig,
    run_id: str,
    top_screen: pd.DataFrame,
    frames: dict[tuple[str, str], tuple[pd.DataFrame, DataProvenance]],
    specs_by_key: dict[str, StrategySpec],
    research_cfg: dict,
) -> pd.DataFrame:
    if top_screen.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    fees_cfg = research_cfg.get("fees") or {}
    slippage = float((research_cfg.get("slippage") or {}).get("default", 0.0002))
    is_pct = float(research_cfg.get("is_pct", 0.70))
    min_trades = int(research_cfg.get("min_trades", 20))
    backtest_exit_config = research_cfg.get("backtest_exit")

    seen: set[str] = set()
    for row in top_screen.to_dict(orient="records"):
        key = row.get("_candidate_key", "")
        if not key or key in seen:
            continue
        seen.add(key)
        frame_key = (row["symbol"], row["timeframe"])
        if frame_key not in frames or key not in specs_by_key:
            continue
        df, prov = frames[frame_key]
        spec = specs_by_key[key]
        signals = run_strategy(df, spec)
        if not signals:
            continue
        metrics = evaluate_strategy(
            df=df,
            signals=signals,
            spec=spec,
            run_id=run_id,
            symbol=row["symbol"],
            asset_class=asset_class_for(row["symbol"]),
            timeframe=row["timeframe"],
            fees=_fee_for(row["symbol"], fees_cfg),
            slippage=slippage,
            is_pct=is_pct,
            min_trades=min_trades,
            freq=vbt_freq(row["timeframe"]),
            data_source=prov.data_source,
            backtest_exit_config=backtest_exit_config,
        )
        metric_row = metrics.to_dict() if isinstance(metrics, StrategyMetrics) else dict(metrics)
        rows.append({**{k: v for k, v in row.items() if not k.startswith("_")}, **{f"stage2_{k}": v for k, v in metric_row.items()}})
    return pd.DataFrame(rows)


def _indicator_attribution(gate_df: pd.DataFrame) -> pd.DataFrame:
    if gate_df.empty:
        return pd.DataFrame(columns=["engine", "family", "strategy_name", "mean_score", "best_score", "tested_rows", "rejected_rows", "verdict"])
    grouped = gate_df.groupby(["engine", "family", "strategy_name"], dropna=False)
    out = grouped.agg(
        mean_score=("screen_score", "mean"),
        best_score=("screen_score", "max"),
        tested_rows=("screen_score", "size"),
        median_entries=("selected_entry_count", "median"),
    ).reset_index()
    out["rejected_rows"] = grouped.apply(
        lambda frame: int(((frame["selected_entry_count"] < 10) | (frame["screen_score"] <= 0)).sum()),
        include_groups=False,
    ).values
    out["verdict"] = np.where(out["best_score"] > 0, "helped", "hurt_or_rejected")
    return out.sort_values(["best_score", "mean_score"], ascending=[False, False])


def _pair_summary(gate_df: pd.DataFrame) -> pd.DataFrame:
    if gate_df.empty:
        return pd.DataFrame(columns=["engine", "symbol", "pair_group", "best_gate", "best_score", "best_strategy", "warnings"])
    idx = gate_df.groupby(["engine", "symbol", "pair_group"], dropna=False)["screen_score"].idxmax()
    best = gate_df.loc[idx].copy()
    return best[
        [
            "engine",
            "symbol",
            "pair_group",
            "timeframe",
            "gate",
            "screen_score",
            "strategy_name",
            "research_label",
            "reliability_warning",
        ]
    ].rename(
        columns={
            "gate": "best_gate",
            "screen_score": "best_score",
            "strategy_name": "best_strategy",
            "reliability_warning": "warnings",
        }
    ).sort_values(["engine", "best_score"], ascending=[True, False])


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_sanitize(payload), indent=2, sort_keys=True), encoding="utf-8")


def _html_table(df: pd.DataFrame, columns: list[str], max_rows: int = 50) -> str:
    if df.empty:
        return "<p>No rows.</p>"
    subset = df.loc[:, [c for c in columns if c in df.columns]].head(max_rows)
    header = "".join(f"<th>{html.escape(str(col))}</th>" for col in subset.columns)
    body = []
    for row in subset.to_dict(orient="records"):
        body.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in subset.columns) + "</tr>")
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _write_report(
    path: Path,
    meta: dict[str, Any],
    gate_df: pd.DataFrame,
    attribution_df: pd.DataFrame,
    pair_df: pd.DataFrame,
    top_df: pd.DataFrame,
) -> None:
    warnings = gate_df[gate_df.get("reliability_warning", "") != ""] if not gate_df.empty else pd.DataFrame()
    rejected = attribution_df[attribution_df.get("verdict", "") == "hurt_or_rejected"] if not attribution_df.empty else pd.DataFrame()
    source_config = html.escape(json.dumps(_json_sanitize(meta.get("source_config_values", {})), indent=2, sort_keys=True))
    cache = html.escape(json.dumps(_json_sanitize(meta.get("cache_health", {})), indent=2, sort_keys=True))
    labels = html.escape(json.dumps(_json_sanitize(meta.get("research_labels", {})), indent=2, sort_keys=True))
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Fast Gate Lab {html.escape(str(meta.get("run_id", "")))}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #202124; }}
    h1, h2 {{ margin-bottom: 8px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    pre {{ background: #f6f8fa; border: 1px solid #d0d7de; padding: 12px; overflow-x: auto; }}
    .note {{ color: #57606a; }}
  </style>
</head>
<body>
  <h1>Fast A/B Gate Research Lab</h1>
  <p class="note">Read-only research evidence. No thresholds, live state, audit DB, or broker files are written.</p>

  <h2>Best Gate Range By Pair/Group</h2>
  {_html_table(pair_df, ["engine", "symbol", "pair_group", "timeframe", "best_gate", "best_score", "best_strategy", "research_label", "warnings"])}

  <h2>Top Confirmation Candidates</h2>
  {_html_table(top_df, ["engine", "symbol", "timeframe", "strategy_name", "gate", "screen_score", "stage2_status", "stage2_trade_count", "stage2_net_return", "stage2_oos_return", "stage2_simulation_backend"])}

  <h2>Indicators That Helped Or Hurt</h2>
  {_html_table(attribution_df, ["engine", "family", "strategy_name", "mean_score", "best_score", "tested_rows", "rejected_rows", "verdict"])}

  <h2>Trade/Sample Reliability Warnings</h2>
  {_html_table(warnings, ["engine", "symbol", "timeframe", "strategy_name", "gate", "selected_entry_count", "reliability_warning"])}

  <h2>Rejected Indicators</h2>
  {_html_table(rejected, ["engine", "family", "strategy_name", "best_score", "rejected_rows", "verdict"])}

  <h2>Proxy / Live-Aligned Labels</h2>
  <pre>{labels}</pre>

  <h2>Exact Source Config Values Used</h2>
  <pre>{source_config}</pre>

  <h2>Cache Health</h2>
  <pre>{cache}</pre>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def run_fast_gate_lab(config: FastGateConfig | None = None) -> dict[str, Any]:
    config = config or FastGateConfig()
    run_id = config.run_id or _run_id()
    run_dir = Path(config.output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    research_cfg = _load_research_config(config.config_path)
    data_cfg = research_cfg.get("data") or {}
    cache_health = parquet_cache_health(data_cfg.get("cache_dir") or "logs/research_lab/data_cache")
    frames = load_symbol_frames(config)
    engines = [e.upper() for e in _as_list(config.engines)]
    runtime_resolvers_enabled = os.environ.get("FAST_GATE_LAB_USE_RUNTIME_RESOLVERS") == "1"

    engine_a_sources: dict[str, dict[str, Any]] = {}
    engine_b_threshold = _current_engine_b_threshold(config.style)
    source_config_values: dict[str, Any] = {
        "engine_a": engine_a_sources,
        "engine_b": {
            "style": config.style,
            "style_key": _engine_b_style_key(config.style),
            "threshold": engine_b_threshold,
            "gate_sweep": list(config.engine_b_gate_sweep),
            "resolver": (
                "market_structure.engine_b_min_score_threshold"
                if runtime_resolvers_enabled
                else "config.yaml NAKED_ENGINE.style_profiles fallback"
            ),
        },
    }

    gate_rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    specs_by_key: dict[str, StrategySpec] = {}

    for (symbol, timeframe), (df, prov) in frames.items():
        engine_a_threshold, pair_group = _current_engine_a_threshold(symbol, config.market_group)
        engine_a_sources[symbol] = {
            "threshold": engine_a_threshold,
            "pair_group": pair_group,
            "gate_sweep": list(config.engine_a_gate_sweep),
            "resolver": (
                "scoring.get_score_threshold"
                if runtime_resolvers_enabled
                else "config.yaml ENGINE_A_* fallback"
            ),
            "group_resolver": (
                "scoring.get_pair_score_group"
                if runtime_resolvers_enabled
                else "source-equivalent local group resolver"
            ),
        }
        for engine in engines:
            base_threshold = engine_a_threshold if engine == "A" else engine_b_threshold
            specs = _strategy_specs_for_engine(config, engine, timeframe)
            for spec in specs:
                key = _candidate_key(engine, symbol, timeframe, spec)
                specs_by_key[key] = spec
                rows, detail = _screen_candidate(
                    config,
                    run_id,
                    engine,
                    symbol,
                    timeframe,
                    pair_group,
                    base_threshold,
                    spec,
                    df,
                    prov,
                )
                gate_rows.extend(rows)
                if detail:
                    details.append(detail)

    gate_df = pd.DataFrame(gate_rows)
    top_screen = _select_top(gate_df, config.top_n)
    top_df = _stage2_confirm(config, run_id, top_screen, frames, specs_by_key, research_cfg)
    attribution_df = _indicator_attribution(gate_df)
    pair_df = _pair_summary(gate_df)

    gate_csv = run_dir / OUTPUT_FILES["gate_matrix"]
    attr_csv = run_dir / OUTPUT_FILES["indicator_attribution"]
    pair_csv = run_dir / OUTPUT_FILES["pair_summary"]
    top_csv = run_dir / OUTPUT_FILES["top_candidates"]
    details_json = run_dir / OUTPUT_FILES["screening_details"]
    meta_json = run_dir / OUTPUT_FILES["run_meta"]
    report_html = run_dir / OUTPUT_FILES["report"]

    public_gate_df = gate_df.drop(columns=[c for c in gate_df.columns if c.startswith("_")], errors="ignore")
    public_top_df = top_df.drop(columns=[c for c in top_df.columns if c.startswith("_")], errors="ignore")
    public_gate_df.to_csv(gate_csv, index=False)
    attribution_df.to_csv(attr_csv, index=False)
    pair_df.to_csv(pair_csv, index=False)
    public_top_df.to_csv(top_csv, index=False)

    meta = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "market_group": config.market_group,
        "style": config.style,
        "timeframes": _as_list(config.timeframes),
        "engines": engines,
        "lookback_days": config.lookback_days,
        "symbols": sorted({symbol for symbol, _ in frames.keys()}),
        "frame_count": len(frames),
        "screened_rows": len(public_gate_df),
        "top_candidate_rows": len(public_top_df),
        "cache_health": cache_health,
        "research_labels": {
            "A": "live_aligned_engine_a",
            "B": "proxy_engine_b",
        },
        "source_config_values": source_config_values,
        "forbidden_writes": ["config.yaml", "thresholds", "live_state", "audit.db", "broker/execution files"],
    }
    _write_json(meta_json, meta)
    _write_json(
        details_json,
        {
            "run_id": run_id,
            "cache_health": cache_health,
            "data_provenance": {
                f"{symbol}|{timeframe}": prov.to_dict()
                for (symbol, timeframe), (_, prov) in frames.items()
            },
            "screening_errors": details,
        },
    )
    _write_report(report_html, meta, public_gate_df, attribution_df, pair_df, public_top_df)

    files = {
        "run_meta": meta_json,
        "gate_matrix": gate_csv,
        "indicator_attribution": attr_csv,
        "pair_summary": pair_csv,
        "top_candidates": top_csv,
        "screening_details": details_json,
        "report": report_html,
    }
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "files": files,
        "cache_health": cache_health,
        "screened_rows": len(public_gate_df),
        "top_candidate_rows": len(public_top_df),
    }
