"""risk_engine.py — Mandatory risk gateway for trade execution.

Every order MUST pass through risk_check() before reaching any executor.
No code path bypasses this module. Even on demo. Even for manual clicks.
"""

import logging
import math
import os
import queue
import sqlite3
import threading
import time as _time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from config import CONFIG
from scoring import CORR_CLUSTERS, _PAIR_TO_CLUSTER

log = logging.getLogger("sentinel")


# ── Execution config helpers (live reads so hot-reload works) ─────────────────
def _cfg(key: str, default):
    """Read a risk config value live from CONFIG so restarts aren't needed."""
    return CONFIG.get(key, default)


# Legacy alias kept for backward compat — reads live
_EXEC_DEFAULTS = {
    "RISK_PCT": 0.01,
    "MAX_PORTFOLIO_HEAT": 0.06,
    "MAX_OPEN_POSITIONS": 5,
    "MAX_CORRELATED": 2,
    "SIGNAL_MAX_AGE_SEC": 300,
    "DRAWDOWN_REDUCE": 0.10,
    "DRAWDOWN_STOP": 0.15,
    "MAX_RISK_PER_TRADE": 0.03,
    "DAILY_LOSS_LIMIT": 0.05,
}
# Frozen _EXEC replaced — callers should use _cfg() or CONFIG.get() directly


@dataclass
class RiskApproval:
    """Result of risk_check(). Executors receive this — they cannot size their own orders."""

    approved: bool
    volume: float  # Lot size (0.0 if rejected)
    risk_amount: float  # Dollar risk for this trade
    risk_pct: float  # As fraction of account (0.01 = 1%)
    portfolio_heat: float  # Total portfolio risk after this trade (fraction)
    drawdown_pct: float  # Current drawdown at decision time (fraction)
    reason: str  # "OK" or rejection reason code

    def to_dict(self) -> dict:
        return asdict(self)


# ── Peak equity persistence (survives restarts) ─────────────────────────────
_RISK_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
_PEAK_UPSERT_SQL = (
    "INSERT INTO peak_equity (asset_type, value, updated_at) VALUES (?, ?, ?) "
    "ON CONFLICT(asset_type) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
)
_peak_audit_queue: queue.Queue = queue.Queue()
_peak_audit_pending = 0
_peak_audit_pending_lock = threading.Lock()
_peak_audit_worker_started = False
_peak_audit_worker_lock = threading.Lock()
_PEAK_AUDIT_BATCH_MAX = 32


def _init_peak_table():
    """Create peak_equity table if it doesn't exist; migrate legacy single-row schema."""
    try:
        with sqlite3.connect(_RISK_DB, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='peak_equity'"
            ).fetchone()
            if cur:
                cols = [
                    r[1]
                    for r in con.execute("PRAGMA table_info(peak_equity)").fetchall()
                ]
                if "id" in cols and "asset_type" not in cols:
                    con.execute("DROP TABLE peak_equity")
                    log.info(
                        "[RISK] Migrated peak_equity to per–asset-class schema (legacy row dropped)"
                    )
            con.execute(
                "CREATE TABLE IF NOT EXISTS peak_equity ("
                "asset_type TEXT PRIMARY KEY, value REAL NOT NULL, updated_at TEXT NOT NULL)"
            )
            con.commit()
    except Exception as e:
        log.error(f"[RISK] Failed to init peak_equity table: {e}")


def _load_peak_equity() -> dict[str, float]:
    """Restore per–asset-class peak equity from SQLite on startup."""
    out: dict[str, float] = {}
    try:
        with sqlite3.connect(_RISK_DB, timeout=15.0) as con:
            rows = con.execute(
                "SELECT asset_type, value, updated_at FROM peak_equity"
            ).fetchall()
            for r in rows:
                out[str(r[0])] = float(r[1])
                log.info(
                    f"[RISK] Restored peak equity [{r[0]}]: ${float(r[1]):,.2f} (saved {r[2]})"
                )
    except Exception as e:
        log.warning(f"[RISK] Could not load peak equity: {e}")
    return out


