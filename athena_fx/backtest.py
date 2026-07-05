"""Phase 10 — weekly factor backtest engine for the athena_fx lane.

Research/backtest only. Uses dependency injection for decide_fn; never imports
Phase 4-9 modules directly (caller wires callables).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from config import CONFIG

from athena_fx.models import (
    ACTION_BUY,
    ACTION_FLATTEN,
    ACTION_NO_TRADE,
    ACTION_REDUCE,
    ACTION_SELL,
    DIAG_MISSING_CARRY,
    DIAG_MISSING_COT,
    DIAG_MISSING_FRED,
    DIAG_MISSING_REER,
    DIAG_MISSING_SWAP,
    FAMILY_CARRY,
    FAMILY_MOMENTUM,
    FAMILY_VALUE,
    pair_legs,
)
from athena_fx import store

logger = logging.getLogger("athena_fx.backtest")

STRICT_FACTOR_CODES = frozenset(
    {
        DIAG_MISSING_CARRY,
        DIAG_MISSING_COT,
        DIAG_MISSING_FRED,
        DIAG_MISSING_REER,
        DIAG_MISSING_SWAP,
    }
)

PRIMARY_FAMILIES = (FAMILY_CARRY, FAMILY_MOMENTUM, FAMILY_VALUE)


def _bars_window(
    bars: list[dict[str, Any]], as_of: str, *, lookback_days: int = 60
) -> list[dict[str, Any]]:
    """Daily bars in [as_of - lookback, as_of] for cost/ATR gates."""
    try:
        start = (date.fromisoformat(as_of) - timedelta(days=lookback_days)).isoformat()
    except ValueError:
        return bars
    return [b for b in bars if start <= str(b.get("date", "")) <= as_of]


def _call_decide_fn(
    decide_fn: Callable[..., dict[str, Any]],
    symbol: str,
    as_of: str,
    has_pos: bool,
    bars_window: list[dict[str, Any]],
) -> dict[str, Any]:
    """Invoke decide_fn; pass preloaded bars when supported."""
    try:
        return decide_fn(symbol, as_of, has_pos, bars=bars_window)
    except TypeError:
        return decide_fn(symbol, as_of, has_pos)


@dataclass
class FxBacktestRequest:
    start: str
    end: str
    symbols: list[str]
    strict_factor_data: bool = True
    cost_model_enabled: bool = True
    slippage_pips: float = field(
        default_factory=lambda: float(CONFIG.get("FX_FACTOR_SLIPPAGE_PIPS", 0.3) or 0.3)
    )
    spread_pips_by_symbol: dict[str, float] = field(default_factory=dict)
    target_annual_vol: float = field(
        default_factory=lambda: float(CONFIG.get("FX_FACTOR_TARGET_VOL", 0.10) or 0.10)
    )
    pair_risk_cap: float = field(
        default_factory=lambda: float(CONFIG.get("FX_FACTOR_PAIR_RISK_CAP", 0.02) or 0.02)
    )
    currency_risk_cap: float = field(
        default_factory=lambda: float(CONFIG.get("FX_FACTOR_CCY_RISK_CAP", 0.04) or 0.04)
    )
    total_risk_cap: float = field(
        default_factory=lambda: float(CONFIG.get("FX_FACTOR_TOTAL_RISK_CAP", 0.10) or 0.10)
    )
    decision_weekday: int = 5
    execution_weekday: int = 0


def pip_size(symbol: str) -> float:
    """Return pip size: 0.01 for JPY-quote pairs, else 0.0001."""
    _, quote = pair_legs(symbol)
    return 0.01 if quote == "JPY" else 0.0001


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _fridays_in_range(start: str, end: str) -> list[str]:
    start_d, end_d = _parse_date(start), _parse_date(end)
    d = start_d
    while d.weekday() != 4 and d <= end_d:
        d += timedelta(days=1)
    out: list[str] = []
    while d <= end_d:
        out.append(d.isoformat())
        d += timedelta(days=7)
    return out


def _next_weekday_on_or_after(d: date, weekday: int) -> date:
    cur = d
    while cur.weekday() != weekday:
        cur += timedelta(days=1)
    return cur


def _next_trading_day(
    from_date: str,
    weekday: int,
    trading_dates: set[str],
) -> Optional[str]:
    """Next execution session: preferred weekday, else next trading day if holiday."""
    d = _parse_date(from_date) + timedelta(days=1)
    target = d
    while target.weekday() != weekday:
        target += timedelta(days=1)
    scan = target
    for _ in range(10):
        ds = scan.isoformat()
        if ds in trading_dates:
            return ds
        scan += timedelta(days=1)
    return None


def _index_bars(bars: list[dict]) -> dict[str, dict]:
    return {b["date"]: b for b in bars}


def _sorted_trading_dates(bars_by_symbol: dict[str, list[dict]]) -> list[str]:
    dates: set[str] = set()
    for bars in bars_by_symbol.values():
        for b in bars:
            dates.add(b["date"])
    return sorted(dates)


def realized_vol_annualized(
    bars_index: dict[str, dict],
    as_of: str,
    window: int = 63,
) -> float:
    """Annualized realized vol from daily log returns up to as_of (inclusive)."""
    dates = sorted(d for d in bars_index if d <= as_of)
    if len(dates) < 2:
        return float(CONFIG.get("FX_FACTOR_TARGET_VOL", 0.10) or 0.10)
    closes: list[float] = []
    for d in dates[-(window + 1) :]:
        c = bars_index[d].get("close")
        if c is not None and c > 0:
            closes.append(float(c))
    if len(closes) < 2:
        return float(CONFIG.get("FX_FACTOR_TARGET_VOL", 0.10) or 0.10)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if not rets:
        return float(CONFIG.get("FX_FACTOR_TARGET_VOL", 0.10) or 0.10)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    daily_std = math.sqrt(var)
    if daily_std <= 0:
        return float(CONFIG.get("FX_FACTOR_TARGET_VOL", 0.10) or 0.10)
    return daily_std * math.sqrt(252)


def vol_scaled_weight(
    realized_vol: float,
    target_vol: float,
    pair_risk_cap: float,
    max_leverage: Optional[float] = None,
) -> float:
    if realized_vol <= 0:
        return 0.0
    cap = max_leverage if max_leverage is not None else float(
        CONFIG.get("FX_FACTOR_MAX_VOL_LEVERAGE", 2.0) or 2.0
    )
    scale = min(target_vol / realized_vol, cap)
    return pair_risk_cap * scale


def currency_abs_exposure(weights: dict[str, tuple[float, str]]) -> dict[str, float]:
    """Sum of absolute weights touching each currency."""
    exp: dict[str, float] = defaultdict(float)
    for sym, (w, _direction) in weights.items():
        base, quote = pair_legs(sym)
        aw = abs(w)
        exp[base] += aw
        exp[quote] += aw
    return dict(exp)


def apply_exposure_caps(
    weights: dict[str, tuple[float, str]],
    pair_risk_cap: float,
    currency_risk_cap: float,
    total_risk_cap: float,
) -> dict[str, tuple[float, str]]:
    if not weights:
        return {}
    clipped: dict[str, tuple[float, str]] = {}
    for sym, (w, direction) in weights.items():
        capped = min(abs(w), pair_risk_cap)
        clipped[sym] = (capped if w >= 0 else -capped, direction)

    def _scale(factor: float) -> dict[str, tuple[float, str]]:
        return {s: (w * factor, d) for s, (w, d) in clipped.items()}

    scale = 1.0
    for _ in range(8):
        ccy = currency_abs_exposure(clipped)
        for exp in ccy.values():
            if exp > currency_risk_cap and exp > 0:
                scale = min(scale, currency_risk_cap / exp)
        total = sum(abs(w) for w, _ in clipped.values())
        if total > total_risk_cap and total > 0:
            scale = min(scale, total_risk_cap / total)
        if scale < 1.0:
            clipped = _scale(scale)
            scale = 1.0
        else:
            break
    return clipped


def _diagnostic_codes(diagnostics: list[Any]) -> set[str]:
    codes: set[str] = set()
    for d in diagnostics or []:
        if isinstance(d, dict):
            code = d.get("code")
        else:
            code = getattr(d, "code", None)
        if code:
            codes.add(str(code))
    return codes


def _fill_price(
    open_px: float,
    direction: str,
    spread_pips: float,
    slippage_pips: float,
    symbol: str,
    *,
    is_entry: bool,
) -> float:
    """Direction-aware fill: buy pays up, sell receives less."""
    pip = pip_size(symbol)
    half_cost = (spread_pips / 2.0 + slippage_pips) * pip
    if direction == "LONG":
        return open_px + half_cost if is_entry else open_px - half_cost
    return open_px - half_cost if is_entry else open_px + half_cost


def _cost_fraction(
    spread_pips: float,
    slippage_pips: float,
    price: float,
    symbol: str,
) -> float:
    if price <= 0:
        return 0.0
    pip = pip_size(symbol)
    return (spread_pips + slippage_pips) * pip / price


def _swap_daily_units(weekday: int, rollover3days: int) -> float:
    return 3.0 if weekday == rollover3days else 1.0


def _max_drawdown(equity_curve: list[dict[str, Any]]) -> float:
    peak = 0.0
    max_dd = 0.0
    for pt in equity_curve:
        eq = float(pt["equity"])
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _sharpe(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    mean = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(var)
    if std <= 0:
        return 0.0
    return mean / std * math.sqrt(252)


def _pair_sanity_flags(
    trades: list[dict[str, Any]],
    factor_family_pnl: dict[str, float],
    summary: dict[str, Any],
    degraded_swap_symbols: set[str],
) -> list[str]:
    flags: list[str] = []
    family_trade_counts: dict[str, int] = defaultdict(int)
    for t in trades:
        fams = t.get("aligned_families") or []
        for f in fams:
            family_trade_counts[f] += 1

    for family, pnl in factor_family_pnl.items():
        if pnl <= 0:
            continue
        pair_pnl: dict[str, float] = defaultdict(float)
        for t in trades:
            fams = t.get("aligned_families") or []
            if family not in fams:
                continue
            share = float(t.get("spot_pnl", 0) + t.get("swap_pnl", 0) - t.get("cost_paid", 0))
            share /= max(len(fams), 1)
            pair_pnl[t["symbol"]] += share
        if pnl > 0:
            for sym, sp in pair_pnl.items():
                if sp / pnl > 0.40:
                    flags.append(f"family_{family}_pnl_concentration_{sym}")
        total_tr = family_trade_counts.get(family, 0)
        if total_tr > 0:
            pair_counts: dict[str, int] = defaultdict(int)
            for t in trades:
                fams = t.get("aligned_families") or []
                if family in fams:
                    pair_counts[t["symbol"]] += 1
            for sym, cnt in pair_counts.items():
                if cnt / total_tr > 0.40:
                    flags.append(f"family_{family}_trade_concentration_{sym}")

    gross = abs(summary.get("spot_contribution", 0)) + abs(summary.get("swap_contribution", 0))
    cost_drag = summary.get("cost_drag", 0)
    if gross > 0 and cost_drag > 0.5 * gross:
        flags.append("pathological_slippage")

    for sym in degraded_swap_symbols:
        flags.append(f"unreliable_swap_{sym}")

    total_pnl = sum(
        t.get("spot_pnl", 0) + t.get("swap_pnl", 0) - t.get("cost_paid", 0) for t in trades
    )
    if total_pnl > 0:
        pair_totals: dict[str, float] = defaultdict(float)
        for t in trades:
            pnl = t.get("spot_pnl", 0) + t.get("swap_pnl", 0) - t.get("cost_paid", 0)
            if pnl > 0:
                pair_totals[t["symbol"]] += pnl
        for sym, sp in pair_totals.items():
            if sp / total_pnl > 0.80:
                flags.append(f"sharpe_driver_{sym}")

    return flags


@dataclass
class _OpenPosition:
    symbol: str
    direction: str
    weight: float
    entry_date: str
    entry_price: float
    entry_equity: float
    aligned_families: list[str]
    spot_pnl: float = 0.0
    swap_pnl: float = 0.0
    cost_paid: float = 0.0
    prev_close: Optional[float] = None


def run_fx_factor_backtest(
    request: FxBacktestRequest,
    *,
    bars_by_symbol: dict[str, list[dict]],
    decide_fn: Callable[[str, str, bool], dict[str, Any]],
    swap_returns_by_symbol: Optional[dict[str, dict[str, float]]] = None,
) -> dict[str, Any]:
    """Run a weekly-cadence FX factor backtest; persist and return report."""
    request_dict = asdict(request)
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = hashlib.sha256(
        (json.dumps(request_dict, sort_keys=True) + request.start + request.end).encode()
    ).hexdigest()[:12]

    diagnostics: list[dict[str, Any]] = []
    symbol_week_valid = 0
    symbol_week_total = 0
    degraded_swap_symbols: set[str] = set()

    bar_index = {sym: _index_bars(bars) for sym, bars in bars_by_symbol.items()}
    all_dates = _sorted_trading_dates(bars_by_symbol)
    trading_set = set(all_dates)
    if not all_dates:
        report = _empty_report(run_id, request_dict, "invalid", diagnostics)
        store.save_backtest_run(run_id, created_at, "invalid", request_dict, report)
        return report

    fridays = _fridays_in_range(request.start, request.end)
    friday_set = set(fridays)
    positions: dict[str, _OpenPosition] = {}
    trades: list[dict[str, Any]] = []
    equity = 1.0
    equity_curve: list[dict[str, Any]] = []
    daily_returns: list[float] = []
    turnover = 0.0

    exec_queue: dict[str, dict[str, Any]] = {}

    def _schedule_weekly_decisions(as_of: str) -> None:
        nonlocal symbol_week_valid, symbol_week_total
        week_targets: dict[str, tuple[float, str, dict[str, Any]]] = {}
        raw_weights: dict[str, tuple[float, str]] = {}

        for symbol in request.symbols:
            symbol_week_total += 1
            bars = bar_index.get(symbol, {})
            if as_of not in bars:
                diagnostics.append(
                    {
                        "code": "missing_ohlc",
                        "severity": "warning",
                        "message": f"no bar on as_of {as_of}",
                        "context": {"symbol": symbol, "as_of_date": as_of},
                    }
                )
                continue

            has_pos = symbol in positions
            bars_window = _bars_window(bars_by_symbol.get(symbol, []), as_of)
            decision = _call_decide_fn(decide_fn, symbol, as_of, has_pos, bars_window)
            dec_diag = decision.get("diagnostics") or []
            codes = _diagnostic_codes(dec_diag)

            swap_missing = swap_returns_by_symbol is None or symbol not in (
                swap_returns_by_symbol or {}
            )
            if swap_missing:
                if request.strict_factor_data or request.cost_model_enabled:
                    diagnostics.append(
                        {
                            "code": DIAG_MISSING_SWAP,
                            "severity": "error",
                            "message": "swap returns missing",
                            "context": {"symbol": symbol, "as_of_date": as_of},
                        }
                    )
                    if request.strict_factor_data:
                        for d in dec_diag:
                            diagnostics.append(d if isinstance(d, dict) else d.to_dict())
                        continue
                    degraded_swap_symbols.add(symbol)
                    diagnostics.append(
                        {
                            "code": DIAG_MISSING_SWAP,
                            "severity": "warning",
                            "message": "swap accrual zero (non-strict)",
                            "context": {"symbol": symbol, "as_of_date": as_of},
                        }
                    )
                else:
                    degraded_swap_symbols.add(symbol)

            strict_hit = request.strict_factor_data and codes & STRICT_FACTOR_CODES
            if strict_hit:
                for d in dec_diag:
                    diagnostics.append(d if isinstance(d, dict) else d.to_dict())
                diagnostics.append(
                    {
                        "code": "strict_factor_excluded",
                        "severity": "error",
                        "message": f"strict exclusion: {sorted(codes & STRICT_FACTOR_CODES)}",
                        "context": {"symbol": symbol, "as_of_date": as_of},
                    }
                )
                continue

            if request.cost_model_enabled and symbol not in request.spread_pips_by_symbol:
                diagnostics.append(
                    {
                        "code": "missing_spread",
                        "severity": "error",
                        "message": "spread missing for symbol",
                        "context": {"symbol": symbol, "as_of_date": as_of},
                    }
                )
                continue

            symbol_week_valid += 1

            action = decision.get("action", ACTION_NO_TRADE)
            size_mult = float(decision.get("size_multiplier", 1.0) or 1.0)

            for d in dec_diag:
                diagnostics.append(d if isinstance(d, dict) else d.to_dict())

            rv = realized_vol_annualized(bars, as_of)
            base_w = vol_scaled_weight(rv, request.target_annual_vol, request.pair_risk_cap)
            base_w *= size_mult

            current_w = positions[symbol].weight if symbol in positions else 0.0
            current_dir = positions[symbol].direction if symbol in positions else None

            target_w = 0.0
            target_dir: Optional[str] = None

            if action == ACTION_FLATTEN:
                target_w = 0.0
            elif action == ACTION_REDUCE and has_pos:
                target_w = current_w * 0.5
                target_dir = current_dir
            elif action == ACTION_NO_TRADE and has_pos:
                target_w = current_w
                target_dir = current_dir
            elif action in (ACTION_BUY, ACTION_SELL):
                target_dir = "LONG" if action == ACTION_BUY else "SHORT"
                target_w = base_w
            else:
                target_w = 0.0

            if target_dir and target_w > 0:
                signed = target_w if target_dir == "LONG" else -target_w
                raw_weights[symbol] = (signed, target_dir)
                week_targets[symbol] = (signed, target_dir, decision)

        capped = apply_exposure_caps(
            raw_weights,
            request.pair_risk_cap,
            request.currency_risk_cap,
            request.total_risk_cap,
        )

        exec_date = _next_trading_day(as_of, request.execution_weekday, trading_set)
        if exec_date is None:
            return

        exec_queue[exec_date] = {
            "targets": capped,
            "decisions": {s: week_targets[s][2] for s in week_targets if s in capped},
            "as_of": as_of,
        }

    for cur_date in all_dates:
        if cur_date < request.start or cur_date > request.end:
            continue

        if cur_date in friday_set:
            _schedule_weekly_decisions(cur_date)

        if cur_date in exec_queue:
            payload = exec_queue[cur_date]
            targets = payload["targets"]
            decisions_meta = payload["decisions"]
            as_of = payload["as_of"]

            for symbol in request.symbols:
                target = targets.get(symbol)
                pos = positions.get(symbol)
                cur_w = pos.weight if pos else 0.0
                cur_dir = pos.direction if pos else None

                if target is None:
                    tgt_w, tgt_dir = 0.0, None
                else:
                    tgt_w, tgt_dir = abs(target[0]), target[1]

                if cur_dir and tgt_dir and cur_dir != tgt_dir and cur_w > 0:
                    exit_cost = _close_position(
                        symbol, pos, cur_date, bar_index, request, trades, positions
                    )
                    if request.cost_model_enabled:
                        equity -= exit_cost * equity
                    turnover += cur_w
                    cur_w = 0.0
                    pos = None

                if tgt_w == 0 and cur_w > 0:
                    exit_cost = _close_position(
                        symbol, pos, cur_date, bar_index, request, trades, positions
                    )
                    if request.cost_model_enabled:
                        equity -= exit_cost * equity
                    turnover += cur_w
                    continue

                if tgt_w == 0:
                    continue

                delta = tgt_w - cur_w
                if abs(delta) < 1e-12 and cur_dir == tgt_dir:
                    continue

                bar = bar_index[symbol].get(cur_date)
                if not bar or bar.get("open") is None:
                    continue

                wd = _parse_date(cur_date).weekday()
                if wd in (4, 5, 6):
                    raise AssertionError(f"new execution on Fri/Sat/Sun: {cur_date}")

                spread = request.spread_pips_by_symbol.get(symbol, 0.0)
                open_px = float(bar["open"])

                if cur_w <= 0:
                    fill = _fill_price(
                        open_px, tgt_dir, spread, request.slippage_pips, symbol, is_entry=True
                    )
                    cost = _cost_fraction(spread, request.slippage_pips, fill, symbol) * tgt_w
                    cost_amt = cost * equity if request.cost_model_enabled else 0.0
                    if request.cost_model_enabled:
                        equity -= cost_amt
                    positions[symbol] = _OpenPosition(
                        symbol=symbol,
                        direction=tgt_dir,
                        weight=tgt_w,
                        entry_date=cur_date,
                        entry_price=fill,
                        entry_equity=equity,
                        aligned_families=list(
                            (decisions_meta.get(symbol) or {}).get("aligned_families") or []
                        ),
                        cost_paid=cost_amt,
                        prev_close=open_px,
                    )
                    turnover += tgt_w
                elif delta > 0:
                    add_w = delta
                    fill = _fill_price(
                        open_px, tgt_dir, spread, request.slippage_pips, symbol, is_entry=True
                    )
                    cost = _cost_fraction(spread, request.slippage_pips, fill, symbol) * add_w
                    cost_amt = cost * equity if request.cost_model_enabled else 0.0
                    if request.cost_model_enabled:
                        equity -= cost_amt
                    pos.weight = tgt_w
                    pos.cost_paid += cost_amt
                    # prev_close deliberately untouched: the daily mark below must
                    # still capture prior-close -> close for the full position
                    turnover += add_w
                elif delta < 0:
                    reduce_w = -delta
                    exit_cost = _partial_close(
                        symbol, pos, reduce_w, cur_date, bar_index, request, trades
                    )
                    if request.cost_model_enabled:
                        equity -= exit_cost * equity
                    pos.weight = tgt_w
                    turnover += reduce_w

        spot_day = 0.0
        swap_day = 0.0
        for symbol, pos in list(positions.items()):
            bar = bar_index[symbol].get(cur_date)
            if not bar:
                continue
            close_px = float(bar["close"])
            if pos.prev_close is None:
                pos.prev_close = close_px
                continue

            if pos.direction == "LONG":
                spot_ret = close_px / pos.prev_close - 1.0
            else:
                spot_ret = -(close_px / pos.prev_close - 1.0)

            contrib = pos.weight * spot_ret
            spot_day += contrib
            pos.spot_pnl += contrib * pos.entry_equity

            swap_info = (swap_returns_by_symbol or {}).get(symbol)
            if swap_info:
                daily = (
                    swap_info["long_daily"]
                    if pos.direction == "LONG"
                    else swap_info["short_daily"]
                )
                rollover = int(swap_info.get("rollover3days", 3))
                units = _swap_daily_units(_parse_date(cur_date).weekday(), rollover)
                swap_ret = daily * units * pos.weight
                swap_day += swap_ret
                pos.swap_pnl += swap_ret * pos.entry_equity

            pos.prev_close = close_px

        day_ret = spot_day + swap_day
        equity *= 1.0 + day_ret
        daily_returns.append(day_ret)
        equity_curve.append({"date": cur_date, "equity": equity})

    in_range = [d for d in all_dates if request.start <= d <= request.end]
    last_date = in_range[-1] if in_range else all_dates[-1]
    for symbol, pos in list(positions.items()):
        _close_position(
            symbol, pos, last_date, bar_index, request, trades, positions, at_close=True
        )

    status = "invalid" if symbol_week_total > 0 and symbol_week_valid == 0 else "completed"

    spot_contrib = sum(t["spot_pnl"] for t in trades)
    swap_contrib = sum(t["swap_pnl"] for t in trades)
    cost_drag = sum(t["cost_paid"] for t in trades)
    wins = [t for t in trades if (t["spot_pnl"] + t["swap_pnl"] - t["cost_paid"]) > 0]
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    sqn = None
    if len(rs) >= 2:
        mean_r = sum(rs) / len(rs)
        var_r = sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1)
        std_r = math.sqrt(var_r)
        if std_r > 0:
            sqn = mean_r / std_r * math.sqrt(len(rs))

    factor_family_pnl: dict[str, float] = defaultdict(float)
    pair_pnl: dict[str, float] = defaultdict(float)
    family_trade_counts: dict[str, int] = defaultdict(int)
    for t in trades:
        net = t["spot_pnl"] + t["swap_pnl"] - t["cost_paid"]
        pair_pnl[t["symbol"]] += net
        fams = t.get("aligned_families") or []
        for f in fams:
            family_trade_counts[f] += 1
        if fams:
            share = net / len(fams)
            for f in fams:
                factor_family_pnl[f] += share

    summary = {
        "trade_count": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "expectancy_r": sum(rs) / len(rs) if rs else 0.0,
        "max_drawdown_pct": _max_drawdown(equity_curve),
        "sharpe": _sharpe(daily_returns),
        "sqn": sqn,
        "turnover": turnover,
        "long_short_split": {
            "long": sum(1 for t in trades if t.get("direction") == "LONG"),
            "short": sum(1 for t in trades if t.get("direction") == "SHORT"),
        },
        "spot_contribution": spot_contrib,
        "swap_contribution": swap_contrib,
        "cost_drag": cost_drag,
    }

    pair_sanity = _pair_sanity_flags(
        trades, dict(factor_family_pnl), summary, degraded_swap_symbols
    )

    report = {
        "run_id": run_id,
        "request": request_dict,
        "status": status,
        "diagnostics": diagnostics,
        "equity_curve": equity_curve,
        "trades": trades,
        "summary": summary,
        "factor_family_pnl": dict(factor_family_pnl),
        "pair_pnl": dict(pair_pnl),
        "pair_sanity_flags": pair_sanity,
        "validation": {
            "family_trade_counts": dict(family_trade_counts),
            "n_warning": any(c < 30 for c in family_trade_counts.values()),
        },
    }
    store.save_backtest_run(run_id, created_at, status, request_dict, report)
    logger.info("backtest %s status=%s trades=%d", run_id, status, len(trades))
    return report


def _empty_report(
    run_id: str,
    request_dict: dict[str, Any],
    status: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "request": request_dict,
        "status": status,
        "diagnostics": diagnostics,
        "equity_curve": [],
        "trades": [],
        "summary": {
            "trade_count": 0,
            "win_rate": 0.0,
            "expectancy_r": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "sqn": None,
            "turnover": 0.0,
            "long_short_split": {"long": 0, "short": 0},
            "spot_contribution": 0.0,
            "swap_contribution": 0.0,
            "cost_drag": 0.0,
        },
        "factor_family_pnl": {},
        "pair_pnl": {},
        "pair_sanity_flags": [],
        "validation": {"family_trade_counts": {}, "n_warning": False},
    }


def _close_position(
    symbol: str,
    pos: _OpenPosition,
    exit_date: str,
    bar_index: dict[str, dict[str, dict]],
    request: FxBacktestRequest,
    trades: list[dict[str, Any]],
    positions: dict[str, _OpenPosition],
    *,
    at_close: bool = False,
) -> float:
    """Close *pos* fully; return exit cost as a fraction of current equity."""
    bar = bar_index[symbol].get(exit_date)
    if not bar:
        positions.pop(symbol, None)
        return 0.0
    spread = request.spread_pips_by_symbol.get(symbol, 0.0)
    if at_close:
        exit_px = float(bar["close"])
        exit_cost = 0.0
        if request.cost_model_enabled:
            exit_cost = (
                _cost_fraction(spread, request.slippage_pips, exit_px, symbol) * pos.weight
            )
            pos.cost_paid += exit_cost * pos.entry_equity
    else:
        open_px = float(bar["open"])
        exit_px = _fill_price(
            open_px, pos.direction, spread, request.slippage_pips, symbol, is_entry=False
        )
        exit_cost = 0.0
        if request.cost_model_enabled:
            exit_cost = (
                _cost_fraction(spread, request.slippage_pips, exit_px, symbol) * pos.weight
            )
            pos.cost_paid += exit_cost * pos.entry_equity

    holding = (_parse_date(exit_date) - _parse_date(pos.entry_date)).days
    risk_denom = request.pair_risk_cap * pos.entry_equity
    net = pos.spot_pnl + pos.swap_pnl - pos.cost_paid
    r_mult = net / risk_denom if risk_denom > 0 else None

    trades.append(
        {
            "symbol": symbol,
            "direction": pos.direction,
            "entry_date": pos.entry_date,
            "exit_date": exit_date,
            "entry_price": pos.entry_price,
            "exit_price": exit_px,
            "spot_pnl": pos.spot_pnl,
            "swap_pnl": pos.swap_pnl,
            "cost_paid": pos.cost_paid,
            "r_multiple": r_mult,
            "holding_days": holding,
            "entry_weight": pos.weight,
            "aligned_families": list(pos.aligned_families),
        }
    )
    positions.pop(symbol, None)
    return exit_cost


def _partial_close(
    symbol: str,
    pos: _OpenPosition,
    reduce_w: float,
    exit_date: str,
    bar_index: dict[str, dict[str, dict]],
    request: FxBacktestRequest,
    trades: list[dict[str, Any]],
) -> float:
    """Close *reduce_w* of *pos*; return exit cost as a fraction of current equity."""
    fraction = reduce_w / pos.weight if pos.weight > 0 else 0.0
    bar = bar_index[symbol].get(exit_date)
    if not bar:
        return 0.0
    spread = request.spread_pips_by_symbol.get(symbol, 0.0)
    open_px = float(bar["open"])
    exit_px = _fill_price(
        open_px, pos.direction, spread, request.slippage_pips, symbol, is_entry=False
    )
    spot_part = pos.spot_pnl * fraction
    swap_part = pos.swap_pnl * fraction
    cost_part = pos.cost_paid * fraction
    pos.cost_paid -= cost_part
    exit_cost = 0.0
    if request.cost_model_enabled:
        exit_cost = _cost_fraction(spread, request.slippage_pips, exit_px, symbol) * reduce_w
        cost_part += exit_cost * pos.entry_equity
    pos.spot_pnl -= spot_part
    pos.swap_pnl -= swap_part
    holding = (_parse_date(exit_date) - _parse_date(pos.entry_date)).days
    risk_denom = request.pair_risk_cap * pos.entry_equity
    net = spot_part + swap_part - cost_part
    trades.append(
        {
            "symbol": symbol,
            "direction": pos.direction,
            "entry_date": pos.entry_date,
            "exit_date": exit_date,
            "entry_price": pos.entry_price,
            "exit_price": exit_px,
            "spot_pnl": spot_part,
            "swap_pnl": swap_part,
            "cost_paid": cost_part,
            "r_multiple": net / risk_denom if risk_denom > 0 else None,
            "holding_days": holding,
            "entry_weight": reduce_w,
            "aligned_families": list(pos.aligned_families),
        }
    )
    return exit_cost
