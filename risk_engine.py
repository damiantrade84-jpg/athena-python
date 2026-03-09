"""risk_engine.py — Mandatory risk gateway for trade execution.

Every order MUST pass through risk_check() before reaching any executor.
No code path bypasses this module. Even on demo. Even for manual clicks.
"""
import logging
import math
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from config import CONFIG
from scoring import CORR_CLUSTERS

log = logging.getLogger("athena")

# ── Execution config defaults (overridden by config.yaml) ────────────────────
_EXEC = {
    "RISK_PCT": CONFIG.get("RISK_PCT", 0.01),
    "MAX_PORTFOLIO_HEAT": CONFIG.get("MAX_PORTFOLIO_HEAT", 0.06),
    "MAX_OPEN_POSITIONS": CONFIG.get("MAX_OPEN_POSITIONS", 5),
    "MAX_CORRELATED": CONFIG.get("MAX_CORRELATED_POSITIONS", 2),
    "SIGNAL_MAX_AGE_SEC": CONFIG.get("SIGNAL_MAX_AGE_SEC", 300),
    "DRAWDOWN_REDUCE": CONFIG.get("DRAWDOWN_REDUCE_THRESHOLD", 0.10),
    "DRAWDOWN_STOP": CONFIG.get("DRAWDOWN_STOP_THRESHOLD", 0.15),
    "MAX_RISK_PER_TRADE": CONFIG.get("MAX_RISK_PER_TRADE", 0.03),
    "DAILY_LOSS_LIMIT": CONFIG.get("DAILY_LOSS_LIMIT", 0.05),
}


@dataclass
class RiskApproval:
    """Result of risk_check(). Executors receive this — they cannot size their own orders."""
    approved: bool
    volume: float           # Lot size (0.0 if rejected)
    risk_amount: float      # Dollar risk for this trade
    risk_pct: float         # As fraction of account (0.01 = 1%)
    portfolio_heat: float   # Total portfolio risk after this trade (fraction)
    reason: str             # "OK" or rejection reason code

    def to_dict(self) -> dict:
        return asdict(self)


# ── Peak equity persistence (survives restarts) ─────────────────────────────
_RISK_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")

def _init_peak_table():
    """Create peak_equity table if it doesn't exist."""
    try:
        with sqlite3.connect(_RISK_DB) as con:
            con.execute("CREATE TABLE IF NOT EXISTS peak_equity (id INTEGER PRIMARY KEY CHECK(id=1), value REAL NOT NULL, updated_at TEXT NOT NULL)")
            con.commit()
    except Exception as e:
        log.error(f"[RISK] Failed to init peak_equity table: {e}")

def _load_peak_equity() -> float:
    """Restore peak equity from SQLite on startup."""
    try:
        with sqlite3.connect(_RISK_DB) as con:
            row = con.execute("SELECT value, updated_at FROM peak_equity WHERE id=1").fetchone()
            if row:
                log.info(f"[RISK] Restored peak equity: ${row[0]:,.2f} (saved {row[1]})")
                return float(row[0])
    except Exception as e:
        log.warning(f"[RISK] Could not load peak equity: {e}")
    return 0.0