def _peak_audit_worker_loop() -> None:
    """Daemon worker: persist peak_equity upserts off the caller thread."""
    global _peak_audit_pending
    while True:
        try:
            first = _peak_audit_queue.get()
        except Exception:
            continue
        batch = [first]
        try:
            while len(batch) < _PEAK_AUDIT_BATCH_MAX:
                batch.append(_peak_audit_queue.get_nowait())
        except queue.Empty:
            pass

        n_batch = len(batch)
        try:
            rows = []
            for row in batch:
                if not isinstance(row, tuple) or len(row) != 3:
                    log.debug(f"[RISK] peak worker skipping invalid payload: {row!r}")
                    continue
                rows.append(row)
            if rows:
                with sqlite3.connect(_RISK_DB, timeout=15.0) as con:
                    con.executemany(_PEAK_UPSERT_SQL, rows)
                    con.commit()
        except Exception as e:
            log.warning(f"[RISK] peak equity worker batch failed: {e}")
        finally:
            with _peak_audit_pending_lock:
                _peak_audit_pending -= n_batch


def _ensure_peak_audit_worker() -> None:
    global _peak_audit_worker_started
    if _peak_audit_worker_started:
        return
    with _peak_audit_worker_lock:
        if _peak_audit_worker_started:
            return
        _t = threading.Thread(
            target=_peak_audit_worker_loop,
            daemon=True,
            name="risk-peak-audit-worker",
        )
        _t.start()
        _peak_audit_worker_started = True


def join_peak_audit_queue(timeout: float | None = None) -> None:
    """Block until queued peak_equity writes are persisted (for tests)."""
    deadline = float("inf") if timeout is None else (_time.time() + timeout)
    while True:
        with _peak_audit_pending_lock:
            pending = _peak_audit_pending
        if pending == 0 and _peak_audit_queue.empty():
            _time.sleep(0.03)
            with _peak_audit_pending_lock:
                pending2 = _peak_audit_pending
            if pending2 == 0 and _peak_audit_queue.empty():
                return
        if _time.time() >= deadline:
            with _peak_audit_pending_lock:
                pend = _peak_audit_pending
            raise TimeoutError(
                f"peak audit queue not drained within {timeout}s ({pend} pending)"
            )
        _time.sleep(0.02)


def _save_peak_equity(asset_type: str, value: float):
    """Enqueue peak equity persistence for one asset class."""
    global _peak_audit_pending
    try:
        ts = datetime.now(timezone.utc).isoformat()
        _ensure_peak_audit_worker()
        with _peak_audit_pending_lock:
            _peak_audit_pending += 1
        _peak_audit_queue.put((asset_type, value, ts), block=False)
    except Exception as e:
        with _peak_audit_pending_lock:
            _peak_audit_pending -= 1
        log.warning(
            f"[RISK] peak equity save failed: {e}"
        )  # non-fatal — next update will retry


_init_peak_table()
_peak_equity: dict[str, float] = _load_peak_equity()
_peak_lock = threading.Lock()
_peak_last_saved: float = 0.0  # epoch seconds of last _save_peak_equity call (any asset)

# ── Daily loss tracker ───────────────────────────────────────────────────────
_daily_pnl: dict[str, float] = {}
_daily_pnl_date: str = ""
_daily_start_balance: dict[str, float] = {}
_daily_lock = threading.Lock()
_kelly_cache: dict = {}           # (asset_type, regime) → (adaptive_risk_pct, cached_at)
_KELLY_TTL_SECONDS: float = 300.0  # 5 minutes


def record_daily_pnl(
    pnl: float, account_balance: float, asset_type: str = ""
) -> None:
    """Record realized P&L for daily loss tracking (per asset class). Called by outcome monitor."""
    global _daily_pnl, _daily_pnl_date, _daily_start_balance
    at = asset_type or "unknown"
    with _daily_lock:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != _daily_pnl_date:
            _daily_pnl = {}
            _daily_start_balance = {}
            _daily_pnl_date = today
        if at not in _daily_start_balance or _daily_start_balance[at] <= 0:
            _daily_start_balance[at] = account_balance
        _daily_pnl[at] = _daily_pnl.get(at, 0.0) + pnl
        if _daily_start_balance[at] > 0 and _daily_pnl[at] < 0:
            loss_pct = abs(_daily_pnl[at]) / _daily_start_balance[at]
            if loss_pct >= _cfg("DAILY_LOSS_LIMIT", 0.05):
                log.warning(
                    f"[RISK] DAILY LOSS LIMIT [{at}]: lost ${abs(_daily_pnl[at]):.2f} "
                    f"({loss_pct:.1%}) today — blocking new trades"
                )


