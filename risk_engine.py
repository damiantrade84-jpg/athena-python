"""risk_engine.py — Mandatory risk gateway for trade execution.

Every order MUST pass through risk_check() before reaching any executor.
No code path bypasses this module. Even on demo. Even for manual clicks.
"""
import logging
import math
import time
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


# ── Peak equity tracker for drawdown circuit breaker ─────────────────────────
_peak_equity = 0.0


def _update_peak(equity: float) -> float:
    """Track peak equity for drawdown calculation."""
    global _peak_equity
    if equity > _peak_equity:
        _peak_equity = equity
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
                 symbol_info: dict | None = None) -> float:
    """Calculate position size in lots from risk budget and SL distance.

    symbol_info may contain: volume_min, volume_max, volume_step, trade_contract_size, point
    """
    risk_budget = account_balance * _EXEC["RISK_PCT"]
    sl_distance = abs(entry_price - sl_price)

    if sl_distance == 0 or entry_price == 0:
        return 0.0

    # Default forex-like lot: 1 lot = 100,000 units
    contract_size = 100_000
    point = 0.00001  # 5-digit broker default

    if symbol_info:
        contract_size = symbol_info.get("trade_contract_size", contract_size)
        point = symbol_info.get("point", point)

    # pip value per lot = contract_size * point (simplified — works for most pairs)
    # For cross-pairs, MT5 provides tick_value which is more accurate
    tick_value = symbol_info.get("trade_tick_value", point * contract_size) if symbol_info else point * contract_size
    tick_size = symbol_info.get("trade_tick_size", point) if symbol_info else point

    if tick_size == 0 or tick_value == 0:
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
    vol_min = symbol_info.get("volume_min", 0.01) if symbol_info else 0.01
    vol_max = symbol_info.get("volume_max", 100.0) if symbol_info else 100.0
    vol_step = symbol_info.get("volume_step", 0.01) if symbol_info else 0.01

    if volume < vol_min:
        log.warning(f"[RISK] volume {volume:.6f} < vol_min {vol_min} — too small")
        return 0.0  # Too small — reject rather than round up

    # Round down to nearest step
    volume = math.floor(volume / vol_step) * vol_step if vol_step > 0 else volume
    volume = min(volume, vol_max)
    volume = round(volume, 2)

    return volume


def _calc_portfolio_heat(open_positions: list, account_balance: float) -> float:
    """Sum risk of all open positions as fraction of account balance."""
    if account_balance <= 0:
        return 1.0  # Infinite heat — block everything
    total_risk = sum(pos.get("risk_amount", 0) for pos in open_positions)
    return total_risk / account_balance


def risk_check(signal: dict, account_balance: float, account_equity: float,
               open_positions: list, symbol_info: dict | None = None,
               kill_switch: bool = False) -> RiskApproval:
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
            pass  # Can't parse timestamp — allow execution but log
            log.warning(f"{prefix} could not parse signal timestamp: {ts_str}")

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

    volume = _calc_volume(account_balance, entry, sl, symbol_info)
    volume = round(volume * dd_factor, 2)  # Apply drawdown reduction

    # Re-clamp after dd_factor
    if symbol_info:
        vol_step = symbol_info.get("volume_step", 0.01)
        volume = math.floor(volume / vol_step) * vol_step if vol_step > 0 else volume
        volume = round(volume, 2)

    if volume <= 0:
        log.warning(f"{prefix} REJECTED: calculated volume is 0 (balance={account_balance}, SL dist={abs(entry-sl):.6f})")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, "ZERO_VOLUME")

    # ── Check 6: Risk amount validation ─────────────────────────────────────
    sl_distance = abs(entry - sl)
    tick_size = symbol_info.get("trade_tick_size", 0.00001) if symbol_info else 0.00001
    tick_value = symbol_info.get("trade_tick_value", 0.00001 * 100_000) if symbol_info else 1.0
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