def _save_peak_equity(value: float):
    """Persist peak equity to SQLite."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(_RISK_DB) as con:
            con.execute(
                "INSERT INTO peak_equity (id, value, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (value, ts))
            con.commit()
    except Exception:
        pass  # non-fatal — next update will retry

_init_peak_table()
_peak_equity = _load_peak_equity()

# ── Daily loss tracker ───────────────────────────────────────────────────────
_daily_pnl: float = 0.0
_daily_pnl_date: str = ""
_daily_start_balance: float = 0.0


def record_daily_pnl(pnl: float, account_balance: float) -> None:
    """Record realized P&L for daily loss tracking. Called by outcome monitor."""
    global _daily_pnl, _daily_pnl_date, _daily_start_balance
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _daily_pnl_date:
        _daily_pnl = 0.0
        _daily_pnl_date = today
        _daily_start_balance = account_balance
    _daily_pnl += pnl
    if _daily_start_balance > 0 and _daily_pnl < 0:
        loss_pct = abs(_daily_pnl) / _daily_start_balance
        if loss_pct >= _EXEC["DAILY_LOSS_LIMIT"]:
            log.warning(f"[RISK] DAILY LOSS LIMIT: lost ${abs(_daily_pnl):.2f} ({loss_pct:.1%}) today — blocking new trades")


def _check_daily_loss(account_balance: float) -> tuple[bool, float]:
    """Check if daily loss limit is breached. Returns (blocked, loss_pct)."""
    global _daily_pnl_date, _daily_pnl, _daily_start_balance
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _daily_pnl_date:
        _daily_pnl = 0.0
        _daily_pnl_date = today
        _daily_start_balance = account_balance
        return False, 0.0
    if _daily_start_balance <= 0:
        _daily_start_balance = account_balance
    if _daily_pnl >= 0:
        return False, 0.0
    loss_pct = abs(_daily_pnl) / _daily_start_balance
    return loss_pct >= _EXEC["DAILY_LOSS_LIMIT"], loss_pct


def _update_peak(equity: float) -> float:
    """Track peak equity for drawdown calculation. Persists to SQLite on new highs."""
    global _peak_equity
    if equity > _peak_equity:
        _peak_equity = equity
        _save_peak_equity(_peak_equity)
    return _peak_equity


def _current_drawdown(equity: float) -> float:
    """Calculate current drawdown from peak as a fraction (0.0 = no drawdown)."""
    peak = _update_peak(equity)
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak)


def _cluster_for_pair(pair_display: str) -> str | None:
    """Find which correlation cluster a pair belongs to."""
    for cluster_name, members in CORR_CLUSTERS.items():
        if pair_display in members:
            return cluster_name
    return None


def _count_correlated(pair_display: str, open_positions: list) -> int:
    """Count how many open positions are in the same correlation cluster."""
    cluster = _cluster_for_pair(pair_display)
    if not cluster:
        return 0
    members = set(CORR_CLUSTERS[cluster])
    return sum(1 for pos in open_positions if pos.get("pair") in members)


def _calc_volume(account_balance: float, entry_price: float, sl_price: float,
                 symbol_info: dict | None = None, asset_type: str = "") -> float:
    """Calculate position size in lots from risk budget and SL distance.

    symbol_info may contain: volume_min, volume_max, volume_step, trade_contract_size, point
    """
    risk_budget = account_balance * _EXEC["RISK_PCT"]
    sl_distance = abs(entry_price - sl_price)

    if sl_distance == 0 or entry_price == 0:
        return 0.0

    is_crypto = asset_type == "crypto"

    # Crypto: 1 unit = 1 unit, tick_value = tick_size (USDT pairs)
    # Stocks: 1 lot = 1 share, price step = 0.01
    # Forex: 1 lot = 100,000 units, 5-digit broker default
    is_stock = asset_type == "stock"
    is_commodity = asset_type == "commodity"
    if is_crypto:
        contract_size = 1
        point = 0.01
    elif is_stock:
        contract_size = 1       # 1 share per lot
        point = 0.01            # 1 cent price step
    elif is_commodity:
        contract_size = 100     # typical commodity lot (e.g. 100 oz gold)
        point = 0.01            # 1 cent price step for metals/oil
    else:
        contract_size = 100_000
        point = 0.00001  # 5-digit broker default

    if symbol_info:
        contract_size = symbol_info.get("trade_contract_size", contract_size)
        point = symbol_info.get("point", point)
        log.info(f"[RISK] _calc_volume symbol_info: contract_size={contract_size}, point={point}, "
                 f"tick_value={symbol_info.get('trade_tick_value')}, tick_size={symbol_info.get('trade_tick_size')}, "
                 f"vol_min={symbol_info.get('volume_min')}, vol_step={symbol_info.get('volume_step')}, "
                 f"bid={symbol_info.get('bid')}, ask={symbol_info.get('ask')}")

    # pip value per lot = contract_size * point (simplified — works for most pairs)
    # For cross-pairs, MT5 provides tick_value which is more accurate
    tick_value = symbol_info.get("trade_tick_value", point * contract_size) if symbol_info else point * contract_size
    tick_size = symbol_info.get("trade_tick_size", point) if symbol_info else point

    # Fallback: if MT5 returns tick_value=0 (market closed, no quotes), estimate from contract specs
    if tick_value == 0 and contract_size > 0 and point > 0:
        tick_value = point * contract_size
        log.warning(f"[RISK] _calc_volume: MT5 tick_value=0 (market closed?), using fallback={tick_value}")
    if tick_size == 0 and point > 0:
        tick_size = point
        log.warning(f"[RISK] _calc_volume: MT5 tick_size=0, using fallback={tick_size}")

    if tick_size == 0 or tick_value == 0:
        log.warning(f"[RISK] _calc_volume: ZERO tick_size={tick_size} or tick_value={tick_value} — cannot calculate volume")
        return 0.0

    # Dollar risk per lot for the given SL distance
    ticks_in_sl = sl_distance / tick_size
    risk_per_lot = ticks_in_sl * tick_value

    if risk_per_lot == 0:
        log.warning(f"[RISK] _calc_volume: risk_per_lot=0, ticks_in_sl={ticks_in_sl}, tick_value={tick_value}")
        return 0.0

    volume = risk_budget / risk_per_lot

    log.info(f"[RISK] _calc_volume: budget=${risk_budget:.2f}, sl_dist={sl_distance:.6f}, "
             f"ticks={ticks_in_sl:.1f}, tick_val={tick_value}, risk/lot=${risk_per_lot:.2f}, raw_vol={volume:.4f}")

    # Clamp to symbol constraints
    _MAX_CRYPTO_NOTIONAL = 5000.0  # Hard cap: max $5000 notional per crypto trade (at 1x leverage)
    if is_crypto:
        vol_min = symbol_info.get("volume_min", 0.001) if symbol_info else 0.001
        vol_max = symbol_info.get("volume_max", 9999.0) if symbol_info else 9999.0
        vol_step = symbol_info.get("volume_step", 0.001) if symbol_info else 0.001
        # Notional cap: prevent oversized positions even when SL is tight
        if entry_price > 0:
            max_by_notional = _MAX_CRYPTO_NOTIONAL / entry_price
            if volume > max_by_notional:
                log.warning(f"[RISK] crypto volume {volume:.4f} clamped to {max_by_notional:.4f} (${_MAX_CRYPTO_NOTIONAL} notional cap)")
                volume = max_by_notional
    elif is_stock:
        vol_min  = symbol_info.get("volume_min",  1.0)   if symbol_info else 1.0    # min 1 share
        vol_max  = symbol_info.get("volume_max",  5000.0) if symbol_info else 5000.0
        vol_step = symbol_info.get("volume_step", 1.0)   if symbol_info else 1.0    # whole shares
    else:
        vol_min = symbol_info.get("volume_min", 0.01) if symbol_info else 0.01
        vol_max = symbol_info.get("volume_max", 100.0) if symbol_info else 100.0
        vol_step = symbol_info.get("volume_step", 0.01) if symbol_info else 0.01

    if volume < vol_min:
        log.warning(f"[RISK] volume {volume:.6f} < vol_min {vol_min} — too small")
        return 0.0  # Too small — reject rather than round up

    # Round down to nearest step
    volume = math.floor(volume / vol_step) * vol_step if vol_step > 0 else volume
    volume = min(volume, vol_max)
    volume = round(volume, 6 if is_crypto else (0 if is_stock else 2))

    return volume


def _calc_portfolio_heat(open_positions: list, account_balance: float) -> float:
    """Sum risk of all open positions as fraction of account balance."""
    if account_balance <= 0:
        return 1.0  # Infinite heat — block everything
    total_risk = sum(pos.get("risk_amount", 0) for pos in open_positions)
    return total_risk / account_balance


def risk_check(signal: dict, account_balance: float, account_equity: float,
               open_positions: list, symbol_info: dict | None = None,
               kill_switch: bool = False, sizing_override: float = 1.0) -> RiskApproval:
    """Mandatory risk gateway. Every execution path calls this first.

    Args:
        signal: Full signal object from analyze_pair() — must contain:
                pair, direction, price, sl, tp1, tp2, type, timestamp, confluenceScore
        account_balance: Current account balance in account currency
        account_equity: Current account equity (balance + unrealized P&L)
        open_positions: List of dicts with at minimum {pair, risk_amount}
        symbol_info: MT5 symbol_info dict (volume_min, volume_max, etc.) — None for crypto
        kill_switch: Global kill switch state

    Returns:
        RiskApproval — executors ONLY proceed if approved=True
    """
    pair = signal.get("pair", "UNKNOWN")
    prefix = f"[RISK] {pair}"

    # ── Check 0: Kill switch ────────────────────────────────────────────────
    if kill_switch:
        log.warning(f"{prefix} REJECTED: kill switch active")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "KILL_SWITCH_ACTIVE")

    # ── Check 0.5: Daily loss limit ────────────────────────────────────────
    daily_blocked, daily_loss_pct = _check_daily_loss(account_balance)
    if daily_blocked:
        log.warning(f"{prefix} REJECTED: daily loss {daily_loss_pct:.1%} exceeds {_EXEC['DAILY_LOSS_LIMIT']:.0%} limit")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "DAILY_LOSS_LIMIT")

    # ── Check 1: Signal freshness ───────────────────────────────────────────
    ts_str = signal.get("timestamp")
    if ts_str:
        try:
            sig_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - sig_time).total_seconds()
            if age > _EXEC["SIGNAL_MAX_AGE_SEC"]:
                log.warning(f"{prefix} REJECTED: signal is {age:.0f}s old (max {_EXEC['SIGNAL_MAX_AGE_SEC']}s)")
                return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "STALE_SIGNAL")
        except (ValueError, TypeError):
            log.warning(f"{prefix} REJECTED: could not parse signal timestamp: {ts_str}")
            return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "UNPARSEABLE_TIMESTAMP")

    # ── Check 2: Drawdown circuit breaker ───────────────────────────────────
    dd = _current_drawdown(account_equity)
    dd_factor = 1.0
    if dd >= _EXEC["DRAWDOWN_STOP"]:
        log.warning(f"{prefix} REJECTED: drawdown {dd:.1%} exceeds stop threshold {_EXEC['DRAWDOWN_STOP']:.0%}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "DRAWDOWN_CIRCUIT_BREAKER")
    if dd >= _EXEC["DRAWDOWN_REDUCE"]:
        dd_factor = 0.5
        log.info(f"{prefix} drawdown {dd:.1%} — halving position size")

    # ── Check 3: Max open positions ─────────────────────────────────────────
    if len(open_positions) >= _EXEC["MAX_OPEN_POSITIONS"]:
        log.warning(f"{prefix} REJECTED: {len(open_positions)} open positions (max {_EXEC['MAX_OPEN_POSITIONS']})")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "MAX_POSITIONS_REACHED")

    # ── Check 4: Correlation guard ──────────────────────────────────────────
    corr_count = _count_correlated(pair, open_positions)
    if corr_count >= _EXEC["MAX_CORRELATED"]:
        cluster = _cluster_for_pair(pair)
        log.warning(f"{prefix} REJECTED: {corr_count} positions in '{cluster}' cluster (max {_EXEC['MAX_CORRELATED']})")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "CORRELATED_CLUSTER_FULL")

    # ── Check 5: Calculate position size ────────────────────────────────────
    entry = signal.get("price", 0)
    sl = signal.get("sl", 0)
    if not entry or not sl or entry == sl:
        log.warning(f"{prefix} REJECTED: invalid entry={entry} or sl={sl}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "INVALID_LEVELS")

    asset_type = signal.get("type", "")
    volume = _calc_volume(account_balance, entry, sl, symbol_info, asset_type)
    is_crypto    = asset_type == "crypto"
    is_stock     = asset_type == "stock"
    is_commodity = asset_type == "commodity"

    # Score-scaled sizing: scale position by signal quality (weak signals get smaller bets)
    max_score = signal.get("maxScore", 13.0)
    score = signal.get("confluenceScore", max_score)
    score_factor = max(0.25, min(1.0, score / max_score)) if max_score > 0 else 1.0
    # Also apply AI sizing override (1.0=full, 0.75=normal, 0.5=half, 0.25=quarter)
    combined_factor = dd_factor * score_factor * max(0.25, min(1.0, sizing_override))
    _decimals = 6 if is_crypto else (0 if is_stock else 2)
    volume = round(volume * combined_factor, _decimals)
    log.info(f"{prefix} sizing: score_factor={score_factor:.2f}, sizing_override={sizing_override:.2f}, dd_factor={dd_factor:.2f} → combined={combined_factor:.2f}")

    # Re-clamp after scaling
    _default_step = 0.001 if is_crypto else (1.0 if is_stock else 0.01)
    vol_step = symbol_info.get("volume_step", _default_step) if symbol_info else _default_step
    volume = math.floor(volume / vol_step) * vol_step if vol_step > 0 else volume
    volume = round(volume, _decimals)

    # Stocks: ensure minimum 1 share (can't trade fractional on MT5)
    if is_stock and 0 < volume < 1:
        volume = 1.0

    if volume <= 0:
        log.warning(f"{prefix} REJECTED: calculated volume is 0 — raw_vol from _calc_volume scaled by combined_factor={combined_factor:.2f}, "
                    f"balance={account_balance}, entry={entry}, SL={sl}, dist={abs(entry-sl):.6f}, asset={asset_type}, "
                    f"symbol_info={'yes' if symbol_info else 'no'}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "ZERO_VOLUME")

    # ── Check 6: Risk amount validation ─────────────────────────────────────
    sl_distance = abs(entry - sl)
    if is_crypto:
        _default_tick = 0.01
        _default_tick_val = 0.01
    elif is_stock or is_commodity:
        _default_tick = 0.01
        _default_tick_val = 0.01  # 1 cent per tick per unit
    else:
        _default_tick = 0.00001
        _default_tick_val = 1.0
    tick_size = symbol_info.get("trade_tick_size", _default_tick) if symbol_info else _default_tick
    tick_value = symbol_info.get("trade_tick_value", _default_tick_val) if symbol_info else _default_tick_val
    ticks_in_sl = sl_distance / tick_size if tick_size > 0 else 0
    risk_amount = ticks_in_sl * tick_value * volume

    risk_pct = risk_amount / account_balance if account_balance > 0 else 1.0

    # Hard cap: never risk more than MAX_RISK_PER_TRADE
    if risk_pct > _EXEC["MAX_RISK_PER_TRADE"]:
        log.warning(f"{prefix} REJECTED: risk {risk_pct:.1%} exceeds max {_EXEC['MAX_RISK_PER_TRADE']:.0%}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "RISK_TOO_HIGH")

    # ── Check 7: Portfolio heat ─────────────────────────────────────────────
    current_heat = _calc_portfolio_heat(open_positions, account_balance)
    new_heat = current_heat + risk_pct
    if new_heat > _EXEC["MAX_PORTFOLIO_HEAT"]:
        log.warning(f"{prefix} REJECTED: portfolio heat would be {new_heat:.1%} (max {_EXEC['MAX_PORTFOLIO_HEAT']:.0%})")
        return RiskApproval(False, 0.0, 0.0, 0.0, new_heat, "PORTFOLIO_HEAT_EXCEEDED")

    # ── ALL CHECKS PASSED ───────────────────────────────────────────────────
    log.info(f"{prefix} APPROVED: {volume} lots, risk ${risk_amount:.2f} ({risk_pct:.2%}), heat {new_heat:.2%}")
    return RiskApproval(
        approved=True,
        volume=volume,
        risk_amount=round(risk_amount, 2),
        risk_pct=round(risk_pct, 4),
        portfolio_heat=round(new_heat, 4),
        reason="OK"
    )