def _check_daily_loss(
    account_balance: float, asset_type: str = ""
) -> tuple[bool, float]:
    """Check if daily loss limit is breached for this asset class. Returns (blocked, loss_pct)."""
    global _daily_pnl_date, _daily_pnl, _daily_start_balance
    at = asset_type or "unknown"
    with _daily_lock:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != _daily_pnl_date:
            _daily_pnl = {}
            _daily_start_balance = {}
            _daily_pnl_date = today
            return False, 0.0
        if at not in _daily_start_balance or _daily_start_balance[at] <= 0:
            _daily_start_balance[at] = account_balance
        pnl = _daily_pnl.get(at, 0.0)
        if pnl >= 0:
            return False, 0.0
        loss_pct = abs(pnl) / _daily_start_balance[at]
        return loss_pct >= _cfg("DAILY_LOSS_LIMIT", 0.05), loss_pct


def _update_peak(equity: float, asset_type: str = "") -> float:
    """Track peak equity for drawdown calculation (per asset class). Thread-safe. Persists on new highs."""
    global _peak_equity, _peak_last_saved
    at = asset_type or "unknown"
    with _peak_lock:
        cur = _peak_equity.get(at)
        if cur is None or equity > cur:
            _peak_equity[at] = equity
            now_ts = _time.time()
            if now_ts - _peak_last_saved >= 60.0:
                _save_peak_equity(at, _peak_equity[at])
                _peak_last_saved = now_ts
        return _peak_equity[at]


def _current_drawdown(equity: float, asset_type: str = "") -> float:
    """Calculate current drawdown from peak as a fraction (0.0 = no drawdown)."""
    peak = _update_peak(equity, asset_type)
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak)


def _cluster_for_pair(pair_display: str) -> str | None:
    """Find which correlation cluster a pair belongs to."""
    return _PAIR_TO_CLUSTER.get(pair_display)


def _count_correlated(pair_display: str, open_positions: list) -> int:
    """Count how many open positions are in the same correlation cluster."""
    cluster = _cluster_for_pair(pair_display)
    if not cluster:
        return 0
    members = set(CORR_CLUSTERS[cluster])
    return sum(1 for pos in open_positions if pos.get("pair") in members)


