"""
Athena Research Lab — Metrics Engine
Runs VectorBT (or a pure-pandas fallback) and extracts standardised metrics.

Safety: no live imports, no production config writes.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ─── Result schema ────────────────────────────────────────────────────────────

@dataclass
class StrategyMetrics:
    run_id: str
    symbol: str
    asset_class: str
    timeframe: str
    family: str
    strategy_name: str
    params_str: str
    direction: str
    session: str = "all"
    # Core trade stats
    trade_count: int = 0
    win_rate: float = float("nan")
    profit_factor: float = float("nan")
    avg_return: float = float("nan")
    expectancy: float = float("nan")
    max_drawdown: float = float("nan")
    sharpe: float = float("nan")
    sqn: float = float("nan")
    # Exposure / duration
    exposure_pct: float = float("nan")
    avg_duration_bars: float = float("nan")
    # Returns
    gross_return: float = float("nan")
    net_return: float = float("nan")
    is_return: float = float("nan")
    oos_return: float = float("nan")
    # Robustness
    robustness_score: float = float("nan")
    param_sensitivity: float = float("nan")
    status: str = "NEEDS_MORE_DATA"   # STRONG_CANDIDATE | WEAK_CANDIDATE | REJECT | NEEDS_MORE_DATA
    skip_reason: str = ""
    data_source: str = ""   # "binance_rest"|"mt5"|"eodhd"|"yfinance_fallback"|"synthetic_test"|"DATA_UNAVAILABLE"

    def to_dict(self) -> dict:
        return asdict(self)


# ─── VectorBT portfolio runner ────────────────────────────────────────────────

def run_portfolio(
    close: pd.Series,
    entries: pd.Series,
    exits: pd.Series,
    short_entries: Optional[pd.Series] = None,
    short_exits: Optional[pd.Series] = None,
    fees: float = 0.001,
    slippage: float = 0.0002,
    freq: str = "1h",
    init_cash: float = 10_000.0,
) -> dict:
    """
    Run VectorBT portfolio simulation.  Falls back to manual pandas backtester
    if VectorBT is not installed or raises.

    Returns a unified stats dict.
    """
    # Try VectorBT
    try:
        return _vbt_portfolio(close, entries, exits, short_entries, short_exits,
                              fees, slippage, freq, init_cash)
    except ImportError:
        log.debug("[metrics] vectorbt not installed — using pandas fallback")
    except Exception as e:
        log.debug("[metrics] vectorbt failed (%s) — using pandas fallback", e)

    # Pandas fallback
    return _pandas_portfolio(close, entries, exits, short_entries, short_exits,
                             fees, slippage)


def _vbt_portfolio(close, entries, exits, short_entries, short_exits,
                   fees, slippage, freq, init_cash) -> dict:
    import vectorbt as vbt

    pf_kwargs = dict(
        close=close,
        entries=entries.fillna(False),
        exits=exits.fillna(False),
        fees=fees,
        slippage=slippage,
        freq=freq,
        init_cash=init_cash,
        log=False,
    )
    if short_entries is not None:
        pf_kwargs["short_entries"] = short_entries.fillna(False)
    if short_exits is not None:
        pf_kwargs["short_exits"] = short_exits.fillna(False)

    pf = vbt.Portfolio.from_signals(**pf_kwargs)

    try:
        stats = pf.stats(silence_warnings=True)
    except Exception:
        stats = {}

    def _get(key, default=float("nan")):
        if isinstance(stats, dict):
            return stats.get(key, default)
        try:
            return stats.get(key, default)
        except Exception:
            return default

    # Try to get trade records for granular stats
    try:
        trades_rec = pf.trades.records
        trade_returns = trades_rec["return"].values if "return" in trades_rec.columns else np.array([])
        trade_durations = (trades_rec["exit_idx"] - trades_rec["entry_idx"]).values \
            if "exit_idx" in trades_rec.columns else np.array([])
    except Exception:
        trade_returns = np.array([])
        trade_durations = np.array([])

    n = len(trade_returns)
    return _build_stats(
        trade_returns=trade_returns,
        trade_durations=trade_durations,
        equity=pf.value(),
        init_cash=init_cash,
        fees=fees,
        stats_dict=stats,
    )


def _pandas_portfolio(close, entries, exits, short_entries, short_exits, fees, slippage) -> dict:
    """Simple bar-by-bar backtester — no VectorBT dependency."""
    position = 0    # 0=flat, 1=long, -1=short
    entry_price = 0.0
    entry_bar = 0
    trade_returns: list[float] = []
    trade_durations: list[int] = []
    equity = [1.0]
    current_eq = 1.0

    n = len(close)
    closes = close.values
    ents = entries.fillna(False).values
    exts = exits.fillna(False).values
    s_ents = short_entries.fillna(False).values if short_entries is not None else np.zeros(n, bool)
    s_exts = short_exits.fillna(False).values if short_exits is not None else np.zeros(n, bool)

    for i in range(1, n):
        price = closes[i]

        if position == 0:
            if ents[i]:
                entry_price = price * (1 + slippage)
                entry_bar = i
                position = 1
            elif s_ents[i]:
                entry_price = price * (1 - slippage)
                entry_bar = i
                position = -1

        elif position == 1:
            should_exit = exts[i] or s_ents[i]
            if should_exit:
                exit_price = price * (1 - slippage)
                ret = (exit_price - entry_price) / entry_price - fees * 2
                trade_returns.append(ret)
                trade_durations.append(i - entry_bar)
                current_eq *= (1 + ret)
                position = 0
                if s_ents[i]:
                    entry_price = exit_price * (1 - slippage)
                    entry_bar = i
                    position = -1

        elif position == -1:
            should_exit = s_exts[i] or ents[i]
            if should_exit:
                exit_price = price * (1 + slippage)
                ret = (entry_price - exit_price) / entry_price - fees * 2
                trade_returns.append(ret)
                trade_durations.append(i - entry_bar)
                current_eq *= (1 + ret)
                position = 0
                if ents[i]:
                    entry_price = exit_price * (1 + slippage)
                    entry_bar = i
                    position = 1

        equity.append(current_eq)

    equity_s = pd.Series(equity, index=close.index[:len(equity)])
    return _build_stats(
        trade_returns=np.array(trade_returns),
        trade_durations=np.array(trade_durations),
        equity=equity_s,
        init_cash=1.0,
        fees=fees,
        stats_dict={},
    )


def _build_stats(trade_returns: np.ndarray, trade_durations: np.ndarray,
                 equity: pd.Series, init_cash: float, fees: float,
                 stats_dict: dict) -> dict:
    n = len(trade_returns)
    if n == 0:
        return {"trade_count": 0}

    wins = trade_returns > 0
    losses = trade_returns <= 0
    win_count = wins.sum()
    loss_count = losses.sum()

    win_rate = win_count / n
    avg_win = trade_returns[wins].mean() if win_count > 0 else 0.0
    avg_loss = abs(trade_returns[losses].mean()) if loss_count > 0 else 0.0
    profit_factor = (win_count * avg_win) / (loss_count * avg_loss) if (loss_count > 0 and avg_loss > 0) else float("inf")
    expectancy = trade_returns.mean()

    # Drawdown from equity curve
    eq = equity.values
    running_max = np.maximum.accumulate(eq)
    dd = (eq - running_max) / running_max
    max_dd = float(dd.min())

    # Sharpe (annualised, assuming daily periods unless equity freq known)
    if len(eq) > 1:
        ret_series = pd.Series(eq).pct_change().dropna()
        if ret_series.std() > 0:
            sharpe = float(ret_series.mean() / ret_series.std() * math.sqrt(252))
        else:
            sharpe = float("nan")
    else:
        sharpe = float("nan")

    # SQN
    if n >= 5:
        sqn = float(math.sqrt(n) * trade_returns.mean() / (trade_returns.std() + 1e-10))
    else:
        sqn = float("nan")

    gross_return = float(eq[-1] / eq[0] - 1) if eq[0] != 0 else float("nan")
    avg_duration = float(trade_durations.mean()) if len(trade_durations) > 0 else float("nan")

    return {
        "trade_count": int(n),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "avg_return": float(expectancy),
        "expectancy": float(expectancy),
        "max_drawdown": float(max_dd),
        "sharpe": sharpe,
        "sqn": sqn,
        "gross_return": gross_return,
        "avg_duration_bars": avg_duration,
        "equity": equity,
        "trade_returns": trade_returns,
    }


# ─── IS / OOS split ──────────────────────────────────────────────────────────

def split_is_oos(df: pd.DataFrame, is_pct: float = 0.70) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = len(df)
    split_idx = int(n * is_pct)
    return df.iloc[:split_idx], df.iloc[split_idx:]


# ─── Full strategy evaluation ─────────────────────────────────────────────────

def evaluate_strategy(
    df: pd.DataFrame,
    signals: dict,
    spec,
    run_id: str,
    symbol: str,
    asset_class: str,
    timeframe: str,
    fees: float = 0.001,
    slippage: float = 0.0002,
    is_pct: float = 0.70,
    min_trades: int = 20,
    freq: str = "1h",
    data_source: str = "",
) -> StrategyMetrics:
    """Compute full StrategyMetrics for one strategy run."""
    from athena_research.data_loader import vbt_freq

    params = getattr(spec, "params", {})
    params_str = "|".join(f"{k}={v}" for k, v in sorted(params.items()))
    direction = getattr(spec, "direction", "both")

    skip = signals.get("meta", {}).get("skip")
    if skip:
        return StrategyMetrics(
            run_id=run_id, symbol=symbol, asset_class=asset_class,
            timeframe=timeframe, family=spec.family, strategy_name=spec.name,
            params_str=params_str, direction=direction,
            status="REJECT", skip_reason=skip, data_source=data_source,
        )

    close = df["close"]
    entries = signals.get("entries", pd.Series(False, index=df.index))
    exits = signals.get("exits", pd.Series(False, index=df.index))
    short_entries = signals.get("short_entries")
    short_exits = signals.get("short_exits")

    # Direction filter
    if direction == "long":
        short_entries = None
        short_exits = None
    elif direction == "short":
        entries = pd.Series(False, index=df.index)
        exits = pd.Series(False, index=df.index)

    # Full run
    all_stats = run_portfolio(close, entries, exits, short_entries, short_exits,
                              fees=fees, slippage=slippage, freq=freq)

    # IS / OOS
    df_is, df_oos = split_is_oos(df, is_pct)
    is_signals = _slice_signals(signals, df_is.index, direction)
    oos_signals = _slice_signals(signals, df_oos.index, direction)

    is_stats = run_portfolio(
        df_is["close"], is_signals["entries"], is_signals["exits"],
        is_signals.get("short_entries"), is_signals.get("short_exits"),
        fees=fees, slippage=slippage, freq=freq,
    )
    oos_stats = run_portfolio(
        df_oos["close"], oos_signals["entries"], oos_signals["exits"],
        oos_signals.get("short_entries"), oos_signals.get("short_exits"),
        fees=fees, slippage=slippage, freq=freq,
    )

    n = all_stats.get("trade_count", 0)
    if n < min_trades:
        return StrategyMetrics(
            run_id=run_id, symbol=symbol, asset_class=asset_class,
            timeframe=timeframe, family=spec.family, strategy_name=spec.name,
            params_str=params_str, direction=direction,
            trade_count=n, status="NEEDS_MORE_DATA",
            skip_reason=f"only {n} trades < min {min_trades}",
            data_source=data_source,
        )

    # Gross vs net: gross_return already excludes fees in our backtester
    # For display purposes, gross = net (we include fees in the simulation)
    gross_r = all_stats.get("gross_return", float("nan"))
    net_r = gross_r  # fees already baked in

    is_r = is_stats.get("gross_return", float("nan"))
    oos_r = oos_stats.get("gross_return", float("nan"))

    robustness = _compute_robustness(n, is_r, oos_r, net_r)
    status = _classify_status(n, net_r, robustness, is_r, oos_r, min_trades)

    # Exposure: fraction of bars with open position
    equity = all_stats.get("equity")
    exposure_pct = float("nan")
    if equity is not None and len(equity) > 0:
        # rough proxy: fraction of consecutive equity changes ≠ 0
        eq_arr = equity.values if hasattr(equity, "values") else np.array(equity)
        exposure_pct = float((np.diff(eq_arr) != 0).mean()) if len(eq_arr) > 1 else float("nan")

    return StrategyMetrics(
        run_id=run_id,
        symbol=symbol,
        asset_class=asset_class,
        timeframe=timeframe,
        family=spec.family,
        strategy_name=spec.name,
        params_str=params_str,
        direction=direction,
        trade_count=n,
        win_rate=all_stats.get("win_rate", float("nan")),
        profit_factor=all_stats.get("profit_factor", float("nan")),
        avg_return=all_stats.get("avg_return", float("nan")),
        expectancy=all_stats.get("expectancy", float("nan")),
        max_drawdown=all_stats.get("max_drawdown", float("nan")),
        sharpe=all_stats.get("sharpe", float("nan")),
        sqn=all_stats.get("sqn", float("nan")),
        exposure_pct=exposure_pct,
        avg_duration_bars=all_stats.get("avg_duration_bars", float("nan")),
        gross_return=gross_r,
        net_return=net_r,
        is_return=is_r,
        oos_return=oos_r,
        robustness_score=robustness,
        status=status,
        data_source=data_source,
    )


def _dedup_series(s: pd.Series) -> pd.Series:
    if s.index.has_duplicates:
        return s[~s.index.duplicated(keep="last")]
    return s


def _dedup_idx(idx):
    if idx.has_duplicates:
        return idx[~idx.duplicated(keep="last")]
    return idx


def _slice_signals(signals: dict, idx, direction: str) -> dict:
    idx = _dedup_idx(idx)
    entries = _dedup_series(signals.get("entries", pd.Series(False))).reindex(idx, fill_value=False)
    exits = _dedup_series(signals.get("exits", pd.Series(False))).reindex(idx, fill_value=False)
    se = signals.get("short_entries")
    sx = signals.get("short_exits")
    se = _dedup_series(se).reindex(idx, fill_value=False) if se is not None else None
    sx = _dedup_series(sx).reindex(idx, fill_value=False) if sx is not None else None
    if direction == "long":
        se = None; sx = None
    elif direction == "short":
        entries = pd.Series(False, index=idx)
        exits = pd.Series(False, index=idx)
    return {"entries": entries, "exits": exits, "short_entries": se, "short_exits": sx}


def _compute_robustness(n: int, is_r: float, oos_r: float, net_r: float) -> float:
    # Sample adequacy (0–1)
    sample_w = min(1.0, n / 50) * 0.30

    # IS/OOS consistency (0–1)
    if not (math.isfinite(is_r) and math.isfinite(oos_r)):
        consist_w = 0.0
    else:
        diff = abs(is_r - oos_r)
        max_ref = max(abs(is_r), 0.01)
        consist_w = max(0.0, 1.0 - diff / max_ref) * 0.30

    # Fee survival (0 or 1)
    fee_w = (0.20 if (math.isfinite(net_r) and net_r > 0) else 0.10)

    return sample_w + consist_w + fee_w


def _classify_status(n: int, net_r: float, robustness: float, is_r: float, oos_r: float,
                     min_trades: int) -> str:
    if n < min_trades:
        return "NEEDS_MORE_DATA"
    if not math.isfinite(net_r) or net_r <= 0:
        return "REJECT"
    if robustness >= 0.60 and math.isfinite(oos_r) and oos_r > 0:
        return "STRONG_CANDIDATE"
    if robustness >= 0.35 or net_r > 0:
        return "WEAK_CANDIDATE"
    return "REJECT"