def _adaptive_risk_pct(asset_type: str, regime: str = "") -> float:
    """Fractional Kelly position sizing from recent trade history.

    Uses half-Kelly criterion: kelly * 0.5 for safety.
    Falls back to fixed RISK_PCT if insufficient data (<10 trades).
    Clamped to 0.5%-3% hard safety bounds.

    Per-asset-class base risk:
    - Forex: 0.5% (lower risk, tighter stops with D1 ATR)
    - Crypto: 1.0% (higher risk, more volatile)
    - Others: 1.0% (default)
    """
    # Per-asset-class base risk percentages
    asset_risk_map = {
        "forex": 0.005,  # 0.5% for forex (tighter stops with D1 ATR)
        "crypto": 0.010,  # 1.0% for crypto (more volatile)
        "stock": 0.010,  # 1.0% for stocks
        "commodity": 0.010,  # 1.0% for commodities
        "index": 0.010,  # 1.0% for indices
    }
    base_risk = asset_risk_map.get(asset_type, _cfg("RISK_PCT", 0.01))

    if not _cfg("ADAPTIVE_KELLY_ENABLED", True):
        return base_risk

    try:
        now_ts = _time.time()
        _cache_key = (asset_type, regime or "")
        _cached = _kelly_cache.get(_cache_key)
        if _cached and (now_ts - _cached[1]) < _KELLY_TTL_SECONDS:
            return _cached[0]

        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
        con = sqlite3.connect(db_path, timeout=15.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.row_factory = sqlite3.Row

        rows = con.execute(
            "SELECT win, r_multiple FROM learning_log "
            "WHERE asset_type = ? AND win IS NOT NULL AND r_multiple IS NOT NULL "
            "ORDER BY ts DESC LIMIT 30",
            (asset_type,),
        ).fetchall()
        con.close()

        if len(rows) < 10:
            return base_risk

        wins = [r for r in rows if r["win"]]
        losses = [r for r in rows if not r["win"]]
        win_rate = len(wins) / len(rows)

        avg_win = sum(r["r_multiple"] for r in wins) / len(wins) if wins else 0
        avg_loss = (
            sum(abs(r["r_multiple"]) for r in losses) / len(losses) if losses else 1
        )

        if avg_loss == 0:
            return base_risk

        win_loss_ratio = avg_win / avg_loss
        kelly_pct = win_rate - ((1 - win_rate) / win_loss_ratio)

        # Half-Kelly for safety (proven approach)
        half_kelly = kelly_pct * 0.5

        # Clamp between 0.5% and 3%
        adaptive_risk = max(0.005, min(0.03, half_kelly))

        # Regime adjustment: reduce in HIGH_VOLATILITY
        if regime == "HIGH_VOLATILITY":
            adaptive_risk *= 0.7

        _kelly_cache[_cache_key] = (adaptive_risk, now_ts)

        log.info(
            f"[KELLY] {asset_type}: WR={win_rate:.0%}, W/L={win_loss_ratio:.2f}, "
            f"kelly={kelly_pct:.3f}, half={half_kelly:.3f}, final={adaptive_risk:.3f}"
        )

        return adaptive_risk

    except Exception as e:
        log.debug(f"[KELLY] Fallback to base risk: {e}")
        return base_risk


def _calc_volume(
    account_balance: float,
    entry_price: float,
    sl_price: float,
    symbol_info: dict | None = None,
    asset_type: str = "",
    regime: str = "",
    pair: dict = None,
) -> float:
    """Calculate position size in lots from risk budget and SL distance.

    symbol_info may contain: volume_min, volume_max, volume_step, trade_contract_size, point
    """
    risk_budget = account_balance * _adaptive_risk_pct(asset_type, regime)
    sl_distance = abs(entry_price - sl_price)

    if sl_distance == 0 or entry_price == 0:
        return 0.0

    # Per-commodity contract sizes
    _COMMODITY_CONTRACTS = {
        "XAU/USD": 100,  # 100 troy oz per lot (gold)
        "XAG/USD": 5000,  # 5000 troy oz per lot (silver)
        "WTI Oil": 1000,  # 1000 barrels per lot (crude)
        "Brent Oil": 1000,  # 1000 barrels per lot
        "Nat Gas": 10000,  # 10000 MMBtu per lot
        "Copper": 25000,  # 25000 lbs per lot
        "XPT/USD": 50,  # 50 oz per lot (platinum)
        "XPD/USD": 100,  # 100 oz per lot (palladium)
    }

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
        contract_size = 1  # 1 share per lot
        point = 0.01  # 1 cent price step
    elif is_commodity:
        display = (
            (pair.get("display") or pair.get("pair", ""))
            if isinstance(pair, dict)
            else ""
        )
        contract_size = _COMMODITY_CONTRACTS.get(display, 100)
        point = 0.01  # 1 cent price step for metals/oil
    else:
        contract_size = 100_000
        point = 0.00001  # 5-digit broker default

    if symbol_info:
        contract_size = symbol_info.get("trade_contract_size", contract_size)
        point = symbol_info.get("point", point)
        log.info(
            f"[RISK] _calc_volume symbol_info: contract_size={contract_size}, point={point}, "
            f"tick_value={symbol_info.get('trade_tick_value')}, tick_size={symbol_info.get('trade_tick_size')}, "
            f"vol_min={symbol_info.get('volume_min')}, vol_step={symbol_info.get('volume_step')}, "
            f"bid={symbol_info.get('bid')}, ask={symbol_info.get('ask')}"
        )

    # pip value per lot = contract_size * point (simplified — works for most pairs)
    # For cross-pairs, MT5 provides tick_value which is more accurate
    tick_value = (
        symbol_info.get("trade_tick_value", point * contract_size)
        if symbol_info
        else point * contract_size
    )
    tick_size = symbol_info.get("trade_tick_size", point) if symbol_info else point

    # Fallback: if MT5 returns tick_value=0 (market closed, no quotes), estimate from contract specs
    if tick_value == 0 and contract_size > 0 and point > 0:
        tick_value = point * contract_size
        log.warning(
            f"[RISK] _calc_volume: MT5 tick_value=0 (market closed?), using fallback={tick_value}"
        )
    if tick_size == 0 and point > 0:
        tick_size = point
        log.warning(f"[RISK] _calc_volume: MT5 tick_size=0, using fallback={tick_size}")

    if tick_size == 0 or tick_value == 0:
        log.warning(
            f"[RISK] _calc_volume: ZERO tick_size={tick_size} or tick_value={tick_value} — cannot calculate volume"
        )
        return 0.0

    # Dollar risk per lot for the given SL distance
    ticks_in_sl = sl_distance / tick_size
    risk_per_lot = ticks_in_sl * tick_value

    if risk_per_lot == 0:
        log.warning(
            f"[RISK] _calc_volume: risk_per_lot=0, ticks_in_sl={ticks_in_sl}, tick_value={tick_value}"
        )
        return 0.0

    volume = risk_budget / risk_per_lot

    log.info(
        f"[RISK] _calc_volume: budget=${risk_budget:.2f}, sl_dist={sl_distance:.6f}, "
        f"ticks={ticks_in_sl:.1f}, tick_val={tick_value}, risk/lot=${risk_per_lot:.2f}, raw_vol={volume:.4f}"
    )

    # Clamp to symbol constraints
    _MAX_CRYPTO_NOTIONAL = (
        5000.0  # Hard cap: max $5000 notional per crypto trade (at 1x leverage)
    )
    if is_crypto:
        vol_min = symbol_info.get("volume_min", 0.001) if symbol_info else 0.001
        vol_max = symbol_info.get("volume_max", 9999.0) if symbol_info else 9999.0
        vol_step = symbol_info.get("volume_step", 0.001) if symbol_info else 0.001
        # Notional cap: prevent oversized positions even when SL is tight
        if entry_price > 0:
            max_by_notional = _MAX_CRYPTO_NOTIONAL / entry_price
            if volume > max_by_notional:
                log.warning(
                    f"[RISK] crypto volume {volume:.4f} clamped to {max_by_notional:.4f} (${_MAX_CRYPTO_NOTIONAL} notional cap)"
                )
                volume = max_by_notional
    elif is_stock:
        vol_min = (
            symbol_info.get("volume_min", 1.0) if symbol_info else 1.0
        )  # min 1 share
        vol_max = symbol_info.get("volume_max", 5000.0) if symbol_info else 5000.0
        vol_step = (
            symbol_info.get("volume_step", 1.0) if symbol_info else 1.0
        )  # whole shares
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


def risk_check(
    signal: dict,
    account_balance: float,
    account_equity: float,
    open_positions: list,
    symbol_info: dict | None = None,
    kill_switch: bool = False,
    sizing_override: float = 1.0,
    is_manual_override: bool = False,
) -> RiskApproval:
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

    # ── Check 0a: Direction validation ──────────────────────────────────────
    direction = signal.get("direction", "").upper()
    if direction not in ("LONG", "SHORT"):
        log.warning(f"{prefix} REJECTED: invalid direction '{signal.get('direction')}'")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, 0.0, "INVALID_DIRECTION")

    asset_type = signal.get("type", "") or "unknown"

    # ── Check 0: Kill switch ────────────────────────────────────────────────
    if kill_switch:
        log.warning(f"{prefix} REJECTED: kill switch active")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, 0.0, "KILL_SWITCH_ACTIVE")

    # ── Check 0.5: Daily loss limit ────────────────────────────────────────
    daily_blocked, daily_loss_pct = _check_daily_loss(account_balance, asset_type)
    if daily_blocked:
        log.warning(
            f"{prefix} REJECTED: daily loss {daily_loss_pct:.1%} exceeds {_cfg('DAILY_LOSS_LIMIT', 0.05):.0%} limit"
        )
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, 0.0, "DAILY_LOSS_LIMIT")

    # ── Check 1: Signal freshness ───────────────────────────────────────────
    ts_str = signal.get("timestamp")
    if ts_str and not is_manual_override:
        try:
            sig_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - sig_time).total_seconds()
            if age > _cfg("SIGNAL_MAX_AGE_SEC", 300):
                log.warning(
                    f"{prefix} REJECTED: signal is {age:.0f}s old (max {_cfg('SIGNAL_MAX_AGE_SEC', 300)}s)"
                )
                return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, 0.0, "STALE_SIGNAL")
        except (ValueError, TypeError):
            log.warning(
                f"{prefix} REJECTED: could not parse signal timestamp: {ts_str}"
            )
            return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, 0.0, "UNPARSEABLE_TIMESTAMP")

    # ── Check 2: Drawdown circuit breaker ───────────────────────────────────
    dd = _current_drawdown(account_equity, asset_type)
    dd_factor = 1.0
    if dd >= _cfg("DRAWDOWN_STOP_THRESHOLD", 0.15):
        log.warning(
            f"{prefix} REJECTED: drawdown {dd:.1%} exceeds stop threshold {_cfg('DRAWDOWN_STOP_THRESHOLD', 0.15):.0%}"
        )
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "DRAWDOWN_CIRCUIT_BREAKER")
    if dd >= _cfg("DRAWDOWN_REDUCE_THRESHOLD", 0.10):
        dd_factor = 0.5
        log.info(f"{prefix} drawdown {dd:.1%} — halving position size")

    # ── Check 3: Max open positions ─────────────────────────────────────────
    if len(open_positions) >= _cfg("MAX_OPEN_POSITIONS", 5):
        log.warning(
            f"{prefix} REJECTED: {len(open_positions)} open positions (max {_cfg('MAX_OPEN_POSITIONS', 5)})"
        )
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "MAX_POSITIONS_REACHED")

    # ── Check 4: Correlation guard ──────────────────────────────────────────
    corr_count = _count_correlated(pair, open_positions)
    if corr_count >= _cfg("MAX_CORRELATED_POSITIONS", 2):
        cluster = _cluster_for_pair(pair)
        log.warning(
            f"{prefix} REJECTED: {corr_count} positions in '{cluster}' cluster (max {_cfg('MAX_CORRELATED_POSITIONS', 2)})"
        )
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "CORRELATED_CLUSTER_FULL")

    # ── Check 4b: Same-pair duplicate guard ─────────────────────────────────
    # Block opening a second position on a pair we already hold.
    # Prevents position stacking on persistent signals (DOT/USDT, USO pattern).
    _existing_same_pair = sum(1 for pos in open_positions if pos.get("pair") == pair)
    if _existing_same_pair >= 1:
        log.warning(
            f"{prefix} REJECTED: already holding {_existing_same_pair} position(s) on {pair}"
        )
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "DUPLICATE_PAIR")

    # ── Check 5: Calculate position size ────────────────────────────────────
    entry = float(signal.get("price") or signal.get("livePrice") or 0)
    sl = float(signal.get("sl") or 0)
    if not entry or not sl or entry == sl:
        log.warning(f"{prefix} REJECTED: invalid entry={entry} or sl={sl}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "INVALID_LEVELS")
    if direction == "LONG" and sl >= entry:
        log.warning(f"{prefix} REJECTED: LONG stop {sl} must be below entry {entry}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "INVALID_LEVELS")
    if direction == "SHORT" and sl <= entry:
        log.warning(f"{prefix} REJECTED: SHORT stop {sl} must be above entry {entry}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "INVALID_LEVELS")

    max_sl_pct = _cfg("MAX_SL_PCT", {}).get(asset_type, 0.05)
    if entry != 0:
        sl_pct = abs(entry - sl) / abs(entry)
        if sl_pct > max_sl_pct:
            log.warning(
                f"{prefix} REJECTED: SL distance {sl_pct:.1%} exceeds MAX_SL_PCT "
                f"{max_sl_pct:.1%} for {asset_type}"
            )
            return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "MAX_SL_EXCEEDED")

    _risk_regime = signal.get("regimeName")
    if not _risk_regime:
        _regime_payload = signal.get("regime")
        if isinstance(_regime_payload, dict):
            _risk_regime = _regime_payload.get("label") or _regime_payload.get(
                "regime"
            )
        elif isinstance(_regime_payload, str):
            _risk_regime = _regime_payload
    if not _risk_regime:
        _trend_state = signal.get("trendState", "")
        if _trend_state in (
            "TRENDING",
            "DEVELOPING",
            "RANGING",
            "DEAD RANGING",
            "HIGH_VOLATILITY",
            "LOW_VOLATILITY",
        ):
            _risk_regime = _trend_state
    volume = _calc_volume(
        account_balance,
        entry,
        sl,
        symbol_info,
        asset_type,
        pair=signal,
        regime=_risk_regime or "",
    )
    is_crypto = asset_type == "crypto"
    is_stock = asset_type == "stock"
    is_commodity = asset_type == "commodity"

    # Score-scaled sizing: scale position by signal quality (weak signals get smaller bets)
    max_score = signal.get("maxScore", 3.0)
    score = signal.get("confluenceScore", max_score)
    score_factor = max(0.25, min(1.0, score / max_score)) if max_score > 0 else 1.0
    # Also apply AI sizing override (1.0=full, 0.75=normal, 0.5=half, 0.25=quarter)
    combined_factor = dd_factor * score_factor * max(0.25, min(1.0, sizing_override))
    _decimals = 6 if is_crypto else (0 if is_stock else 2)
    volume = round(volume * combined_factor, _decimals)
    log.info(
        f"{prefix} sizing: score_factor={score_factor:.2f}, sizing_override={sizing_override:.2f}, dd_factor={dd_factor:.2f} → combined={combined_factor:.2f}"
    )

    # Re-clamp after scaling
    _default_step = 0.001 if is_crypto else (1.0 if is_stock else 0.01)
    vol_step = (
        symbol_info.get("volume_step", _default_step) if symbol_info else _default_step
    )
    volume = math.floor(volume / vol_step) * vol_step if vol_step > 0 else volume
    volume = round(volume, _decimals)

    # Stocks: ensure minimum 1 share (can't trade fractional on MT5)
    if is_stock and 0 < volume < 1:
        volume = 1.0

    if volume <= 0:
        log.warning(
            f"{prefix} REJECTED: calculated volume is 0 — raw_vol from _calc_volume scaled by combined_factor={combined_factor:.2f}, "
            f"balance={account_balance}, entry={entry}, SL={sl}, dist={abs(entry - sl):.6f}, asset={asset_type}, "
            f"symbol_info={'yes' if symbol_info else 'no'}"
        )
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "ZERO_VOLUME")

    # ── Check 6: Risk amount validation ─────────────────────────────────────
    sl_distance = abs(entry - sl)
    if is_crypto:
        _default_tick = 0.01
        _default_contract = 1
        _default_tick_val = _default_tick * _default_contract  # 0.01
    elif is_commodity:
        # Match _calc_volume fallback: tick_value = point * contract_size (e.g. 0.01 * 100 = 1.0 for gold)
        _default_tick = 0.01
        _default_contract = 100
        _default_tick_val = _default_tick * _default_contract  # 1.0
    elif is_stock:
        _default_tick = 0.01
        _default_contract = 1
        _default_tick_val = _default_tick * _default_contract  # 0.01
    else:
        _default_tick = 0.00001
        _default_tick_val = 1.0
    tick_size = (
        symbol_info.get("trade_tick_size", _default_tick)
        if symbol_info
        else _default_tick
    )
    tick_value = (
        symbol_info.get("trade_tick_value", _default_tick_val)
        if symbol_info
        else _default_tick_val
    )
    ticks_in_sl = sl_distance / tick_size if tick_size > 0 else 0
    risk_amount = ticks_in_sl * tick_value * volume

    risk_pct = risk_amount / account_balance if account_balance > 0 else 1.0

    # Hard cap: never risk more than MAX_RISK_PER_TRADE
    if risk_pct > _cfg("MAX_RISK_PER_TRADE", 0.03):
        log.warning(
            f"{prefix} REJECTED: risk {risk_pct:.1%} exceeds max {_cfg('MAX_RISK_PER_TRADE', 0.03):.0%}"
        )
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "RISK_TOO_HIGH")

    # ── Check 7: Portfolio heat ─────────────────────────────────────────────
    current_heat = _calc_portfolio_heat(open_positions, account_balance)
    new_heat = current_heat + risk_pct
    if new_heat > _cfg("MAX_PORTFOLIO_HEAT", 0.06):
        log.warning(
            f"{prefix} REJECTED: portfolio heat would be {new_heat:.1%} (max {_cfg('MAX_PORTFOLIO_HEAT', 0.06):.0%})"
        )
        return RiskApproval(False, 0.0, 0.0, 0.0, new_heat, dd, "PORTFOLIO_HEAT_EXCEEDED")

    # ── ALL CHECKS PASSED ───────────────────────────────────────────────────
    log.info(
        f"{prefix} APPROVED: {volume} lots, risk ${risk_amount:.2f} ({risk_pct:.2%}), heat {new_heat:.2%}"
    )
    return RiskApproval(
        approved=True,
        volume=volume,
        risk_amount=round(risk_amount, 2),
        risk_pct=round(risk_pct, 4),
        portfolio_heat=round(new_heat, 4),
        drawdown_pct=round(dd, 4),
        reason="OK",
    )
